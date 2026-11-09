import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import Letterhead from "./Letterhead";

type Flags = { manage: boolean; member: boolean; family: boolean };
type Person = { id: string; name: string };
type Named = { id: string; name: string };
type Final = {
  status: "written" | "unchanged" | "skipped";
  label?: string;
  previous?: string | null;
  reason?: string;
};
type Exam = {
  id: string;
  version: number;
  status: "planned" | "held" | "decided" | "letter_sent" | "cancelled";
  statusTitle: string;
  kind: string;
  kindTitle: string;
  reason: string;
  studentId: string;
  student: string;
  classId: string;
  className: string;
  subjectId: string;
  subject: string;
  periodId: string;
  period: string;
  day: string;
  start: string;
  end: string;
  roomId: string;
  room: string;
  chairman: Person;
  examiner: Person;
  members: Person[];
  result: {
    outcome: "graded" | "absent";
    label: string;
    passed: boolean;
    tasks: string;
    notes: string;
    by: string;
    at: string;
    scaleName: string;
  } | null;
  decision: {
    decidedBy: string;
    decidedAt: string;
    final: Final;
    reason: string;
  } | null;
  letter: { number: string; sentAt: string; sentBy: string } | null;
  cancelReason: string;
  canEdit: boolean;
  canCancel: boolean;
  canResult: boolean;
  canDecide: boolean;
  canSendLetter: boolean;
};
type Scale = { id: string; version: number; name: string; labels: string[] };
type Options = {
  kinds: Record<string, string>;
  statuses: Record<string, string>;
  today: string;
  scales: Scale[];
  // Only the administrator receives what the planning form needs.
  teachers?: Named[];
  rooms?: Named[];
  subjects?: Named[];
  periods?: (Named & { status: string })[];
  classes?: Named[];
  students?: (Named & { classId: string; className: string })[];
};
type FamilyExam = {
  id: string;
  studentId: string;
  subject: string;
  period: string;
  kind: string;
  kindTitle: string;
  day: string;
  decision: {
    outcome: "graded" | "absent";
    label: string;
    passed: boolean;
    decidedAt: string;
  };
  letter: { number: string; sentAt: string } | null;
};
type Protocol = {
  school: string;
  kindTitle: string;
  reason: string;
  student: string;
  className: string;
  subject: string;
  period: string;
  day: string;
  start: string;
  end: string;
  room: string;
  chairman: Person;
  examiner: Person;
  members: Person[];
  result: Exam["result"];
};
type LetterText = {
  school: string;
  number: string;
  sentAt: string;
  student: string;
  className: string;
  subject: string;
  period: string;
  kindTitle: string;
  day: string;
  outcome: "graded" | "absent";
  label: string;
  passed: boolean;
  chairman: string;
  decidedBy: string;
};
type Doc =
  | { kind: "protocol"; data: Protocol }
  | { kind: "letter"; data: LetterText };
type History = {
  seq: number;
  actor: string;
  created: string;
  action: string;
  reason: string;
}[];

const PATH = "behaviour/commission-exams";
const emptyForm = {
  classId: "",
  studentId: "",
  subjectId: "",
  periodId: "",
  kind: "reexam",
  reason: "",
  day: "",
  start: "09:00",
  end: "10:00",
  roomId: "",
  chairmanId: "",
  examinerId: "",
  memberIds: [] as string[],
};
const actionLabels: Record<string, string> = {
  created: "Запланирован",
  rescheduled: "Изменён",
  cancelled: "Отменён",
  result: "Внесён результат",
  result_changed: "Результат изменён",
  decided: "Решение принято",
  letter_sent: "Письмо отправлено",
};
const stamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "";
const dayLabel = (day: string) =>
  new Date(day + "T00:00").toLocaleDateString("ru-RU");
const verdict = (d: {
  outcome: "graded" | "absent";
  label: string;
  passed: boolean;
}) =>
  d.outcome === "absent"
    ? "Не явился"
    : `${d.label}, ${d.passed ? "сдал(а)" : "не сдал(а)"}`;

export default function CommissionExams({
  workspace,
  flags,
  studentId,
  hideDocument,
  onDocument,
  onDirty,
}: {
  workspace: string;
  flags: Flags;
  // The child of the family portal.
  studentId?: string;
  // The behaviour letter is on screen: only one printable document at a time.
  hideDocument?: boolean;
  onDocument?: () => void;
  // The planning form holds changes that are not saved yet.
  onDirty?: (dirty: boolean) => void;
}) {
  const staff = flags.manage || flags.member;
  const [options, setOptions] = useState<Options | null>(null),
    [items, setItems] = useState<Exam[]>([]),
    [family, setFamily] = useState<FamilyExam[]>([]),
    [next, setNext] = useState<number | null>(null),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [doc, setDoc] = useState<Doc | null>(null),
    [history, setHistory] = useState<{ id: string; items: History } | null>(
      null,
    ),
    [form, setForm] = useState<
      (typeof emptyForm & { editing?: Exam }) | null
    >(null),
    [entering, setEntering] = useState<{
      id: string;
      outcome: "graded" | "absent";
      scaleId: string;
      label: string;
      passed: boolean;
      tasks: string;
      notes: string;
    } | null>(null),
    [deciding, setDeciding] = useState<{
      id: string;
      write: boolean;
      reason: string;
      hint: string;
    } | null>(null);
  const sequence = useRef(0),
    request = useRef({ key: "", id: "" });
  const dirty = Boolean(
    form && (form.reason.trim() || form.studentId || form.memberIds.length),
  );
  useEffect(() => onDirty?.(dirty), [dirty]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onDirty?.(false), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (hideDocument) setDoc(null);
  }, [hideDocument]);

  async function load(more = false) {
    const token = ++sequence.current;
    setBusy(true);
    setError("");
    try {
      if (!staff) {
        if (!studentId) return;
        const result = await api(
          `${PATH}/family?studentId=${encodeURIComponent(studentId)}`,
          workspace,
        );
        if (token === sequence.current) setFamily(result.items);
        return;
      }
      const opts: Options = options ?? (await api(PATH + "/options", workspace));
      const params = new URLSearchParams();
      if (status) params.set("status", status);
      if (more && next) params.set("before", String(next));
      const result = await api(
        PATH + (params.size ? "?" + params.toString() : ""),
        workspace,
      );
      if (token !== sequence.current) return;
      setOptions(opts);
      setItems((old) => (more ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
    } catch (e: any) {
      if (token === sequence.current) setError(e.message);
    } finally {
      if (token === sequence.current) setBusy(false);
    }
  }
  useEffect(() => {
    setDoc(null);
    setHistory(null);
    void load();
    return () => {
      ++sequence.current;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, status, studentId]);

  async function run(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const refresh = () => load();
  function show(next: Doc) {
    if (dirty && !window.confirm("Форма экзамена не сохранена. Отбросить её?"))
      return;
    setForm(null);
    onDocument?.();
    setDoc(next);
  }
  async function protocol(e: Exam) {
    await run(async () => {
      show({
        kind: "protocol",
        data: await api(`${PATH}/${e.id}/protocol`, workspace),
      });
    }, "");
  }
  async function letter(id: string) {
    await run(async () => {
      show({
        kind: "letter",
        data: await api(`${PATH}/${id}/letter`, workspace),
      });
    }, "");
  }
  async function showHistory(e: Exam) {
    if (history?.id === e.id) return setHistory(null);
    await run(async () => {
      const result = await api(`${PATH}/${e.id}/history`, workspace);
      setHistory({ id: e.id, items: result.items });
    }, "");
  }
  function edit(e: Exam) {
    setForm({
      ...emptyForm,
      classId: e.classId,
      studentId: e.studentId,
      subjectId: e.subjectId,
      periodId: e.periodId,
      kind: e.kind,
      reason: e.reason,
      day: e.day,
      start: e.start,
      end: e.end,
      roomId: e.roomId,
      chairmanId: e.chairman.id,
      examinerId: e.examiner.id,
      memberIds: e.members.map((m) => m.id),
      editing: e,
    });
  }
  async function save() {
    if (!form) return;
    const { editing, classId, ...fields } = form;
    void classId;
    const body = { ...fields };
    const key = JSON.stringify([editing?.id, body]);
    if (request.current.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    await run(async () => {
      if (editing)
        await api(`${PATH}/${editing.id}`, workspace, "PUT", {
          ...body,
          version: editing.version,
        });
      else
        await api(PATH, workspace, "POST", {
          ...body,
          requestId: request.current.id,
        });
      request.current = { key: "", id: "" };
      setForm(null);
      await refresh();
    }, editing ? "Экзамен изменён, комиссия получила уведомление." : "Экзамен запланирован, комиссия получила уведомление.");
  }
  async function cancel(e: Exam) {
    const reason = window.prompt("Причина отмены экзамена");
    if (!reason?.trim()) return;
    await run(async () => {
      await api(`${PATH}/${e.id}/cancel`, workspace, "POST", {
        version: e.version,
        reason,
      });
      await refresh();
    }, "Экзамен отменён.");
  }
  async function enter() {
    if (!entering || !options) return;
    const e = items.find((x) => x.id === entering.id);
    const scale = options.scales.find((s) => s.id === entering.scaleId);
    if (!e) return;
    await run(async () => {
      await api(`${PATH}/${e.id}/result`, workspace, "POST", {
        version: e.version,
        outcome: entering.outcome,
        tasks: entering.tasks,
        notes: entering.notes,
        ...(entering.outcome === "graded"
          ? {
              scaleId: entering.scaleId,
              scaleVersion: scale?.version ?? 0,
              label: entering.label,
              passed: entering.passed,
            }
          : {}),
      });
      setEntering(null);
      await refresh();
    }, "Результат внесён, администрация получила уведомление.");
  }
  function startEntering(e: Exam) {
    const scale = options?.scales[0];
    setEntering({
      id: e.id,
      outcome: e.result?.outcome ?? "graded",
      scaleId: scale?.id ?? "",
      label: e.result?.label || scale?.labels[0] || "",
      passed: e.result?.passed ?? true,
      tasks: e.result?.tasks ?? "",
      notes: e.result?.notes ?? "",
    });
  }
  async function decide(e: Exam, replace = false) {
    if (!deciding) return;
    const body = {
      version: e.version,
      writeFinal: deciding.write,
      replace,
      reason: deciding.reason,
    };
    const key = JSON.stringify([e.id, body]);
    if (request.current.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api(`${PATH}/${e.id}/decide`, workspace, "POST", {
        ...body,
        requestId: request.current.id,
      });
      request.current = { key: "", id: "" };
      setDeciding(null);
      await refresh();
      setNotice(
        result.final.status === "written"
          ? "Решение принято, оценка записана в итог периода."
          : result.final.status === "unchanged"
            ? "Решение принято, итог периода уже совпадал."
            : "Решение принято без записи в итог периода.",
      );
    } catch (x: any) {
      const code = x.detail?.code;
      if (code === "grade_exists")
        setDeciding({
          ...deciding,
          hint: `Опубликован итог «${x.detail.current?.label || "без оценки"}». Заменить решением комиссии? Укажите причину и нажмите «Заменить».`,
        });
      else if (code === "no_book")
        setDeciding({
          ...deciding,
          hint: `${x.message} Либо снимите отметку записи в итог и укажите причину.`,
        });
      else setError(x.message);
    } finally {
      setBusy(false);
    }
  }
  async function sendLetter(e: Exam) {
    if (!window.confirm("Отправить семье письмо с решением комиссии?")) return;
    await run(async () => {
      await api(`${PATH}/${e.id}/letter`, workspace, "POST", {
        version: e.version,
      });
      await refresh();
    }, "Письмо отправлено семье.");
  }

  const set = (change: Partial<typeof emptyForm>) =>
    setForm((f) => (f ? { ...f, ...change } : f));
  const pupils = (options?.students ?? []).filter(
    (s) => !form?.classId || s.classId === form.classId,
  );
  const colleagues = (options?.teachers ?? []).filter(
    (t) => t.id !== form?.chairmanId && t.id !== form?.examinerId,
  );

  const document_ = doc && (
    <section
      className="bn-letter"
      aria-label={
        doc.kind === "protocol" ? "Протокол экзамена" : "Решение комиссии"
      }
    >
      <Letterhead school={doc.data.school} />
      {doc.kind === "protocol" ? (
        <>
          <h3>Протокол комиссионного экзамена</h3>
          <p>
            Вид: {doc.data.kindTitle}. Основание: {doc.data.reason}
          </p>
          <p>
            Ученик: {doc.data.student}
            {doc.data.className ? `, ${doc.data.className}` : ""}
          </p>
          <p>
            Предмет: {doc.data.subject}. Период: {doc.data.period}
          </p>
          <p>
            Дата: {dayLabel(doc.data.day)}, {doc.data.start}–{doc.data.end}.
            Кабинет: {doc.data.room}
          </p>
          <p>
            Председатель: {doc.data.chairman.name}. Экзаменатор:{" "}
            {doc.data.examiner.name}
          </p>
          <p>Члены комиссии: {doc.data.members.map((m) => m.name).join(", ")}</p>
          {doc.data.result ? (
            <>
              <p>
                Результат:{" "}
                {verdict(doc.data.result)}
              </p>
              {doc.data.result.tasks && (
                <p className="bn-text">Задания: {doc.data.result.tasks}</p>
              )}
              {doc.data.result.notes && (
                <p className="bn-text">Ход экзамена: {doc.data.result.notes}</p>
              )}
            </>
          ) : (
            <>
              <p>Задания: ______________________________________________</p>
              <p>Ход экзамена: _________________________________________</p>
              <p>Оценка: ________  Сдал(а) / не сдал(а)</p>
            </>
          )}
          <div className="bn-signatures">
            {[
              `Председатель: ${doc.data.chairman.name}`,
              `Экзаменатор: ${doc.data.examiner.name}`,
              ...doc.data.members.map((m) => `Член комиссии: ${m.name}`),
            ].map((line) => (
              <span key={line}>{line}</span>
            ))}
          </div>
        </>
      ) : (
        <>
          <h3>
            Решение комиссии № {doc.data.number} от{" "}
            {dayLabel(doc.data.sentAt.slice(0, 10))}
          </h3>
          <p>
            Ученик: {doc.data.student}
            {doc.data.className ? `, ${doc.data.className}` : ""}
          </p>
          <p>
            Предмет: {doc.data.subject}. Период: {doc.data.period}
          </p>
          <p>
            Вид: {doc.data.kindTitle}. Дата экзамена: {dayLabel(doc.data.day)}
          </p>
          <p>Решение: {verdict(doc.data)}</p>
          <p>Председатель комиссии: {doc.data.chairman}</p>
          <p className="bn-sign">
            С решением ознакомлен(а): ____________________ Дата: ________
          </p>
        </>
      )}
      <div className="bn-controls bn-noprint">
        <button onClick={() => window.print()}>Печать</button>
        <button onClick={() => setDoc(null)}>Закрыть</button>
      </div>
    </section>
  );

  if (!staff) {
    if (!family.length) return <>{document_}</>;
    return (
      <section aria-label="Комиссионные экзамены">
        <h3>Комиссионные экзамены</h3>
        {error && <p role="alert">{error}</p>}
        {document_}
        <ul className="bn-list">
          {family.map((e) => (
            <li key={e.id} className="bn-note">
              <p>
                <strong>{e.subject}</strong> · {e.period} · {dayLabel(e.day)}
                <span className="bn-badge">{verdict(e.decision)}</span>
              </p>
              <p className="bn-meta">{e.kindTitle}</p>
              {e.letter && (
                <div className="bn-controls">
                  <button disabled={busy} onClick={() => void letter(e.id)}>
                    Письмо № {e.letter.number}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return (
    <section aria-label="Комиссионные экзамены">
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      {document_}
      <fieldset disabled={busy}>
        <div className="bn-controls">
          <label>
            Статус
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Все</option>
              {Object.entries(options?.statuses ?? {}).map(([id, title]) => (
                <option key={id} value={id}>
                  {title}
                </option>
              ))}
            </select>
          </label>
          {flags.manage && !form && (
            <button onClick={() => setForm({ ...emptyForm, day: options?.today ?? "" })}>
              Новый экзамен
            </button>
          )}
        </div>
        {flags.manage && form && options && (
          <section className="bn-form" aria-label="Комиссионный экзамен">
            <h3>
              {form.editing ? "Изменить экзамен" : "Новый комиссионный экзамен"}
            </h3>
            <div className="bn-controls">
              <label>
                Класс
                <select
                  value={form.classId}
                  onChange={(e) => set({ classId: e.target.value, studentId: "" })}
                >
                  <option value="">Все классы</option>
                  {options.classes?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Ученик
                <select
                  value={form.studentId}
                  onChange={(e) => set({ studentId: e.target.value })}
                >
                  <option value="">Выберите</option>
                  {pupils.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.className ? ` · ${s.className}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Предмет
                <select
                  value={form.subjectId}
                  onChange={(e) => set({ subjectId: e.target.value })}
                >
                  <option value="">Выберите</option>
                  {options.subjects?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Период
                <select
                  value={form.periodId}
                  onChange={(e) => set({ periodId: e.target.value })}
                >
                  <option value="">Выберите</option>
                  {options.periods?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Вид
                <select
                  value={form.kind}
                  onChange={(e) => set({ kind: e.target.value })}
                >
                  {Object.entries(options.kinds).map(([id, title]) => (
                    <option key={id} value={id}>
                      {title}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="bn-wide">
              Основание
              <textarea
                value={form.reason}
                maxLength={1000}
                onChange={(e) => set({ reason: e.target.value })}
              />
            </label>
            <div className="bn-controls">
              <label>
                Дата
                <input
                  type="date"
                  value={form.day}
                  onChange={(e) => set({ day: e.target.value })}
                />
              </label>
              <label>
                Начало
                <input
                  type="time"
                  value={form.start}
                  onChange={(e) => set({ start: e.target.value })}
                />
              </label>
              <label>
                Окончание
                <input
                  type="time"
                  value={form.end}
                  onChange={(e) => set({ end: e.target.value })}
                />
              </label>
              <label>
                Кабинет
                <select
                  value={form.roomId}
                  onChange={(e) => set({ roomId: e.target.value })}
                >
                  <option value="">Выберите</option>
                  {options.rooms?.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="bn-controls">
              <label>
                Председатель
                <select
                  value={form.chairmanId}
                  onChange={(e) =>
                    set({
                      chairmanId: e.target.value,
                      memberIds: form.memberIds.filter((m) => m !== e.target.value),
                    })
                  }
                >
                  <option value="">Выберите</option>
                  {options.teachers
                    ?.filter((t) => t.id !== form.examinerId)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Экзаменатор
                <select
                  value={form.examinerId}
                  onChange={(e) =>
                    set({
                      examinerId: e.target.value,
                      memberIds: form.memberIds.filter((m) => m !== e.target.value),
                    })
                  }
                >
                  <option value="">Выберите</option>
                  {options.teachers
                    ?.filter((t) => t.id !== form.chairmanId)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            <fieldset className="bn-pupils">
              <legend>
                Члены комиссии (выбрано {form.memberIds.length}, от 1 до 8)
              </legend>
              {colleagues.map((t) => (
                <label key={t.id}>
                  <input
                    type="checkbox"
                    checked={form.memberIds.includes(t.id)}
                    onChange={(e) =>
                      set({
                        memberIds: e.target.checked
                          ? [...form.memberIds, t.id]
                          : form.memberIds.filter((m) => m !== t.id),
                      })
                    }
                  />
                  {t.name}
                </label>
              ))}
            </fieldset>
            <div className="bn-controls">
              <button onClick={() => void save()}>
                {form.editing ? "Сохранить" : "Запланировать"}
              </button>
              <button
                onClick={() => {
                  if (!dirty || window.confirm("Отбросить несохранённую форму?"))
                    setForm(null);
                }}
              >
                Отмена
              </button>
            </div>
          </section>
        )}
        <ul className="bn-list">
          {items.map((e) => (
            <li
              key={e.id}
              className={`bn-note${e.status === "cancelled" ? " bn-withdrawn" : ""}`}
            >
              <p>
                <strong>{e.subject}</strong> · {e.student}
                {e.className ? ` · ${e.className}` : ""} · {e.period}
                <span className="bn-badge">{e.statusTitle}</span>
                {e.letter && (
                  <span className="bn-badge">Письмо № {e.letter.number}</span>
                )}
              </p>
              <p>
                {e.kindTitle}. {e.reason}
              </p>
              <p className="bn-meta">
                {dayLabel(e.day)}, {e.start}–{e.end} · кабинет {e.room}
                <br />
                Председатель: {e.chairman.name} · Экзаменатор: {e.examiner.name}{" "}
                · Члены: {e.members.map((m) => m.name).join(", ")}
              </p>
              {e.result && (
                <p>
                  Результат: {verdict(e.result)}
                  <span className="bn-meta"> · {e.result.by}</span>
                </p>
              )}
              {e.decision && (
                <p className="bn-meta">
                  Решение: {e.decision.decidedBy},{" "}
                  {stamp(e.decision.decidedAt)} ·{" "}
                  {e.decision.final.status === "written"
                    ? "записано в итог периода"
                    : e.decision.final.status === "unchanged"
                      ? "итог периода не изменился"
                      : `без записи в итог: ${e.decision.final.reason}`}
                </p>
              )}
              {e.cancelReason && (
                <p className="bn-meta">Отменён: {e.cancelReason}</p>
              )}
              <div className="bn-controls">
                {e.canResult && (
                  <button onClick={() => startEntering(e)}>Внести результат</button>
                )}
                {e.canDecide && (
                  <button
                    onClick={() =>
                      setDeciding({
                        id: e.id,
                        write: e.result?.outcome === "graded",
                        reason: "",
                        hint: "",
                      })
                    }
                  >
                    Утвердить решение
                  </button>
                )}
                {e.canSendLetter && (
                  <button onClick={() => void sendLetter(e)}>
                    Отправить письмо семье
                  </button>
                )}
                {e.canEdit && <button onClick={() => edit(e)}>Изменить</button>}
                {e.canCancel && (
                  <button onClick={() => void cancel(e)}>Отменить</button>
                )}
                {e.status !== "cancelled" && (
                  <button onClick={() => void protocol(e)}>Протокол</button>
                )}
                {e.status === "letter_sent" && (
                  <button onClick={() => void letter(e.id)}>Письмо</button>
                )}
                <button onClick={() => void showHistory(e)}>История</button>
              </div>
              {entering?.id === e.id && options && (
                <div className="bn-form" aria-label="Результат экзамена">
                  <div className="bn-controls">
                    <label>
                      Итог
                      <select
                        value={entering.outcome}
                        onChange={(x) =>
                          setEntering({
                            ...entering,
                            outcome: x.target.value as "graded" | "absent",
                            passed: x.target.value === "graded",
                          })
                        }
                      >
                        <option value="graded">Оценка</option>
                        <option value="absent">Не явился</option>
                      </select>
                    </label>
                    {entering.outcome === "graded" && (
                      <>
                        <label>
                          Шкала
                          <select
                            value={entering.scaleId}
                            onChange={(x) =>
                              setEntering({
                                ...entering,
                                scaleId: x.target.value,
                                label:
                                  options.scales.find((s) => s.id === x.target.value)
                                    ?.labels[0] ?? "",
                              })
                            }
                          >
                            {options.scales.map((s) => (
                              <option key={s.id} value={s.id}>
                                {s.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Оценка
                          <select
                            value={entering.label}
                            onChange={(x) =>
                              setEntering({ ...entering, label: x.target.value })
                            }
                          >
                            {options.scales
                              .find((s) => s.id === entering.scaleId)
                              ?.labels.map((l) => (
                                <option key={l} value={l}>
                                  {l}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label className="bn-check">
                          <input
                            type="checkbox"
                            checked={entering.passed}
                            onChange={(x) =>
                              setEntering({ ...entering, passed: x.target.checked })
                            }
                          />
                          Сдал
                        </label>
                      </>
                    )}
                  </div>
                  <label className="bn-wide">
                    Задания (билет)
                    <textarea
                      value={entering.tasks}
                      maxLength={4000}
                      onChange={(x) =>
                        setEntering({ ...entering, tasks: x.target.value })
                      }
                    />
                  </label>
                  <label className="bn-wide">
                    Ход экзамена
                    <textarea
                      value={entering.notes}
                      maxLength={4000}
                      onChange={(x) =>
                        setEntering({ ...entering, notes: x.target.value })
                      }
                    />
                  </label>
                  <div className="bn-controls">
                    <button onClick={() => void enter()}>Сохранить результат</button>
                    <button onClick={() => setEntering(null)}>Отмена</button>
                  </div>
                </div>
              )}
              {deciding?.id === e.id && (
                <div className="bn-form" aria-label="Решение комиссии">
                  <label className="bn-check">
                    <input
                      type="checkbox"
                      checked={deciding.write}
                      disabled={e.result?.outcome !== "graded"}
                      onChange={(x) =>
                        setDeciding({
                          ...deciding,
                          write: x.target.checked,
                          hint: "",
                        })
                      }
                    />
                    Записать в итоговую оценку периода
                  </label>
                  <label className="bn-wide">
                    {deciding.write ? "Причина замены (если итог уже есть)" : "Причина, почему итог не записывается"}
                    <input
                      value={deciding.reason}
                      maxLength={500}
                      onChange={(x) =>
                        setDeciding({ ...deciding, reason: x.target.value })
                      }
                    />
                  </label>
                  {deciding.hint && <p role="alert">{deciding.hint}</p>}
                  <div className="bn-controls">
                    <button onClick={() => void decide(e)}>Утвердить</button>
                    {deciding.hint.startsWith("Опубликован") && (
                      <button onClick={() => void decide(e, true)}>
                        Заменить
                      </button>
                    )}
                    <button onClick={() => setDeciding(null)}>Отмена</button>
                  </div>
                </div>
              )}
              {history?.id === e.id && (
                <ol className="bn-history">
                  {history.items.map((h) => (
                    <li key={h.seq}>
                      {stamp(h.created)} · {h.actor} ·{" "}
                      {actionLabels[h.action] || h.action}
                      {h.reason ? ` · ${h.reason}` : ""}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
        {!items.length && !busy && <p>Комиссионных экзаменов нет.</p>}
        {next && <button onClick={() => void load(true)}>Показать ещё</button>}
      </fieldset>
    </section>
  );
}
