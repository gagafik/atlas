"""Run CPU-heavy scheduling in a disposable spawned process, never in ASGI.

The child receives a snapshot, not a database connection. Only the parent may
create a preview/apply it. Cancels have a short cooperative grace period, then
the owned child is terminated and joined. A dead parent also ends the child.
"""
import logging
import multiprocessing as mp
import os
import threading
import time


def configured_workers():
    default = max(1, min(2, (os.cpu_count() or 2)-1))
    try: value = int(os.environ.get('ATLAS_SOLVER_WORKERS', str(default)))
    except ValueError: return default
    return max(1, min(value, 16, os.cpu_count() or 2))


def _lower_priority():
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(5)
    except (OSError, AttributeError):
        logging.warning('Unable to lower generator process priority')


def _child(document, workload_ids, seconds, options, cancelled, channel):
    from .models import State
    from .optimization import Optimization
    from .scheduling import solve
    _lower_priority()
    done = threading.Event()
    def watch_parent():
        parent = mp.parent_process()
        while not done.wait(.5):
            if parent is not None and not parent.is_alive():
                os._exit(0)  # Disposable snapshot only; never owns a DB transaction.
    threading.Thread(target=watch_parent, name='generator-owner', daemon=True).start()
    try:
        state = State.model_validate_json(document)
        if options.get('optimization') is not None:
            options['optimization'] = Optimization.model_validate(options['optimization'])
        def progress(value): channel.send(('progress', value))
        channel.send(('progress',dict(phase='building',execution='process',workers=configured_workers())))
        result = solve(state, workload_ids, seconds, cancel=cancelled, progress=progress, **options)
        channel.send(('result', result))
    except (BrokenPipeError, EOFError):
        pass
    except Exception:
        logging.exception('Isolated generation failed')
        try:
            channel.send(('result',dict(status='failed',message='Ошибка генератора. Расписание не изменено; подробности в журнале сервера.',details=[])))
        except (BrokenPipeError, EOFError): pass
    finally:
        done.set()
        channel.close()


def solve_isolated(state, workload_ids=None, seconds=15, cancel=None, progress=None,
                   cycle_ids=None, optimization=None, *, _target=None, _wall_seconds=None, _cancel_grace=3):
    """Same solver result contract; private overrides support lifecycle tests."""
    def cancelled(): return dict(status='cancelled', message='Поиск отменён. Расписание не изменено.',details=[])
    if cancel is not None and cancel.is_set(): return cancelled()
    ctx = mp.get_context('spawn')
    receiver, sender = ctx.Pipe(duplex=False)
    child_cancel = ctx.Event()
    options = dict(cycle_ids=cycle_ids)
    if optimization is not None:
        options['optimization'] = optimization.model_dump() if hasattr(optimization,'model_dump') else optimization
    process = ctx.Process(target=_target or _child, args=(state.model_dump_json(), workload_ids, seconds, options, child_cancel, sender),
                          name='atlas-generator',daemon=True)
    started = False
    try:
        process.start(); started = True
        sender.close()
        began = time.monotonic()
        deadline = began + (_wall_seconds if _wall_seconds is not None else seconds+120)
        cancelling_since = None
        result = None
        while True:
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                child_cancel.set()
                if cancelling_since is None: cancelling_since = now
                if now-cancelling_since >= _cancel_grace: return cancelled()
            if now >= deadline:
                child_cancel.set()
                return dict(status='timeout',message='Превышено общее время подготовки и поиска. Расписание не изменено.',details=[])
            if receiver.poll(.1):
                try: kind, value = receiver.recv()
                except EOFError: break
                if kind == 'progress' and progress is not None: progress(value)
                elif kind == 'result': result = value; break
            elif not process.is_alive(): break
        if cancel is not None and cancel.is_set(): return cancelled()
        if result is not None: return result
        logging.error('Generator process exited without a result: exit code %s', process.exitcode)
        return dict(status='failed',message='Процесс генератора завершился без результата. Расписание не изменено; поиск можно повторить.',details=[])
    finally:
        child_cancel.set()
        sender.close(); receiver.close()
        if started:
            process.join(timeout=.5)
            if process.is_alive():
                process.terminate(); process.join(timeout=3)
            if process.is_alive():
                process.kill(); process.join(timeout=3)
            if process.is_alive(): raise RuntimeError('Generator child could not be stopped')
            process.close()
