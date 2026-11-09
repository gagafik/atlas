import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { requestId } from "./dutySchedule";
import { BRIEFING_PRINT, printNode } from "./printReport";
import {
  HISTORY_NAMES,
  dayLabel,
  stampLabel,
  type BriefingDetail,
  type BriefingItem,
  type OverduePerson,
  type SafetyOptions,
} from "./safety";
import "./Safety.css";

const TABS = [
  ["journal", "Журнал инструктажей"],
  ["new", "Новый инструктаж"],
  ["overdue", "Кто не прошёл"],
] as const;
type Tab = (typeof TABS)[number][0];
type Person = { id: string; name: string; className: string };

const monthsAgo = (iso: string, months: number) => {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 10);
};

// One form for a new briefing and for editing a recorded one; the audience of a recorded briefing stays fixed.
function BriefingForm({
  workspace,
  options,
  initial,
  onSaved,
  onDirty,
  onFail,
}: {
  workspace: string;
  options: SafetyOptions;
  initial?: BriefingDetail;
  onSaved: (saved: BriefingDetail) => void;
  onDirty: (dirty: boolean) => void;
  onFail: (message: string, status?: number) => void;
}) {
  const [kind, setKind] = useState(initial?.kind ?? options.kinds[0]?.id ?? ""),
    [topic, setTopic] = useState(initial?.topic ?? ""),
    [heldOn, setHeldOn] = useState(initial?.heldOn ?? options.today),
    [conductorId, setConductorId] = useState(
      initial ? (initial.conductorTeacherId ?? "") : "",
    ),
    [conductorName, setConductorName] = useState(
      initial && !initial.conductorTeacherId ? initial.conductor : "",
    ),
    [note, setNote] = useState(initial?.note ?? "");
  const [audience, setAudience] = useState<"class" | "staff">(
    initial?.audience ?? "class",
  );
  const [classId, setClassId] = useState(initial?.classId ?? ""),
    [roster, setRoster] = useState<Person[]>([]),
    [label, setLabel] = useState(""),
    [chosen, setChosen] = useState<Set<string>>(new Set()),
    [filter, setFilter] = useState("");
  const [absent, setAbsent] = useState<Set<string>>(
    new Set(initial?.people.filter((p) => !p.present).map((p) => p.id)),
  );
  const [busy, setBusy] = useState(false);
  const slot = useRef({ key: "", id: "" });
  const edit = !!initial;
  useEffect(() => {
    if (edit || audience !== "class" || !classId || !heldOn) {
      if (!edit) setRoster([]);
      return;
    }
    let live = true;
    api(
      `safety/roster?classId=${encodeURIComponent(classId)}&day=${heldOn}`,
      workspace,
    )
      .then((list: { id: string; name: string }[]) => {
        if (!live) return;
        setRoster(list.map((p) => ({ ...p, className: "" })));
        // Ticks stay for the pupils who are still in the class on the new date.
        setAbsent(
          (old) =>
            new Set([...old].filter((id) => list.some((p) => p.id === id))),
        );
      })
      .catch((e) => {
        if (live) {
          setRoster([]);
          onFail(e.message, e.status);
        }
      });
    return () => {
      live = false;
    };
  }, [edit, audience, classId, heldOn, workspace]);
  const people: Person[] = edit
    ? initial!.people.map((p) => ({
        id: p.id,
        name: p.name,
        className: p.className,
      }))
    : audience === "class"
      ? roster
      : options.teachers
          .filter((t) => chosen.has(t.id))
          .map((t) => ({ ...t, className: "" }));
  const touch =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      onDirty(true);
    };
  const flip = (set: Set<string>, id: string) => {
    const next = new Set(set);
    if (!next.delete(id)) next.add(id);
    return next;
  };
  const visible = options.teachers.filter((t) =>
    t.name.toLowerCase().includes(filter.trim().toLowerCase()),
  );
  const empty = !edit && audience === "class" && !!classId && !roster.length;
  const ready =
    !!topic.trim() &&
    !!heldOn &&
    (conductorId || conductorName.trim()) &&
    (edit ||
      (audience === "class"
        ? !!classId && !empty
        : chosen.size && label.trim()));
  const save = () => {
    const common = {
      kind,
      heldOn,
      topic: topic.trim(),
      conductorTeacherId: conductorId || null,
      conductorName: conductorId ? "" : conductorName.trim(),
      note,
      absentIds: people.filter((p) => absent.has(p.id)).map((p) => p.id),
    };
    const body = edit
      ? { ...common, version: initial!.version }
      : audience === "class"
        ? { ...common, audience, classId, teacherIds: [] }
        : {
            ...common,
            audience,
            staffLabel: label.trim(),
            teacherIds: [...chosen],
          };
    setBusy(true);
    api(
      edit ? `safety/briefings/${initial!.id}` : "safety/briefings",
      workspace,
      edit ? "PUT" : "POST",
      { ...body, requestId: requestId(slot, body) },
    )
      .then((saved: BriefingDetail) => {
        onDirty(false);
        onSaved(saved);
      })
      .catch((e) => onFail(e.message, e.status))
      .finally(() => setBusy(false));
  };
  return (
    <form
      className="sf-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) save();
      }}
    >
      <label>
        Вид
        <select value={kind} onChange={(e) => touch(setKind)(e.target.value)}>
          {options.kinds.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Дата
        <input
          type="date"
          value={heldOn}
          min={options.yearStart}
          max={options.today}
          onChange={(e) => touch(setHeldOn)(e.target.value)}
        />
      </label>
      <label className="sf-full">
        Тема
        <input
          value={topic}
          maxLength={200}
          onChange={(e) => touch(setTopic)(e.target.value)}
        />
      </label>
      <label>
        Провёл
        <select
          value={conductorId}
          onChange={(e) => touch(setConductorId)(e.target.value)}
        >
          <option value="">Другой человек</option>
          {options.teachers.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      {!conductorId && (
        <label>
          Фамилия, имя, должность
          <input
            value={conductorName}
            maxLength={160}
            onChange={(e) => touch(setConductorName)(e.target.value)}
          />
        </label>
      )}
      {!edit && (
        <label>
          Для кого
          <select
            value={audience}
            onChange={(e) => {
              touch(setAudience)(e.target.value as "class" | "staff");
              setAbsent(new Set());
            }}
          >
            <option value="class">Класс</option>
            <option value="staff">Сотрудники</option>
          </select>
        </label>
      )}
      {!edit && audience === "class" && (
        <label>
          Класс
          <select
            value={classId}
            onChange={(e) => touch(setClassId)(e.target.value)}
          >
            <option value="">Выберите класс</option>
            {options.classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {!edit && audience === "staff" && (
        <>
          <label>
            Название группы
            <input
              value={label}
              maxLength={120}
              onChange={(e) => touch(setLabel)(e.target.value)}
            />
          </label>
          <div className="sf-full">
            <div className="sf-controls">
              <label>
                Найти сотрудника
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
              </label>
              <button
                type="button"
                onClick={() => {
                  touch(setChosen)(
                    new Set([...chosen, ...visible.map((t) => t.id)]),
                  );
                }}
              >
                Выбрать всех
              </button>
              <button type="button" onClick={() => touch(setChosen)(new Set())}>
                Снять выбор
              </button>
            </div>
            <div className="sf-roster">
              {visible.map((t) => (
                <label key={t.id} className="sf-check">
                  <input
                    type="checkbox"
                    checked={chosen.has(t.id)}
                    onChange={() => touch(setChosen)(flip(chosen, t.id))}
                  />
                  <span>{t.name}</span>
                </label>
              ))}
            </div>
          </div>
        </>
      )}
      {empty && (
        <p className="sf-full sf-error" role="alert">
          В классе на эту дату нет учеников
        </p>
      )}
      {!!people.length && (
        <div className="sf-full">
          <h4>Присутствовали</h4>
          <div className="sf-roster">
            {people.map((p) => (
              <label key={p.id} className="sf-check">
                <input
                  type="checkbox"
                  checked={!absent.has(p.id)}
                  onChange={() => touch(setAbsent)(flip(absent, p.id))}
                />
                <span>
                  {p.name}
                  {p.className && (
                    <small className="sf-muted"> · {p.className}</small>
                  )}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}
      <label className="sf-full">
        Примечание
        <textarea
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(e) => touch(setNote)(e.target.value)}
        />
      </label>
      <div className="sf-actions sf-full">
        <button type="submit" disabled={!ready || busy}>
          {edit ? "Сохранить изменения" : "Записать инструктаж"}
        </button>
      </div>
    </form>
  );
}

export default function SafetyBriefings({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const spaces = user.role === "admin" ? ["school", "demo"] : ["school"];
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace) ? initialWorkspace : spaces[0],
  );
  const [options, setOptions] = useState<SafetyOptions | null>(null),
    [tab, setTab] = useState<Tab>("journal");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    dirty = useRef(false);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    let live = true;
    setOptions(null);
    setError("");
    api("safety/options", workspace)
      .then((o: SafetyOptions) => {
        if (live) setOptions(o);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  const leave = () => {
    if (!dirty.current || window.confirm("Закрыть без сохранения?")) onClose();
  };
  const go = (next: Tab) => {
    if (next === tab) return;
    if (dirty.current && !window.confirm("Перейти без сохранения?")) return;
    dirty.current = false;
    setNotice("");
    setTab(next);
  };
  return (
    <dialog
      ref={dialog}
      className="sf-dialog"
      aria-labelledby="safety-briefings-title"
      onCancel={(e) => {
        e.preventDefault();
        leave();
      }}
    >
      <header>
        <div>
          <h2 id="safety-briefings-title">
            Инструктажи по технике безопасности
          </h2>
          <p>Журнал регистрации, отметки о присутствии и список не прошедших</p>
        </div>
        <button onClick={leave}>Закрыть</button>
      </header>
      {error && (
        <p role="alert" className="sf-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="sf-notice">
          {notice}
        </p>
      )}
      {spaces.length > 1 && (
        <div className="sf-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => {
                dirty.current = false;
                setWorkspace(e.target.value);
              }}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <div className="sf-tabs">
        {TABS.map(([id, name]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => go(id)}>
            {name}
          </button>
        ))}
      </div>
      {options && tab === "journal" && (
        <Journal
          key={workspace}
          workspace={workspace}
          options={options}
          onFail={(m) => setError(m)}
          onNotice={setNotice}
          onDirty={(d) => (dirty.current = d)}
        />
      )}
      {options && tab === "new" && (
        <BriefingForm
          key={workspace}
          workspace={workspace}
          options={options}
          onDirty={(d) => (dirty.current = d)}
          onFail={(m) => setError(m)}
          onSaved={() => {
            setError("");
            setNotice("Инструктаж записан");
            setTab("journal");
          }}
        />
      )}
      {options && tab === "overdue" && (
        <Overdue
          key={workspace}
          workspace={workspace}
          options={options}
          onFail={(m) => setError(m)}
        />
      )}
    </dialog>
  );
}

function Journal({
  workspace,
  options,
  onFail,
  onNotice,
  onDirty,
}: {
  workspace: string;
  options: SafetyOptions;
  onFail: (message: string) => void;
  onNotice: (message: string) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const [start, setStart] = useState(options.yearStart),
    [end, setEnd] = useState(options.today),
    [kind, setKind] = useState(""),
    [classId, setClassId] = useState("");
  const [items, setItems] = useState<BriefingItem[]>([]),
    [next, setNext] = useState<number | null>(null),
    [loading, setLoading] = useState(true),
    [reload, setReload] = useState(0);
  const [open, setOpen] = useState<BriefingDetail | null>(null),
    [editing, setEditing] = useState(false),
    [reason, setReason] = useState<string | null>(null);
  const printable = useRef<HTMLDivElement>(null),
    slot = useRef({ key: "", id: "" });
  const query = () => {
    const p = new URLSearchParams();
    if (start) p.set("start", start);
    if (end) p.set("end", end);
    if (kind) p.set("kind", kind);
    if (classId) p.set("classId", classId);
    return p;
  };
  useEffect(() => {
    let live = true;
    setLoading(true);
    api("safety/briefings?" + query(), workspace)
      .then((page: { items: BriefingItem[]; nextBefore: number | null }) => {
        if (!live) return;
        setItems(page.items);
        setNext(page.nextBefore);
        onFail("");
      })
      .catch((e) => {
        if (!live) return;
        setItems([]);
        setNext(null);
        onFail(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [start, end, kind, classId, workspace, reload]);
  const more = () => {
    const p = query();
    p.set("before", String(next));
    api("safety/briefings?" + p, workspace)
      .then((page: { items: BriefingItem[]; nextBefore: number | null }) => {
        setItems((old) => [...old, ...page.items]);
        setNext(page.nextBefore);
      })
      .catch((e) => onFail(e.message));
  };
  const show = (id: string) => {
    setEditing(false);
    setReason(null);
    return api(`safety/briefings/${id}`, workspace)
      .then((d: BriefingDetail) => setOpen(d))
      .catch((e) => onFail(e.message));
  };
  const fail = (message: string, status?: number) => {
    onFail(message);
    if (status === 409 && open) show(open.id);
  };
  const withdraw = () => {
    if (!open || !reason || reason.trim().length < 3) return;
    const body = { version: open.version, reason: reason.trim() };
    api(`safety/briefings/${open.id}/withdraw`, workspace, "POST", {
      ...body,
      requestId: requestId(slot, body),
    })
      .then((d: BriefingDetail) => {
        setOpen(d);
        setReason(null);
        onFail("");
        onNotice("Инструктаж отозван");
        setReload((n) => n + 1);
      })
      .catch((e) => fail(e.message, e.status));
  };
  const print = () => {
    if (!open) return;
    if (
      !printNode(
        printable.current,
        "Инструктаж " + dayLabel(open.heldOn),
        BRIEFING_PRINT,
      )
    )
      onNotice("Браузер не открыл окно печати");
  };
  const kindName = (id: string) =>
    options.kinds.find((k) => k.id === id)?.label ?? id;
  return (
    <section>
      <div className="sf-controls">
        <label>
          С
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          По
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <label>
          Вид
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Все</option>
            {options.kinds.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Класс
          <select value={classId} onChange={(e) => setClassId(e.target.value)}>
            <option value="">Все</option>
            {options.classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!loading && !items.length && <p>Инструктажей за период нет</p>}
      {items.map((b) => (
        <button
          key={b.id}
          className="sf-row"
          aria-expanded={open?.id === b.id}
          onClick={() => show(b.id)}
        >
          <span>
            <strong>{dayLabel(b.heldOn)}</strong> · {kindName(b.kind)} ·{" "}
            {b.topic}
            <br />
            <small className="sf-muted">
              {b.audienceLabel} · провёл {b.conductor}
            </small>
          </span>
          <span>
            присутствовали {b.present} из {b.present + b.absent}
          </span>
        </button>
      ))}
      {next !== null && <button onClick={more}>Показать ещё</button>}
      {open && (
        <section aria-label="Инструктаж" className="sf-class">
          <div ref={printable}>
            <h3 className="sf-printonly">Журнал регистрации инструктажа</h3>
            <h3>
              {kindName(open.kind)} инструктаж · {dayLabel(open.heldOn)}
              {open.status === "withdrawn" && " (отозван)"}
            </h3>
            <p>
              Тема: {open.topic}
              <br />
              Для кого: {open.audienceLabel}
              <br />
              Провёл: {open.conductor}
              {open.note && (
                <>
                  <br />
                  Примечание: {open.note}
                </>
              )}
            </p>
            <div className="sf-wide">
              <table className="sf-table">
                <thead>
                  <tr>
                    <th>№</th>
                    <th>ФИО</th>
                    <th>Класс</th>
                    <th>Отметка</th>
                    <th className="sf-sign">Подпись</th>
                  </tr>
                </thead>
                <tbody>
                  {open.people.map((p, i) => (
                    <tr key={p.id}>
                      <td data-label="№">{i + 1}</td>
                      <td data-label="ФИО">{p.name}</td>
                      <td data-label="Класс">{p.className || "—"}</td>
                      <td data-label="Отметка">
                        {p.present ? "присутствовал" : "отсутствовал"}
                      </td>
                      <td className="sf-sign" />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {open.status === "active" && !editing && (
            <div className="sf-actions">
              <button onClick={() => setEditing(true)}>Изменить</button>
              <button onClick={() => setReason("")}>Отозвать</button>
              <button onClick={print}>Печать</button>
            </div>
          )}
          {open.status === "withdrawn" && (
            <div className="sf-actions">
              <button onClick={print}>Печать</button>
            </div>
          )}
          {reason !== null && (
            <div className="sf-controls">
              <label>
                Причина отзыва
                <input
                  value={reason}
                  maxLength={500}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button onClick={withdraw} disabled={reason.trim().length < 3}>
                Отозвать инструктаж
              </button>
              <button onClick={() => setReason(null)}>Не отзывать</button>
            </div>
          )}
          {editing && (
            <BriefingForm
              workspace={workspace}
              options={options}
              initial={open}
              onDirty={onDirty}
              onFail={fail}
              onSaved={(saved) => {
                setOpen(saved);
                setEditing(false);
                onFail("");
                onNotice("Изменения сохранены");
                setReload((n) => n + 1);
              }}
            />
          )}
          <h4>История</h4>
          <ul className="sf-list">
            {open.history.map((h, i) => (
              <li key={i}>
                {stampLabel(h.created)} · {HISTORY_NAMES[h.action] ?? h.action}{" "}
                · {h.actor}
                {h.reason && ` · ${h.reason}`}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

function Overdue({
  workspace,
  options,
  onFail,
}: {
  workspace: string;
  options: SafetyOptions;
  onFail: (message: string) => void;
}) {
  const [audience, setAudience] = useState<"pupils" | "staff">("pupils"),
    [classId, setClassId] = useState(""),
    [start, setStart] = useState(() => {
      const back = monthsAgo(options.today, 6);
      return back < options.yearStart ? options.yearStart : back;
    }),
    [end, setEnd] = useState(options.today);
  const [people, setPeople] = useState<OverduePerson[] | null>(null);
  const printable = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // An empty or reversed period has nothing to list: drop the old list and send nothing.
    if (!start || !end || end < start) {
      setPeople(null);
      return;
    }
    let live = true;
    const p = new URLSearchParams({ audience, start, end });
    if (audience === "pupils" && classId) p.set("classId", classId);
    api("safety/overdue?" + p, workspace)
      .then((r: { people: OverduePerson[] }) => {
        if (!live) return;
        setPeople(r.people);
        onFail("");
      })
      .catch((e) => {
        if (!live) return;
        setPeople(null);
        onFail(e.message);
      });
    return () => {
      live = false;
    };
  }, [audience, classId, start, end, workspace]);
  return (
    <section>
      <div className="sf-controls">
        <label>
          Кто
          <select
            value={audience}
            onChange={(e) => setAudience(e.target.value as "pupils" | "staff")}
          >
            <option value="pupils">Ученики</option>
            <option value="staff">Сотрудники</option>
          </select>
        </label>
        {audience === "pupils" && (
          <label>
            Класс
            <select
              value={classId}
              onChange={(e) => setClassId(e.target.value)}
            >
              <option value="">Все</option>
              {options.classes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          С
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          По
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <div className="sf-actions">
          <button
            disabled={!people?.length}
            onClick={() =>
              printNode(
                printable.current,
                "Не прошли инструктаж",
                BRIEFING_PRINT,
              )
            }
          >
            Печать
          </button>
        </div>
      </div>
      <p className="sf-muted">
        Учитываются первичный и повторный инструктажи, на которых человек
        присутствовал
      </p>
      {people && !people.length && <p>Все прошли инструктаж за период</p>}
      {!!people?.length && (
        <div ref={printable}>
          <h3 className="sf-printonly">
            Не прошли инструктаж: {dayLabel(start)} – {dayLabel(end)}
          </h3>
          <div className="sf-wide">
            <table className="sf-table">
              <thead>
                <tr>
                  <th>ФИО</th>
                  <th>Класс</th>
                  <th>Последний повторный или первичный</th>
                </tr>
              </thead>
              <tbody>
                {people.map((p) => (
                  <tr key={p.id}>
                    <td data-label="ФИО">{p.name}</td>
                    <td data-label="Класс">{p.className || "—"}</td>
                    <td data-label="Последний">
                      {p.last ? dayLabel(p.last) : "нет"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
