import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { requestId } from "./dutySchedule";
import type { JournalEntry } from "./journal";
import {
  inClasses,
  orderTargets,
  parallelBody,
  targetDue,
  type ParallelTarget,
} from "./homeworkBulk";

interface Listing {
  revision: number;
  source: { lessonKey: string; day: string; nextLessonDate: string | null };
  targets: ParallelTarget[];
}

/** Gives the lesson's published homework to the same subject in other classes of the teacher, a due date per class. */
export default function HomeworkParallelPanel({
  workspace,
  entry,
  disabled,
  onPublished,
}: {
  workspace: string;
  entry: JournalEntry;
  disabled: boolean;
  onPublished: () => void;
}) {
  const [open, setOpen] = useState(false),
    [listing, setListing] = useState<Listing | null>(null),
    [mode, setMode] = useState<"same" | "next">("same"),
    [chosen, setChosen] = useState<Record<string, boolean>>({}),
    [dues, setDues] = useState<Record<string, string>>({}),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [stale, setStale] = useState(false),
    [error, setError] = useState(""),
    [note, setNote] = useState("");
  const slot = useRef({ key: "", id: "" }),
    lock = useRef(false);
  const sourceDue = entry.document.content.dueDate;
  const day = entry.document.lesson.date;

  function dueDates(next: "same" | "next", targets: ParallelTarget[]) {
    return Object.fromEntries(
      targets
        .filter((t) => t.lessonKey)
        .map((t) => [t.lessonKey!, targetDue(next, sourceDue, t) ?? ""]),
    );
  }
  async function load() {
    setLoading(true);
    setError("");
    setStale(false);
    try {
      const r: Listing = await api(
        `journal/homework-parallel?${new URLSearchParams({ lessonKey: entry.lessonKey, day })}`,
        workspace,
      );
      setListing(r);
      setChosen({});
      setDues(dueDates(mode, r.targets));
    } catch (e: any) {
      setListing(null);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (open) void load();
    // The list belongs to this lesson and its saved version (the parent remounts the panel on a new one).
  }, [open]);

  function changeMode(next: "same" | "next") {
    setMode(next);
    if (listing) setDues(dueDates(next, listing.targets));
  }
  const ordered = listing ? orderTargets(listing.targets) : [];
  const picked = ordered.filter(
    (t) => t.available && t.lessonKey && chosen[t.lessonKey],
  );
  async function publish() {
    if (!listing || lock.current || !picked.length) return;
    if (
      !window.confirm(
        `Опубликовать это задание в ${inClasses(picked.length)}: ${picked.map((t) => t.group).join(", ")}? Ученики и родители этих классов получат уведомление.`,
      )
    )
      return;
    const body = parallelBody(
      { lessonKey: entry.lessonKey, day, version: entry.version },
      listing.revision,
      picked.map((t) => ({
        lessonKey: t.lessonKey!,
        day: t.day!,
        version: t.version,
        dueDate: dues[t.lessonKey!] || null,
      })),
    );
    lock.current = true;
    setBusy(true);
    setError("");
    setStale(false);
    setNote("");
    try {
      const r = await api("journal/homework-parallel", workspace, "POST", {
        requestId: requestId(slot, body),
        ...body,
      });
      setNote(
        `Задание опубликовано: ${r.published.map((p: { group: string }) => p.group).join(", ")}.`,
      );
      await load();
      onPublished();
    } catch (e: any) {
      setError(e.message);
      setStale(e.status === 409);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function row(t: ParallelTarget) {
    const key = t.lessonKey || t.groupId;
    return (
      <li key={t.groupId}>
        <input
          type="checkbox"
          id={`parallel-${key}`}
          checked={!!(t.lessonKey && chosen[t.lessonKey])}
          disabled={!t.available || busy || disabled}
          onChange={(e) =>
            setChosen((c) => ({ ...c, [t.lessonKey!]: e.target.checked }))
          }
        />
        <label htmlFor={`parallel-${key}`}>
          {t.group}
          {t.day ? ` · ${t.day} ${t.start}–${t.end}` : ""}
          {t.teachers ? ` · ${t.teachers}` : ""}
        </label>
        {t.available && t.lessonKey ? (
          <>
            <input
              type="date"
              aria-label={`Срок для ${t.group}`}
              value={dues[t.lessonKey] || ""}
              disabled={busy || disabled}
              onChange={(e) =>
                setDues((d) => ({ ...d, [t.lessonKey!]: e.target.value }))
              }
            />
            <small>Следующий урок: {t.nextLessonDate || "не найден"}</small>
          </>
        ) : (
          <small>{t.reason}</small>
        )}
      </li>
    );
  }
  const near = ordered.filter((t) => t.parallel),
    others = ordered.filter((t) => !t.parallel);
  return (
    <section className="homework-parallel">
      {!open && (
        <button disabled={disabled} onClick={() => setOpen(true)}>
          Задать это задание в других классах
        </button>
      )}
      {disabled && (
        <p role="status">
          Сохраните и опубликуйте задание, чтобы задать его в других классах.
        </p>
      )}
      {open && (
        <>
          <h3>Задание в других классах</h3>
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {stale && (
            <button disabled={loading || busy} onClick={() => void load()}>
              Обновить список классов
            </button>
          )}
          {note && <p role="status">{note}</p>}
          {listing && !listing.targets.length && (
            <p>Других классов с этим предметом у вас нет.</p>
          )}
          {!!listing?.targets.length && (
            <>
              <div
                role="radiogroup"
                aria-label="Срок сдачи в других классах"
                className="homework-parallel-mode"
              >
                <label className="checkbox-label">
                  <input
                    type="radio"
                    name={`parallel-due-${entry.lessonKey}`}
                    checked={mode === "same"}
                    disabled={busy || disabled}
                    onChange={() => changeMode("same")}
                  />
                  Срок как в этом уроке ({sourceDue || "без срока"})
                </label>
                <label className="checkbox-label">
                  <input
                    type="radio"
                    name={`parallel-due-${entry.lessonKey}`}
                    checked={mode === "next"}
                    disabled={busy || disabled}
                    onChange={() => changeMode("next")}
                  />
                  К следующему уроку каждого класса
                </label>
              </div>
              {!!near.length && (
                <ul className="homework-parallel-list">{near.map(row)}</ul>
              )}
              {!!others.length && (
                <details open={!near.length}>
                  <summary>Другие классы этого предмета</summary>
                  <ul className="homework-parallel-list">{others.map(row)}</ul>
                </details>
              )}
              <button
                className="primary"
                disabled={!picked.length || busy || disabled || loading}
                onClick={() => void publish()}
              >
                Опубликовать в {inClasses(picked.length)}
              </button>
            </>
          )}
          {loading && !listing && <p>Загрузка классов…</p>}
        </>
      )}
    </section>
  );
}
