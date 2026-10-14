import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { State } from "./types";
import { named } from "./utils";
import ChangePreview, { type Preview } from "./ChangePreview";

type Config = {
  version: number;
  open: boolean;
  courseIds: string[];
  maxRequests: number;
  maxAlternatives: number;
  closesOn: string | null;
  message: string;
};
type Choice = { courseIds: string[] };
type Submission = {
  studentId: string;
  studentName?: string;
  version: number;
  status: string;
  choices: Choice[];
  note: string;
  comment: string;
  author?: string;
  reviewer?: string;
  inPlan?: boolean;
  updated?: string;
};
type Personal = {
  revision: number;
  config: Config;
  accepting: boolean;
  courses: { id: string; name: string }[];
  submission: Submission;
  inPlan: boolean;
};
const STATUSES: Record<string, string> = {
  draft: "Не отправлена",
  pending: "На рассмотрении",
  accepted: "Принята в план заявок",
  rejected: "Возвращена для уточнения",
};

export function IntakeAdmin({
  state: s,
  workspace,
  onState,
}: {
  state: State;
  workspace: string;
  onState: (state: State) => Promise<void>;
}) {
  const [config, setConfig] = useState<Config | null>(null),
    [rows, setRows] = useState<Submission[]>([]);
  const [offset, setOffset] = useState(0),
    [total, setTotal] = useState(0),
    [epoch, setEpoch] = useState(0);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  useEffect(() => {
    let live = true;
    void Promise.all([
      api("intake/config", workspace),
      api(`intake/incoming?offset=${offset}`, workspace),
    ])
      .then(([cfg, list]) => {
        if (live) {
          setConfig(cfg);
          setRows(list.rows);
          setTotal(list.total);
          setPreview(null);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, offset, epoch, s.revision]);
  async function run(task: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="intake-panel">
      <h2>Приём заявок через личный кабинет</h2>
      <p>
        Опубликуйте доступные курсы. Ученик или его родитель отправит
        приоритеты, а составитель проверит их перед переносом в план. Приём
        заявки не назначает учебную группу: распределение по секциям запускается
        отдельно.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {config && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              setConfig(
                await api("intake/config", workspace, "PUT", {
                  ...config,
                  revision: s.revision,
                }),
              );
              setEpoch((n) => n + 1);
            });
          }}
        >
          <label className="inline-check">
            <input
              type="checkbox"
              checked={config.open}
              onChange={(e) => setConfig({ ...config, open: e.target.checked })}
              disabled={busy}
            />
            Открыть приём
          </label>
          <div className="form-grid">
            <label className="field">
              <span>Дата окончания включительно</span>
              <input
                type="date"
                value={config.closesOn || ""}
                onChange={(e) =>
                  setConfig({ ...config, closesOn: e.target.value || null })
                }
              />
              <small>Проверяется по дате компьютера сервера.</small>
            </label>
            <label className="field">
              <span>Заявок от одного ученика</span>
              <input
                type="number"
                min={1}
                max={10}
                required
                value={config.maxRequests}
                onChange={(e) =>
                  setConfig({ ...config, maxRequests: Number(e.target.value) })
                }
              />
            </label>
            <label className="field">
              <span>Альтернатив в одной заявке</span>
              <input
                type="number"
                min={1}
                max={20}
                required
                value={config.maxAlternatives}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    maxAlternatives: Number(e.target.value),
                  })
                }
              />
            </label>
          </div>
          <label className="field">
            <span>Сообщение ученикам</span>
            <textarea
              rows={2}
              maxLength={2000}
              value={config.message}
              onChange={(e) =>
                setConfig({ ...config, message: e.target.value })
              }
            />
          </label>
          <fieldset>
            <legend>Курсы для приёма</legend>
            {(s.courses || []).length ? (
              (s.courses || []).map((c) => (
                <label key={c.id} className="inline-check">
                  <input
                    type="checkbox"
                    checked={config.courseIds.includes(c.id)}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        courseIds: e.target.checked
                          ? [...config.courseIds, c.id]
                          : config.courseIds.filter((id) => id !== c.id),
                      })
                    }
                  />
                  {c.name}
                </label>
              ))
            ) : (
              <p>Сначала создайте курсы и секции в каталоге.</p>
            )}
          </fieldset>
          <button className="primary" disabled={busy}>
            Сохранить условия приёма
          </button>
          <button type="button" disabled={busy} onClick={() => setConfig({ ...config, courseIds: [] })}>Снять выбор курсов</button>
        </form>
      )}
      <div className="dated-toolbar">
        <h3>Входящие предпочтения · {total}</h3>
        <button disabled={busy} onClick={() => setEpoch((n) => n + 1)}>
          Обновить заявки
        </button>
      </div>
      {!rows.length && <p className="muted">Заявки ещё не поступали.</p>}
      {rows.map((row) => (
        <article className="intake-submission" key={row.studentId}>
          <h4>
            {row.studentName} · {STATUSES[row.status] || row.status}
          </h4>
          <p className="muted">
            Редакция {row.version}
            {row.updated
              ? ` · ${new Date(row.updated).toLocaleString("ru")}`
              : ""}
            {row.author ? ` · ${row.author}` : ""}
          </p>
          <ol>
            {row.choices.map((choice, i) => (
              <li key={i}>
                  {choice.courseIds.map((id) => named(s.courses || [], id)).join(" → ")}
              </li>
            ))}
          </ol>
          {!row.choices.length && (
            <p>
              Запрошен отзыв предпочтений. Прежние составы групп не изменятся
              автоматически.
            </p>
          )}
          {row.note && <p className="intake-text">{row.note}</p>}
          {row.comment && (
            <p className="intake-text">
              Комментарий составителя: {row.comment}
            </p>
          )}
          {row.status === "accepted" &&
            !row.inPlan &&
            row.choices.length > 0 && (
              <p className="generation-warning">
                Принятые заявки отсутствуют в текущем плане или изменены после
                рассмотрения.
              </p>
            )}
          <label className="field">
            <span>Ответ ученику</span>
            <textarea
              maxLength={1000}
              rows={2}
              value={comments[row.studentId] || ""}
              onChange={(e) =>
                setComments({ ...comments, [row.studentId]: e.target.value })
              }
            />
          </label>
          <div className="dated-toolbar">
            <button
              disabled={busy || !["pending", "accepted"].includes(row.status)}
              onClick={() =>
                void run(async () =>
                  setPreview(
                    await api("intake/preview", workspace, "POST", {
                      revision: s.revision,
                      studentId: row.studentId,
                      version: row.version,
                      comment: comments[row.studentId] || "",
                    }),
                  ),
                )
              }
            >
              Проверить перенос в план
            </button>
            <button
              disabled={
                busy ||
                row.status !== "pending" ||
                !comments[row.studentId]?.trim()
              }
              onClick={() =>
                void run(async () => {
                  await api("intake/reject", workspace, "POST", {
                    revision: s.revision,
                    studentId: row.studentId,
                    version: row.version,
                    comment: comments[row.studentId],
                  });
                  setEpoch((n) => n + 1);
                })
              }
            >
              Вернуть для уточнения
            </button>
          </div>
        </article>
      ))}
      {total > 100 && (
        <div className="dated-toolbar">
          <button
            disabled={offset === 0 || busy}
            onClick={() => setOffset(Math.max(0, offset - 100))}
          >
            Предыдущие
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 100, total)} из {total}
          </span>
          <button
            disabled={offset + 100 >= total || busy}
            onClick={() => setOffset(offset + 100)}
          >
            Следующие
          </button>
        </div>
      )}
      {preview && (
        <ChangePreview
          preview={preview}
          busy={busy}
          onCancel={() => setPreview(null)}
          title="Перенос предпочтений в план заявок"
          onApply={() =>
            run(async () => {
              await onState(
                await api("intake/apply", workspace, "POST", {
                  token: preview.token,
                  revision: preview.revision,
                }),
              );
              setPreview(null);
              setEpoch((n) => n + 1);
            })
          }
        />
      )}
    </section>
  );
}

export function IntakePortal({ workspace }: { workspace: string }) {
  const [students, setStudents] = useState<{ id: string; name: string }[]>([]),
    [student, setStudent] = useState("");
  const [data, setData] = useState<Personal | null>(null),
    [choices, setChoices] = useState<Choice[]>([]),
    [note, setNote] = useState("");
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [epoch, setEpoch] = useState(0);
  const lock = useRef(false);
  const dirty =
    !!data &&
    (JSON.stringify(choices) !== JSON.stringify(data.submission.choices) ||
      note !== data.submission.note);
  useEffect(() => {
    let live = true;
    api("intake/options", workspace)
      .then((rows) => {
        if (live) {
          setStudents(rows);
          setStudent(rows[0]?.id || "");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  useEffect(() => {
    if (!student) return;
    let live = true;
    setData(null);
    setError("");
    api(`intake/student?studentId=${encodeURIComponent(student)}`, workspace)
      .then((value) => {
        if (live) {
          setData(value);
          setChoices(value.submission.choices);
          setNote(value.submission.note);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [student, workspace, epoch]);
  useEffect(() => {
    if (!dirty) return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  function discard() {
    return (
      !dirty ||
      window.confirm("Неотправленные изменения будут потеряны. Продолжить?")
    );
  }
  const editable = !!data?.accepting && !busy;
  function change(i: number, courses: string[]) {
    setChoices(choices.map((c, n) => (n === i ? { courseIds: courses } : c)));
  }
  return (
    <section className="intake-panel intake-personal">
      <h2>Мои предпочтения по курсам</h2>
      <p>
        В каждой заявке перечислите альтернативы по приоритету: первая — самый
        желаемый курс. По одной заявке выбирается один курс. Если нужны два
        разных курса, создайте две заявки.
      </p>
      <label className="field">
        <span>Ученик</span>
        <select
          value={student}
          disabled={busy}
          onChange={(e) => {
            if (discard()) {
              setStudent(e.target.value);
              setMessage("");
            }
          }}
        >
          {students.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={busy}
        onClick={() => {
          if (discard()) {
            setEpoch((n) => n + 1);
            setMessage("");
          }
        }}
      >
        Обновить данные
      </button>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="info-line">
          {message}
        </p>
      )}
      {data && (
        <>
          <p className="info-line">
            {data.accepting ? "Приём открыт" : "Приём закрыт"}
            {data.config.closesOn
              ? ` · до ${data.config.closesOn} включительно`
              : ""}
          </p>
          {data.config.message && (
            <p className="intake-text">{data.config.message}</p>
          )}
          <p>
            <strong>
              {STATUSES[data.submission.status] || data.submission.status}
            </strong>{" "}
            · редакция {data.submission.version}
          </p>
          {data.submission.comment && (
            <p className="intake-text">
              Ответ составителя: {data.submission.comment}
            </p>
          )}
          {data.submission.status === "accepted" &&
            !data.inPlan &&
            data.submission.choices.length > 0 && (
              <p className="generation-warning">
                После принятия рабочий план изменился. Уточните текущее
                назначение у составителя.
              </p>
            )}
          {data.inPlan && (
            <p className="muted">
              Последний принятый вариант есть в плане заявок. Назначение учебной
              группы выполняется отдельно.
            </p>
          )}
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (lock.current || !editable) return;
              if (
                !choices.length &&
                !window.confirm(
                  "Отправить запрос на отзыв предпочтений? Текущие группы пока сохранятся.",
                )
              )
                return;
              lock.current = true;
              setBusy(true);
              setError("");
              setMessage("");
              try {
                const result = await api("intake/student", workspace, "PUT", {
                  revision: data.revision,
                  configVersion: data.config.version,
                  version: data.submission.version,
                  studentId: student,
                  choices,
                  note,
                });
                setData(result);
                setChoices(result.submission.choices);
                setNote(result.submission.note);
                setMessage(
                  "Предпочтения отправлены составителю. Распределение по группам ещё не изменено.",
                );
              } catch (e: any) {
                setError(e.message);
              } finally {
                lock.current = false;
                setBusy(false);
              }
            }}
          >
            {choices.map((choice, i) => (
              <fieldset key={i}>
                <legend>Заявка {i + 1}</legend>
                {choice.courseIds.map((id, j) => (
                  <div className="intake-choice" key={j}>
                    <label className="field">
                      <span>Приоритет {j + 1}</span>
                      <select
                        required
                        disabled={!editable}
                        value={id}
                        onChange={(e) =>
                          change(
                            i,
                            choice.courseIds.map((v, n) =>
                              n === j ? e.target.value : v,
                            ),
                          )
                        }
                      >
                        <option value="">Выберите курс</option>
                        {id && !data.courses.some((c) => c.id === id) && (
                          <option value={id}>
                            Курс больше недоступен — замените или удалите
                          </option>
                        )}
                        {data.courses.map((c) => (
                          <option
                            key={c.id}
                            value={c.id}
                            disabled={
                              c.id !== id &&
                              choices.some((x) => x.courseIds.includes(c.id))
                            }
                          >
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={!editable || j === 0}
                      aria-label={`Поднять альтернативу ${j + 1} заявки ${i + 1}`}
                      onClick={() => {
                        const next = [...choice.courseIds];
                        [next[j - 1], next[j]] = [next[j], next[j - 1]];
                        change(i, next);
                      }}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      disabled={!editable || choice.courseIds.length === 1}
                      onClick={() =>
                        change(
                          i,
                          choice.courseIds.filter((_, n) => n !== j),
                        )
                      }
                    >
                      Убрать
                    </button>
                  </div>
                ))}
                <div className="dated-toolbar">
                  <button
                    type="button"
                    disabled={
                      !editable ||
                      choice.courseIds.length >= data.config.maxAlternatives
                    }
                    onClick={() => change(i, [...choice.courseIds, ""])}
                  >
                    Добавить альтернативу
                  </button>
                  <button
                    type="button"
                    disabled={!editable}
                    onClick={() =>
                      setChoices(choices.filter((_, n) => n !== i))
                    }
                  >
                    Убрать заявку
                  </button>
                </div>
              </fieldset>
            ))}
            <button
              type="button"
              disabled={!editable || choices.length >= data.config.maxRequests}
              onClick={() => setChoices([...choices, { courseIds: [""] }])}
            >
              Добавить заявку
            </button>
            <label className="field">
              <span>Комментарий составителю</span>
              <textarea
                maxLength={1000}
                rows={3}
                disabled={!editable}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <p className="muted">
              Отправка не меняет расписание и составы. После рассмотрения
              принятые предпочтения поступят в план; наличие мест и пересечения
              проверяются при распределении.
            </p>
            <button className="primary" disabled={!editable}>
              {busy
                ? "Отправляем…"
                : choices.length
                  ? "Отправить предпочтения"
                  : "Запросить отзыв предпочтений"}
            </button>
          </form>
        </>
      )}
    </section>
  );
}
