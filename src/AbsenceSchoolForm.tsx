import { useState } from "react";
import { api } from "./api";
import {
  type AbsenceCode,
  type PreviewLesson,
  type SchoolPupil,
  type Subject,
  lessonEdge,
  schoolSubmitLabel,
} from "./absenceForm";

type Entry = {
  studentId: string;
  studentName: string;
  classId: string;
  className: string;
  lessonCount: number;
  lessons: PreviewLesson[];
  problem: string | null;
};
type Preview = {
  id?: string;
  status: "preview" | "blocked";
  expires?: string;
  items: Entry[];
};
type Result = {
  count: number;
  items: { id: string; studentId: string; studentName: string }[];
};

const MAX = 50;

// A note entered by the school: one pupil or a team from several classes; accepted at once after a per-pupil preview.
export default function AbsenceSchoolForm({
  workspace,
  classes,
  pupils,
  codes,
  subjects,
  yearStart,
  yearEnd,
  defaultDate,
  onDone,
}: {
  workspace: string;
  classes: { id: string; name: string }[];
  pupils: SchoolPupil[];
  codes: AbsenceCode[];
  subjects: Subject[];
  yearStart: string;
  yearEnd: string;
  defaultDate: string;
  onDone: () => void;
}) {
  const [classId, setClassId] = useState(""),
    [picked, setPicked] = useState<string[]>([]);
  const [start, setStart] = useState(defaultDate),
    [end, setEnd] = useState(defaultDate),
    [from, setFrom] = useState("00:00"),
    [to, setTo] = useState("23:59");
  const [codeId, setCodeId] = useState(""),
    [subjectId, setSubjectId] = useState(""),
    [reason, setReason] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID()),
    [preview, setPreview] = useState<Preview | null>(null),
    [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const shown = classId || classes[0]?.id || "";
  const inClass = pupils.filter((p) => p.classId === shown);
  const name = (id: string) => pupils.find((p) => p.id === id)?.name ?? id;
  const className = (id: string) =>
    classes.find((c) => c.id === id)?.name ?? "";
  // Any change makes the checked preview stale and the next request a different one.
  const touch = () => {
    setPreview(null);
    setResult(null);
    setRequestId(crypto.randomUUID());
  };
  const choose = (ids: string[]) => {
    setPicked(ids.slice(0, MAX));
    touch();
  };
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const finish = (r: Result) => {
    setResult(r);
    setPreview(null);
    setPicked([]);
    setReason("");
    setRequestId(crypto.randomUUID());
    onDone();
  };
  const prepare = () =>
    run(async () => {
      const r = await api(
        "journal/absences/school/preview",
        workspace,
        "POST",
        {
          requestId,
          studentIds: picked,
          startDate: start,
          endDate: end,
          startTime: from,
          endTime: to,
          reason,
          codeId,
          subjectId,
        },
      );
      if (r.status === "applied") finish(r);
      else setPreview(r);
    });
  const apply = () =>
    run(async () => {
      if (
        !preview?.id ||
        !window.confirm(
          `Внести записки: ${preview.items.length}. Они будут приняты сразу, семьи получат уведомление. Записки сохранятся все или ни одной.`,
        )
      )
        return;
      finish(
        await api(
          `journal/absences/school/${preview.id}/apply`,
          workspace,
          "POST",
        ),
      );
    });
  const edge = (lesson: PreviewLesson, which: "start" | "end") => {
    const { date, time } = lessonEdge(lesson, which);
    if (which === "start") {
      setStart(date);
      setFrom(time);
      if (end < date) setEnd(date);
    } else {
      setEnd(date);
      setTo(time);
      if (start > date) setStart(date);
    }
    touch();
  };
  return (
    <section className="absence-card" aria-label="Записка от имени школы">
      <h3>Внести записку от имени школы</h3>
      <p>
        Записка принимается сразу: отсутствие на полностью включённых в период
        уроках станет уважительным. Семья получит уведомление и увидит тип,
        период и предмет; причину и файлы видят только законные представители с
        подтверждённой связью, школа и ответственные. Можно выбрать до {MAX}{" "}
        учеников из разных классов, например команду олимпиады.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <fieldset disabled={busy} className="access-fields">
        <label className="field">
          <span>Класс</span>
          <select value={shown} onChange={(e) => setClassId(e.target.value)}>
            {classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <div className="dated-toolbar">
          <button
            type="button"
            disabled={!inClass.length}
            onClick={() =>
              choose([
                ...picked,
                ...inClass
                  .map((p) => p.id)
                  .filter((id) => !picked.includes(id)),
              ])
            }
          >
            Отметить весь класс
          </button>
          <button
            type="button"
            disabled={!picked.length}
            onClick={() => choose([])}
          >
            Снять все отметки
          </button>
        </div>
        <div className="absence-pupils">
          {inClass.map((p) => (
            <label className="absence-file" key={p.id}>
              <input
                type="checkbox"
                checked={picked.includes(p.id)}
                disabled={!picked.includes(p.id) && picked.length >= MAX}
                onChange={(e) =>
                  choose(
                    e.target.checked
                      ? [...picked, p.id]
                      : picked.filter((id) => id !== p.id),
                  )
                }
              />
              {p.name}
            </label>
          ))}
          {!inClass.length && <p>В этом классе нет действующих учеников.</p>}
        </div>
        <p>
          Выбрано: {picked.length} из {MAX}.
        </p>
        <div className="absence-file">
          {picked.map((id) => (
            <button
              type="button"
              key={id}
              aria-label={`Убрать из списка: ${name(id)}`}
              onClick={() => choose(picked.filter((x) => x !== id))}
            >
              {name(id)} ×
            </button>
          ))}
        </div>
        <div className="dated-fields">
          <label className="field">
            <span>Первый день</span>
            <input
              type="date"
              required
              min={yearStart}
              max={yearEnd}
              value={start}
              onChange={(e) => {
                setStart(e.target.value);
                touch();
              }}
            />
          </label>
          <label className="field">
            <span>С какого времени</span>
            <input
              type="time"
              required
              value={from}
              onChange={(e) => {
                setFrom(e.target.value);
                touch();
              }}
            />
          </label>
          <label className="field">
            <span>Последний день</span>
            <input
              type="date"
              required
              min={start}
              max={yearEnd}
              value={end}
              onChange={(e) => {
                setEnd(e.target.value);
                touch();
              }}
            />
          </label>
          <label className="field">
            <span>До какого времени</span>
            <input
              type="time"
              required
              value={to}
              onChange={(e) => {
                setTo(e.target.value);
                touch();
              }}
            />
          </label>
        </div>
        <label className="field">
          <span>Тип отсутствия</span>
          <select
            required
            value={codeId}
            onChange={(e) => {
              setCodeId(e.target.value);
              touch();
            }}
          >
            <option value="">Выберите тип</option>
            {codes
              .filter((c) => c.active)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          <span>Предмет</span>
          <select
            value={subjectId}
            onChange={(e) => {
              setSubjectId(e.target.value);
              touch();
            }}
          >
            <option value="">Все предметы</option>
            {subjects.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Причина</span>
          <textarea
            maxLength={4000}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              touch();
            }}
          />
        </label>
        <div className="dated-toolbar">
          <button
            disabled={
              !picked.length || !codeId || !reason.trim() || !start || !end
            }
            onClick={() => void prepare()}
          >
            Подготовить предпросмотр
          </button>
        </div>
      </fieldset>
      {preview && (
        <section aria-label="Предпросмотр записок" className="absence-card">
          <h3>Проверьте записки</h3>
          {preview.status === "blocked" && (
            <p className="info-line" role="alert">
              Записки не внесены: у некоторых учеников есть замечания. Уберите
              отмеченных учеников и подготовьте предпросмотр заново.
            </p>
          )}
          <div className="absence-bulk-preview">
            {preview.items.map((item) => (
              <article className="absence-card" key={item.studentId}>
                <strong>
                  {item.studentName} · {item.className} · уроков:{" "}
                  {item.lessonCount}
                </strong>
                {item.problem && <p className="info-line">{item.problem}</p>}
                {item.lessons.map((l, i) => (
                  <p className="absence-file" key={i}>
                    {l.date} · {l.start}–{l.end} · {l.subject}
                    <button type="button" onClick={() => edge(l, "start")}>
                      С этого урока
                    </button>
                    <button type="button" onClick={() => edge(l, "end")}>
                      По этот урок
                    </button>
                  </p>
                ))}
                {item.lessonCount > item.lessons.length && (
                  <p>Показаны первые уроки: {item.lessons.length}.</p>
                )}
                {preview.status === "blocked" && item.problem && (
                  <button
                    type="button"
                    onClick={() =>
                      choose(picked.filter((id) => id !== item.studentId))
                    }
                  >
                    Убрать из списка
                  </button>
                )}
              </article>
            ))}
          </div>
          {preview.status === "preview" && (
            <>
              <p>
                Предпросмотр действителен до{" "}
                {preview.expires
                  ? new Date(preview.expires).toLocaleTimeString("ru-RU")
                  : ""}
                . Если данные или доступы изменятся, ни одна записка не будет
                внесена. После ошибки соединения можно повторить: записки не
                продублируются.
              </p>
              <button
                className="primary"
                disabled={busy}
                onClick={() => void apply()}
              >
                {schoolSubmitLabel(preview.items.length)}
              </button>
            </>
          )}
        </section>
      )}
      {result && (
        <section aria-label="Результат" className="absence-card" role="status">
          <strong>Внесено записок: {result.count}</strong>
          {result.items.map((i) => (
            <p key={i.id}>
              {i.studentName} ·{" "}
              {className(
                pupils.find((p) => p.id === i.studentId)?.classId ?? "",
              )}{" "}
              · принята
            </p>
          ))}
        </section>
      )}
    </section>
  );
}
