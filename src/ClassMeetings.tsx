import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import MeetingPrint from "./MeetingPrint";
import type { ClassDetail, ClassItem, ClassPlan, Options } from "./meetings";
import {
  addDays,
  clockTime,
  displayTime,
  labels,
  schoolInput,
  schoolISO,
  todaySchool,
} from "./meetings";

const actions: Record<string, string> = {
  created: "Создано",
  edited: "Изменено",
  synced: "Список класса обновлён",
  cancelled: "Отменено",
  marked: "Отмечена явка",
};

type Form = {
  id?: string;
  version?: number;
  title: string;
  description: string;
  mode: "classes" | "grade";
  classIds: string[];
  grade: string;
  day: string;
  from: string;
  to: string;
  roomId: string;
  location: string;
};

const blank = (options: Options): Form => ({
  title: "Родительское собрание",
  description: "",
  mode: "classes",
  classIds:
    options.classMeeting.classes.length === 1
      ? [options.classMeeting.classes[0].id]
      : [],
  grade: options.classMeeting.parallels[0]?.grade || "",
  day: addDays(todaySchool(), 1),
  from: "18:00",
  to: "19:00",
  roomId: "",
  location: "",
});

export default function ClassMeetings({
  workspace,
  family,
  admin,
  options,
  onDirty,
}: {
  workspace: string;
  family: boolean;
  admin: boolean;
  options: Options;
  onDirty: (dirty: boolean) => void;
}) {
  const [items, setItems] = useState<ClassItem[]>([]),
    [next, setNext] = useState<number | null>(null),
    [detail, setDetail] = useState<ClassDetail | null>(null),
    [form, setForm] = useState<Form | null>(null),
    [plan, setPlan] = useState<ClassPlan | null>(null),
    [marks, setMarks] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      setPlan(null);
    } finally {
      setBusy(false);
    }
  }
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api(
      "meetings/class-meetings" + path,
      workspace,
      method,
      {
        ...body,
        requestId: replay.current.id,
      },
    );
    replay.current = { key: "", id: "" };
    return result;
  }
  async function load(before?: number) {
    const r = await api(
      "meetings/class-meetings" + (before ? `?before=${before}` : ""),
      workspace,
    );
    setItems((old) => (before ? [...old, ...r.items] : r.items));
    setNext(r.nextBefore);
  }
  async function open(id: string) {
    const d: ClassDetail = await api(
      "meetings/class-meetings/" + id,
      workspace,
    );
    setDetail(d);
    setMarks(
      Object.fromEntries((d.roster || []).map((r) => [r.studentId, r.mark])),
    );
  }
  useEffect(() => {
    const n = ++sequence.current;
    setDetail(null);
    setForm(null);
    setItems([]);
    setBusy(true);
    setError("");
    api("meetings/class-meetings", workspace)
      .then((r) => {
        if (n !== sequence.current) return;
        setItems(r.items);
        setNext(r.nextBefore);
      })
      .catch((e) => {
        if (n === sequence.current) setError(e.message);
      })
      .finally(() => {
        if (n === sequence.current) setBusy(false);
      });
    return () => {
      ++sequence.current;
    };
  }, [workspace]);
  const change = (patch: Partial<Form>) => {
    setForm((old) => (old ? { ...old, ...patch } : old));
    setPlan(null);
    onDirty(true);
  };
  function body(f: Form) {
    return {
      title: f.title,
      description: f.description,
      ...(f.mode === "grade" ? { grade: f.grade } : { classIds: f.classIds }),
      startsAt: schoolISO(f.day + "T" + f.from),
      endsAt: schoolISO(f.day + "T" + f.to),
      ...(f.roomId ? { roomId: f.roomId } : {}),
      location: f.location,
    };
  }
  function edit(d: ClassDetail) {
    setForm({
      id: d.id,
      version: d.version,
      title: d.title,
      description: d.description,
      mode: d.grade ? "grade" : "classes",
      classIds: d.classIds,
      grade: d.grade || options.classMeeting.parallels[0]?.grade || "",
      day: schoolInput(d.startsAt).slice(0, 10),
      from: clockTime(d.startsAt),
      to: clockTime(d.endsAt),
      roomId: d.roomId || "",
      location: d.location,
    });
    setPlan(null);
  }
  async function cancel(d: ClassDetail) {
    const answer = window.prompt(
      "Причина отмены собрания. Родители увидят её в разделе «Собрания классов».",
    );
    if (!answer?.trim()) return;
    await mutate(`/${d.id}/cancel`, {
      version: d.version,
      reason: answer.trim(),
    });
    await open(d.id);
    await load();
    setNotice("Собрание отменено, родителям отправлено уведомление.");
  }
  const changed = detail
    ? Object.entries(marks)
        .filter(
          ([id, mark]) =>
            detail.roster.find((r) => r.studentId === id)?.mark !== mark,
        )
        .map(([studentId, mark]) => ({ studentId, mark }))
    : [];
  return (
    <section className="class-meetings" aria-label="Собрания классов">
      {error && (
        <p role="alert" className="meeting-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="meeting-notice">
          {notice}
        </p>
      )}
      {busy && <p role="status">Загружаю…</p>}
      {!family && !form && (
        <div className="meeting-toolbar">
          {options.classMeeting.canCreate && (
            <button
              disabled={busy}
              onClick={() => {
                setDetail(null);
                setPlan(null);
                setForm(blank(options));
              }}
            >
              Новое собрание класса
            </button>
          )}
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                if (detail) await open(detail.id);
                await load();
              })
            }
          >
            Обновить
          </button>
        </div>
      )}
      {form && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () =>
              setPlan(
                await api(
                  "meetings/class-meetings/preview",
                  workspace,
                  "POST",
                  {
                    ...body(form),
                    ...(form.id ? { id: form.id } : {}),
                  },
                ),
              ),
            );
          }}
        >
          <fieldset disabled={busy}>
            <legend>
              {form.id ? "Изменить собрание" : "Новое собрание класса"}
            </legend>
            <label>
              Название
              <input
                required
                maxLength={160}
                value={form.title}
                onChange={(e) => change({ title: e.target.value })}
              />
            </label>
            <label>
              Описание (увидят родители)
              <textarea
                maxLength={2000}
                value={form.description}
                onChange={(e) => change({ description: e.target.value })}
              />
            </label>
            {admin && (
              <label>
                Кого приглашаем
                <select
                  value={form.mode}
                  onChange={(e) =>
                    change({ mode: e.target.value as Form["mode"] })
                  }
                >
                  <option value="classes">Выбранные классы</option>
                  <option value="grade">Параллель целиком</option>
                </select>
              </label>
            )}
            {form.mode === "grade" ? (
              <label>
                Параллель
                <select
                  value={form.grade}
                  onChange={(e) => change({ grade: e.target.value })}
                >
                  {options.classMeeting.parallels.map((p) => (
                    <option key={p.grade} value={p.grade}>
                      {p.grade} классы ({p.classIds.length})
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="meeting-checks" role="group" aria-label="Классы">
                {options.classMeeting.classes.map((c) => (
                  <label key={c.id}>
                    <input
                      type="checkbox"
                      checked={form.classIds.includes(c.id)}
                      onChange={(e) =>
                        change({
                          classIds: e.target.checked
                            ? [...form.classIds, c.id]
                            : form.classIds.filter((id) => id !== c.id),
                        })
                      }
                    />
                    {c.name}
                  </label>
                ))}
              </div>
            )}
            <div className="meeting-fields">
              <label>
                День
                <input
                  type="date"
                  required
                  value={form.day}
                  onChange={(e) => change({ day: e.target.value })}
                />
              </label>
              <label>
                С
                <input
                  type="time"
                  required
                  value={form.from}
                  onChange={(e) => change({ from: e.target.value })}
                />
              </label>
              <label>
                До
                <input
                  type="time"
                  required
                  value={form.to}
                  onChange={(e) => change({ to: e.target.value })}
                />
              </label>
              <label>
                Кабинет
                <select
                  value={form.roomId}
                  onChange={(e) => change({ roomId: e.target.value })}
                >
                  <option value="">Без кабинета</option>
                  {options.rooms.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Или другое место
                <input
                  maxLength={300}
                  value={form.location}
                  onChange={(e) => change({ location: e.target.value })}
                />
              </label>
            </div>
            <div className="meeting-toolbar">
              <button
                type="submit"
                disabled={
                  form.mode === "classes" ? !form.classIds.length : !form.grade
                }
              >
                Проверить
              </button>
              <button
                type="button"
                onClick={() => {
                  setForm(null);
                  setPlan(null);
                  onDirty(false);
                }}
              >
                Отмена
              </button>
            </div>
            {plan && (
              <div className="meeting-preview" role="status">
                <p>
                  Классы:{" "}
                  <strong>{plan.classes.map((c) => c.name).join(", ")}</strong>.
                  Учеников на эту дату: <strong>{plan.pupils}</strong>. Получат
                  приглашение родителей: <strong>{plan.guardians}</strong>.
                </p>
                {plan.withoutGuardianCount > 0 && (
                  <p>
                    Без аккаунта родителя ({plan.withoutGuardianCount}):{" "}
                    {plan.withoutGuardian.join(", ")}
                    {plan.withoutGuardianCount > plan.withoutGuardian.length
                      ? " и др."
                      : ""}
                    . Сообщите им лично.
                  </p>
                )}
                {plan.closedDay && (
                  <p className="meeting-error">
                    В этот день школа или класс закрыты: {plan.closedDay}.
                    Проверьте дату.
                  </p>
                )}
                {plan.conflicts.map((c) => (
                  <p key={c} className="meeting-error">
                    {c}
                  </p>
                ))}
                <button
                  type="button"
                  className="primary"
                  disabled={busy || plan.conflicts.length > 0}
                  onClick={() =>
                    void run(async () => {
                      const edited = !!form.id;
                      const r = edited
                        ? await mutate(
                            `/${form.id}`,
                            { ...body(form), version: form.version },
                            "PUT",
                          )
                        : await mutate("", body(form));
                      setForm(null);
                      setPlan(null);
                      onDirty(false);
                      await open(r.id);
                      await load();
                      setNotice(
                        edited
                          ? "Собрание изменено, родители получили уведомление."
                          : `Собрание назначено. Приглашено родителей: ${r.invited}.`,
                      );
                    })
                  }
                >
                  {form.id ? "Сохранить изменения" : "Назначить собрание"}
                </button>
              </div>
            )}
          </fieldset>
        </form>
      )}
      {detail && !form && !family && (
        <article className="meeting-class-detail">
          <button disabled={busy} onClick={() => setDetail(null)}>
            ← К списку
          </button>
          <h3>{detail.title}</h3>
          <p>
            {displayTime(detail.startsAt)}–{clockTime(detail.endsAt)} ·{" "}
            {detail.place} · {(detail.classNames || []).join(", ")} ·{" "}
            {labels[detail.status] || detail.status}
          </p>
          {detail.description && <p>{detail.description}</p>}
          {detail.reason && <p>Причина отмены: {detail.reason}</p>}
          {detail.withoutGuardian.length > 0 && (
            <p className="meeting-error">
              Нет аккаунта родителя: {detail.withoutGuardian.join(", ")}.
              Сообщите им лично.
            </p>
          )}
          <div className="meeting-toolbar">
            {detail.canEdit && (
              <button disabled={busy} onClick={() => edit(detail)}>
                Изменить
              </button>
            )}
            {detail.status === "scheduled" && (
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const r = await mutate(`/${detail.id}/sync`, {
                      version: detail.version,
                    });
                    await open(detail.id);
                    setNotice(
                      r.added || r.removed
                        ? `Список обновлён: добавлено ${r.added}, убрано ${r.removed}. Семьи получили уведомление.`
                        : "Состав класса не изменился.",
                    );
                  })
                }
              >
                Обновить список класса
              </button>
            )}
            {detail.canCancel && (
              <button
                disabled={busy}
                onClick={() => void run(() => cancel(detail))}
              >
                Отменить
              </button>
            )}
            <button
              disabled={busy || !detail.roster.length}
              onClick={() => window.print()}
            >
              Печать
            </button>
          </div>
          <table className="meeting-table">
            <thead>
              <tr>
                <th>Ученик</th>
                <th>Класс</th>
                <th>Родители</th>
                <th>Явка</th>
              </tr>
            </thead>
            <tbody>
              {detail.roster.map((r) => (
                <tr key={r.studentId}>
                  <td data-label="Ученик">{r.name}</td>
                  <td data-label="Класс">{r.className}</td>
                  <td data-label="Родители">{r.guardians.join(", ") || "—"}</td>
                  <td data-label="Явка">
                    {detail.canMark ? (
                      <span className="meeting-marks">
                        {(["present", "absent"] as const).map((m) => (
                          <label key={m}>
                            <input
                              type="radio"
                              name={"mark-" + r.studentId}
                              checked={marks[r.studentId] === m}
                              disabled={busy}
                              onChange={() =>
                                setMarks({ ...marks, [r.studentId]: m })
                              }
                            />
                            {labels[m]}
                          </label>
                        ))}
                      </span>
                    ) : (
                      labels[r.mark] || ""
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {detail.canMark && (
            <button
              className="primary"
              disabled={busy || !changed.length}
              onClick={() =>
                void run(async () => {
                  await mutate(`/${detail.id}/marks`, {
                    version: detail.version,
                    marks: changed,
                  });
                  await open(detail.id);
                  setNotice("Явка сохранена.");
                })
              }
            >
              Сохранить явку
            </button>
          )}
          <h4>История</h4>
          <ol>
            {detail.history.map((h, i) => (
              <li key={i}>
                {displayTime(h.created)} · {actions[h.action] || h.action}
                {h.reason ? ` · ${h.reason}` : ""}
              </li>
            ))}
          </ol>
          <MeetingPrint
            title={detail.title}
            subtitle={`${displayTime(detail.startsAt)} · ${detail.place} · ${(detail.classNames || []).join(", ")}`}
            head={["№", "Ученик", "Класс", "Родитель", "Подпись"]}
            rows={detail.roster.map((r, i) => [
              String(i + 1),
              r.name,
              r.className,
              r.guardians.join(", "),
              "",
            ])}
          />
        </article>
      )}
      {!detail && !form && (
        <div className="class-meeting-list">
          {items.map((m) => (
            <article key={m.id}>
              <h4>{m.title}</h4>
              <p>
                {displayTime(m.startsAt)}–{clockTime(m.endsAt)} · {m.place}
              </p>
              <p>
                {labels[m.status] || m.status}
                {m.classNames
                  ? ` · ${m.classNames.join(", ")} · приглашено: ${m.pupils}`
                  : ""}
                {m.marked ? ` · отмечено: ${m.marked}` : ""}
              </p>
              {m.students && (
                <p>Ваши дети: {m.students.map((s) => s.name).join(", ")}</p>
              )}
              {m.description && <p>{m.description}</p>}
              {m.reason && <p>Причина отмены: {m.reason}</p>}
              {!family && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => open(m.id))}
                >
                  Открыть
                </button>
              )}
            </article>
          ))}
          {!items.length && !busy && (
            <p>
              {family
                ? "Приглашений на собрания классов пока нет."
                : "Собраний классов пока нет."}
            </p>
          )}
          {next && (
            <button disabled={busy} onClick={() => void run(() => load(next))}>
              Ещё собрания
            </button>
          )}
        </div>
      )}
    </section>
  );
}
