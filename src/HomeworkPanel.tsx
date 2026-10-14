import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { MaterialLinks, type Material } from "./AssignmentFilesPanel";

interface Attachment {
  id: string;
  filename: string;
  size: number;
  created: string;
}
interface Submitted {
  assignmentFiles: Material[];
  latePolicy: "allow" | "block";
  text: string;
  files: Attachment[];
  submittedAt: string;
  attempt: number;
  assignment: string;
  homework: string;
  dueDate: string | null;
  late: boolean;
  review: {
    decision: string;
    comment: string;
    reviewedAt: string;
    reviewer: string;
  } | null;
}
interface Work {
  assignmentFiles: Material[];
  latePolicy: "allow" | "block";
  deadlineClosed: boolean;
  canSaveDraft: boolean;
  assignment: string;
  homework: string;
  dueDate: string | null;
  subject: string;
  staff: boolean;
  canReview: boolean;
  version: number;
  status: string;
  student: { id: string; name: string };
  canSubmit: boolean;
  canWithdraw: boolean;
  draft: { text: string; fileIds: string[] } | null;
  availableFiles: Attachment[];
  submitted: Submitted | null;
  assignmentChanged: boolean;
}
interface Overview {
  students: {
    studentId: string;
    name: string;
    status: string;
    late: boolean;
    assignmentChanged: boolean;
  }[];
}
interface History {
  id: number;
  created: string;
  actor: string;
  action: string;
  submitted: Submitted | null;
}
const labels: Record<string, string> = {
  not_submitted: "Не отправлено",
  submitted: "На проверке",
  returned: "На доработке",
  accepted: "Принято",
};
const historyLabels: Record<string, string> = {
  "homework.submitted": "Отправка",
  "homework.reviewed": "Проверка",
  "homework.withdrawn": "Работа отозвана",
};

function fileLink(workspace: string, key: string, student: string, id: string) {
  return `/api/journal/homework/${encodeURIComponent(key)}/files/${id}?${new URLSearchParams({ workspace, studentId: student })}`;
}
function Files({
  items,
  workspace,
  lessonKey,
  studentId,
}: {
  items: Attachment[];
  workspace: string;
  lessonKey: string;
  studentId: string;
}) {
  return (
    <ul className="homework-files">
      {items.map((f) => (
        <li key={f.id}>
          <a href={fileLink(workspace, lessonKey, studentId, f.id)} download>
            {f.filename}
          </a>
          <small> · {Math.max(1, Math.ceil(f.size / 1024))} КБ</small>
        </li>
      ))}
    </ul>
  );
}

function StudentWork({
  workspace,
  lessonKey,
  studentId,
  changed,
  onActivity,
}: {
  workspace: string;
  lessonKey: string;
  studentId: string;
  changed: () => void;
  onActivity: (value: { dirty: boolean; busy: boolean }) => void;
}) {
  const [work, setWork] = useState<Work | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [dirty, setDirty] = useState(false);
  const [text, setText] = useState(""),
    [fileIds, setFileIds] = useState<string[]>([]),
    [comment, setComment] = useState("");
  const [history, setHistory] = useState<History[]>([]),
    [next, setNext] = useState<number | null>(null),
    [historyOpen, setHistoryOpen] = useState(false);
  const alive = useRef(true),
    upload = useRef<AbortController | null>(null);
  const endpoint = `journal/homework/${encodeURIComponent(lessonKey)}`;
  const target = `studentId=${encodeURIComponent(studentId)}`;
  function receive(value: Work) {
    setWork(value);
    setText(value.draft?.text || "");
    setFileIds(value.draft?.fileIds || []);
    setComment("");
    setDirty(false);
    setHistory([]);
    setHistoryOpen(false);
    setNext(null);
  }
  useEffect(() => {
    alive.current = true;
    let live = true;
    api(`${endpoint}?${target}`, workspace)
      .then((r) => {
        if (live) receive(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
      alive.current = false;
      upload.current?.abort();
    };
  }, [workspace, lessonKey, studentId]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    onActivity({ dirty, busy });
    return () => onActivity({ dirty: false, busy: false });
  }, [dirty, busy, onActivity]);
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function save(action: "draft" | "submit" | "withdraw") {
    if (!work) return;
    const r = await api(endpoint, workspace, "PUT", {
      studentId,
      version: work.version,
      assignment: work.assignment,
      action,
      text,
      fileIds,
    });
    if (alive.current) {
      receive(r);
      changed();
    }
  }
  async function check(decision: string) {
    if (!work) return;
    const r = await api(`${endpoint}/review`, workspace, "POST", {
      studentId,
      version: work.version,
      assignment: work.assignment,
      decision,
      comment,
    });
    if (alive.current) {
      receive(r);
      changed();
    }
  }
  async function historyPage(before?: number) {
    const r = await api(
      `${endpoint}/history?${target}${before ? `&before=${before}` : ""}`,
      workspace,
    );
    if (alive.current) {
      setHistory((old) => (before ? [...old, ...r.items] : r.items));
      setNext(r.nextBefore);
      setHistoryOpen(true);
    }
  }
  if (!work)
    return (
      <div>
        {error ? (
          <p className="error-banner" role="alert">
            {error}
          </p>
        ) : (
          <p role="status">Загружаю домашнюю работу…</p>
        )}
      </div>
    );
  return (
    <section className="homework-work">
      <div className="dated-toolbar">
        <h3>
          {work.student.name} · {labels[work.status]}
        </h3>
        <button
          disabled={busy}
          onClick={() => {
            if (
              dirty &&
              !window.confirm(
                "Заменить несохранённый ответ последней сохранённой версией?",
              )
            )
              return;
            void run(async () => {
              const r = await api(`${endpoint}?${target}`, workspace);
              if (alive.current) receive(r);
            });
          }}
        >
          Обновить работу
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <p className="journal-text">
        <strong>{work.subject}. </strong>
        {work.homework}
      </p>
      <p>
        {work.dueDate
          ? `Срок: ${work.dueDate}, до конца дня по времени школы (Кызылорда). ${work.latePolicy === "block" ? "После срока приём закрывается." : "Поздние отправки принимаются с отметкой."}`
          : "Срок не указан."}
      </p>
      <MaterialLinks
        items={work.assignmentFiles}
        workspace={workspace}
        studentId={studentId}
      />
      {work.deadlineClosed && (
        <p className="info-line">
          Приём работ закрыт. Ученик может сохранить черновик. Для отправки, в
          том числе после возврата на доработку, учитель должен продлить срок
          или разрешить позднюю сдачу.
        </p>
      )}
      {work.assignmentChanged && (
        <p className="info-line">
          После отправки условия задания изменились. Прежняя работа сохранена;
          новый ответ отправляется по текущим условиям и сроку.
        </p>
      )}
      {work.submitted && (
        <article className="homework-submitted">
          <h4>
            Отправка №{work.submitted.attempt} ·{" "}
            {new Date(work.submitted.submittedAt).toLocaleString("ru-RU")}
            {work.submitted.late ? " · после срока" : ""}
          </h4>
          <p className="journal-text">{work.submitted.text}</p>
          {work.assignmentChanged && (
            <details>
              <summary>Условия на момент отправки</summary>
              <p className="journal-text">{work.submitted.homework}</p>
              <p>
                Срок: {work.submitted.dueDate || "не указан"}.{" "}
                {work.submitted.latePolicy === "block"
                  ? "После срока приём закрыт."
                  : "Поздние отправки разрешены."}
              </p>
              <MaterialLinks
                items={work.submitted.assignmentFiles}
                workspace={workspace}
                studentId={studentId}
              />
            </details>
          )}
          <Files
            items={work.submitted.files}
            workspace={workspace}
            lessonKey={lessonKey}
            studentId={studentId}
          />
          {work.submitted.review && (
            <div className="info-line">
              <strong>{labels[work.submitted.review.decision]}</strong>
              <p className="journal-text">{work.submitted.review.comment}</p>
              <small>
                {work.submitted.review.reviewer} ·{" "}
                {new Date(work.submitted.review.reviewedAt).toLocaleString(
                  "ru-RU",
                )}
              </small>
            </div>
          )}
        </article>
      )}
      {work.canSaveDraft && (
        <fieldset className="access-fields homework-draft" disabled={busy}>
          <h4>Ваш черновик {dirty ? "· есть несохранённые изменения" : ""}</h4>
          <p className="muted">
            Черновик виден только вам. Учитель и родитель увидят ответ после
            отправки.
          </p>
          <label className="field">
            <span>Ответ на домашнее задание</span>
            <textarea
              rows={5}
              maxLength={10000}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <label className="field">
            <span>Приложить файлы</span>
            <input
              type="file"
              multiple
              accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx"
              disabled={fileIds.length >= 5}
              onChange={(e) => {
                const picked = Array.from(e.target.files || []);
                e.target.value = "";
                if (!picked.length) return;
                void run(async () => {
                  if (picked.length + fileIds.length > 5)
                    throw Error(
                      "В одном ответе может быть не больше пяти файлов.",
                    );
                  if (picked.some((f) => f.size > 10 * 1024 * 1024))
                    throw Error("Каждый файл должен быть не больше 10 МБ.");
                  upload.current = new AbortController();
                  for (const f of picked) {
                    const params = new URLSearchParams({
                      workspace,
                      studentId,
                      filename: f.name,
                      assignment: work.assignment,
                    });
                    const response = await fetch(
                      `/api/${endpoint}/files?${params}`,
                      {
                        method: "POST",
                        headers: {
                          "X-Atlas-Request": "1",
                          "Content-Type": "application/octet-stream",
                        },
                        body: f,
                        signal: upload.current.signal,
                      },
                    );
                    let result: any;
                    try {
                      result = await response.json();
                    } catch {
                      throw Error("Сервер не принял файл. Повторите загрузку.");
                    }
                    if (!response.ok)
                      throw Error(
                        typeof result.detail === "string"
                          ? result.detail
                          : "Файл не принят.",
                      );
                    if (!alive.current) return;
                    setWork((old) =>
                      old
                        ? {
                            ...old,
                            availableFiles: [...old.availableFiles, result],
                          }
                        : old,
                    );
                    setFileIds((old) => [...old, result.id]);
                    setDirty(true);
                  }
                });
              }}
            />
            <small>
              До 5 файлов по 10 МБ: PDF, изображения, TXT, DOCX, XLSX, PPTX.
            </small>
          </label>
          {!!work.availableFiles.length && (
            <fieldset className="dated-multi">
              <legend>Файлы этого задания</legend>
              {work.availableFiles.map((f) => (
                <label className="check-line" key={f.id}>
                  <input
                    type="checkbox"
                    checked={fileIds.includes(f.id)}
                    disabled={!fileIds.includes(f.id) && fileIds.length >= 5}
                    onChange={(e) => {
                      setFileIds((old) =>
                        e.target.checked
                          ? [...old, f.id]
                          : old.filter((x) => x !== f.id),
                      );
                      setDirty(true);
                    }}
                  />
                  {f.filename}
                </label>
              ))}
              <small>Выберите файлы, которые войдут в ответ.</small>
            </fieldset>
          )}
          <div className="dated-toolbar">
            <button onClick={() => void run(() => save("draft"))}>
              Сохранить черновик ответа
            </button>
            <button
              className="primary"
              disabled={!work.canSubmit || (!text.trim() && !fileIds.length)}
              onClick={() => void run(() => save("submit"))}
            >
              Отправить работу
            </button>
          </div>
        </fieldset>
      )}
      {work.canWithdraw && (
        <div>
          {work.deadlineClosed && (
            <p className="muted">
              После отзыва повторная отправка будет недоступна до изменения
              срока учителем.
            </p>
          )}
          <button
            disabled={busy}
            onClick={() => void run(() => save("withdraw"))}
          >
            Отозвать работу для изменения
          </button>
        </div>
      )}
      {work.canReview && work.submitted && work.status !== "returned" && (
        <fieldset className="access-fields homework-review" disabled={busy}>
          <label className="field">
            <span>Комментарий учителя</span>
            <textarea
              rows={3}
              maxLength={4000}
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <div className="dated-toolbar">
            <button
              className="primary"
              disabled={work.assignmentChanged}
              onClick={() => void run(() => check("accepted"))}
            >
              Принять работу
            </button>
            <button
              disabled={!comment.trim()}
              onClick={() => void run(() => check("returned"))}
            >
              Вернуть на доработку
            </button>
          </div>
          <small>
            Принятие подтверждает проверку. Оценка выставляется отдельно в
            журнале урока.
          </small>
        </fieldset>
      )}
      <div className="dated-toolbar">
        <button disabled={busy} onClick={() => void run(() => historyPage())}>
          История отправок и проверки
        </button>
      </div>
      {historyOpen && (
        <div className="homework-history">
          {!history.length && <p>Отправок пока нет.</p>}
          {history.map((h) => (
            <details key={h.id}>
              <summary>
                {historyLabels[h.action] || h.action} ·{" "}
                {new Date(h.created).toLocaleString("ru-RU")} · {h.actor}
              </summary>
              {h.submitted && (
                <>
                  <p className="journal-text">
                    <strong>Условия на момент отправки: </strong>
                    {h.submitted.homework}
                  </p>
                  <p>
                    Срок: {h.submitted.dueDate || "не указан"}.{" "}
                    {h.submitted.latePolicy === "block"
                      ? "После срока приём закрыт."
                      : "Поздние отправки разрешены."}
                  </p>
                  <MaterialLinks
                    items={h.submitted.assignmentFiles}
                    workspace={workspace}
                    studentId={studentId}
                  />
                  <p className="journal-text">{h.submitted.text}</p>
                  <Files
                    items={h.submitted.files}
                    workspace={workspace}
                    lessonKey={lessonKey}
                    studentId={studentId}
                  />
                  {h.submitted.review && (
                    <p className="journal-text">
                      {labels[h.submitted.review.decision]}:{" "}
                      {h.submitted.review.comment}
                    </p>
                  )}
                </>
              )}
            </details>
          ))}
          {next && (
            <button
              disabled={busy}
              onClick={() => void run(() => historyPage(next))}
            >
              Более ранние записи
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export default function HomeworkPanel({
  workspace,
  lessonKey,
  studentId,
}: {
  workspace: string;
  lessonKey: string;
  studentId?: string;
}) {
  const [open, setOpen] = useState(false),
    [overview, setOverview] = useState<Overview | null>(null),
    [selected, setSelected] = useState(""),
    [error, setError] = useState(""),
    [epoch, setEpoch] = useState(0);
  const [activity, setActivity] = useState({ dirty: false, busy: false });
  function canLeave() {
    return (
      !activity.busy &&
      (!activity.dirty ||
        window.confirm("Закрыть несохранённые изменения домашней работы?"))
    );
  }
  useEffect(() => {
    if (!open || studentId) return;
    let live = true;
    api(`journal/homework/${encodeURIComponent(lessonKey)}`, workspace)
      .then((r) => {
        if (live) {
          setOverview(r);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [open, workspace, lessonKey, studentId, epoch]);
  return (
    <div className="homework-panel journal-controls">
      <button
        aria-expanded={open}
        disabled={activity.busy}
        onClick={() => {
          if (!open || canLeave()) setOpen((v) => !v);
        }}
      >
        {open
          ? "Свернуть домашние работы"
          : studentId
            ? "Открыть домашнюю работу"
            : "Домашние работы учеников"}
      </button>
      {open && (
        <>
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {!studentId && overview && (
            <div className="homework-roster">
              <p>
                На проверке:{" "}
                {
                  overview.students.filter((s) => s.status === "submitted")
                    .length
                }{" "}
                · принято:{" "}
                {
                  overview.students.filter((s) => s.status === "accepted")
                    .length
                }{" "}
                · на доработке:{" "}
                {
                  overview.students.filter((s) => s.status === "returned")
                    .length
                }
              </p>
              {overview.students.map((s) => (
                <button
                  key={s.studentId}
                  aria-pressed={selected === s.studentId}
                  disabled={activity.busy}
                  onClick={() => {
                    if (selected === s.studentId || canLeave())
                      setSelected(s.studentId);
                  }}
                >
                  {s.name} · {labels[s.status]}
                  {s.late ? " · после срока" : ""}
                  {s.assignmentChanged ? " · задание изменено" : ""}
                </button>
              ))}
            </div>
          )}
          {(studentId || selected) && (
            <StudentWork
              key={workspace + lessonKey + (studentId || selected)}
              workspace={workspace}
              lessonKey={lessonKey}
              studentId={studentId || selected}
              changed={() => setEpoch((e) => e + 1)}
              onActivity={setActivity}
            />
          )}
        </>
      )}
    </div>
  );
}
