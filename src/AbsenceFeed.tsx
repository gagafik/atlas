import { useEffect, useState } from "react";
import { api } from "./api";
import type { TeacherAbsence } from "./types";

interface Pending {
  leaveId: string;
  teacherId: string;
  teacher: string;
  start: string;
  end: string;
  startTime: string;
  endTime: string;
  lessons: number;
  absence: TeacherAbsence;
}
interface Withdrawn {
  leaveId: string;
  absenceId: string;
  teacherId: string;
  teacher: string;
  start: string;
  end: string;
}
interface Uncovered {
  absenceId: string;
  teacherId: string;
  teacher: string;
  days: { date: string; lessons: number }[];
}
interface Feed {
  revision: number;
  pending: Pending[];
  withdrawn: Withdrawn[];
  uncovered: Uncovered[];
}

const shortDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;

// Approved staff leave that the timetable does not know yet, absences left after a cancelled leave and
// lessons of absent teachers that still have no substitute. The schedule is changed only by the buttons.
export default function AbsenceFeed({
  workspace,
  revision,
  busy,
  onAdd,
  onRemove,
  onPlan,
}: {
  workspace: string;
  revision: number;
  busy: boolean;
  onAdd: (absence: TeacherAbsence) => void;
  onRemove: (absenceId: string) => void;
  onPlan: (day: string, teacherId: string) => void;
}) {
  const [feed, setFeed] = useState<Feed | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api("calendar/absence-feed", workspace)
      .then((r) => {
        if (live) {
          setFeed(r);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, revision]);
  const current = feed && feed.revision === revision ? feed : null;
  return (
    <>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {!!current?.pending.length && (
        <>
          <h3>Согласованные отсутствия не внесены</h3>
          {current.pending.map((p) => (
            <article key={p.absence.id}>
              <div>
                <strong>{p.teacher}</strong>
                <p>
                  {p.start} — {p.end} · каждый день {p.startTime}–{p.endTime} ·
                  уроков: {p.lessons}
                </p>
              </div>
              <button disabled={busy} onClick={() => onAdd(p.absence)}>
                Внести в расписание
              </button>
            </article>
          ))}
        </>
      )}
      {!!current?.withdrawn.length && (
        <>
          <h3>Заявка отменена, отсутствие осталось</h3>
          {current.withdrawn.map((w) => (
            <article key={w.absenceId}>
              <div>
                <strong>{w.teacher}</strong>
                <p>
                  {w.start} — {w.end}
                </p>
              </div>
              <button disabled={busy} onClick={() => onRemove(w.absenceId)}>
                Убрать из расписания
              </button>
            </article>
          ))}
        </>
      )}
      {!!current?.uncovered.length && (
        <>
          <h3>Уроки без замены</h3>
          {current.uncovered.map((u) => (
            <article key={u.absenceId}>
              <div>
                <strong>{u.teacher}</strong>
              </div>
              {u.days.map((d) => (
                <button
                  key={d.date}
                  disabled={busy}
                  onClick={() => onPlan(d.date, u.teacherId)}
                >
                  Замены на {shortDate(d.date)} ({d.lessons})
                </button>
              ))}
            </article>
          ))}
        </>
      )}
    </>
  );
}
