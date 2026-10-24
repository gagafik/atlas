import { useEffect, useState } from "react";
import { api } from "./api";
import { named } from "./utils";
import type { State } from "./types";
import ChangePreview, { type Preview } from "./ChangePreview";
import {
  factorLine,
  type MergeOption,
  type RankedTeacher,
} from "./substitutions";

type Action = "substitution" | "merge" | "self_study" | "cancel" | "none";
interface PlanLesson {
  row: {
    id: string;
    start: string;
    end: string;
    group: string;
    subject: string;
    rooms: string;
    teacherIds: string[];
    changeType: string | null;
    actions: Action[];
    note: string | null;
  };
  proposal: {
    action: Action;
    teacherId?: string;
    mergeWith?: string;
    name?: string;
    score?: number;
    reason?: string;
  };
  candidates: RankedTeacher[];
  busyCount: number;
  mergeOptions: MergeOption[];
}
interface Plan {
  revision: number;
  day: string;
  teacherId: string;
  absences: {
    id: string;
    name: string;
    start: string;
    end: string;
    startTime: string;
    endTime: string;
  }[];
  lessons: PlanLesson[];
}
interface Choice {
  action: Action;
  teacherId: string;
  mergeWith: string;
}
const ACTIONS: Record<Action, string> = {
  substitution: "Замена",
  merge: "Объединение",
  self_study: "Самостоятельная работа",
  cancel: "Отмена урока",
  none: "Оставить как есть",
};
const none: Choice = { action: "none", teacherId: "", mergeWith: "" };

// Proposes a replacement for every lesson of an absent teacher on one date, then
// checks and stores all chosen changes together through the ordinary editor preview.
export default function SubstitutionPlan({
  state: s,
  workspace,
  onState,
  day,
  initialTeacher,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
  day: string;
  initialTeacher?: string;
}) {
  const [teacher, setTeacher] = useState(initialTeacher || "");
  const [plan, setPlan] = useState<Plan | null>(null),
    [choices, setChoices] = useState<Record<string, Choice>>({}),
    [name, setName] = useState("Замена");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (initialTeacher) setTeacher(initialTeacher);
  }, [initialTeacher]);
  useEffect(() => {
    setPlan(null);
    setPreview(null);
    setChoices({});
    setError("");
  }, [workspace, s.revision, day, teacher]);
  async function load() {
    if (!teacher || busy) return;
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const r: Plan = await api("calendar/day-plan", workspace, "POST", {
        revision: s.revision,
        day,
        teacherId: teacher,
      });
      setPlan(r);
      setChoices(
        Object.fromEntries(
          r.lessons.map((l) => [
            l.row.id,
            l.proposal.action === "substitution" ||
            l.proposal.action === "merge"
              ? {
                  action: l.proposal.action,
                  teacherId: l.proposal.teacherId || "",
                  mergeWith: l.proposal.mergeWith || "",
                }
              : none,
          ]),
        ),
      );
      setName(
        (r.absences[0] ? `Замена: ${r.absences[0].name}` : "Замена").slice(
          0,
          160,
        ),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const assignments = (plan?.lessons || [])
    .map((l) => ({ l, c: choices[l.row.id] || none }))
    .filter(({ c }) => c.action !== "none")
    .map(({ l, c }) => ({
      rowId: l.row.id,
      action: c.action,
      teacherId:
        c.action === "substitution" || c.action === "self_study"
          ? c.teacherId || null
          : null,
      mergeWith: c.action === "merge" ? c.mergeWith || null : null,
    }));
  const incomplete = assignments.some(
    (a) =>
      ((a.action === "substitution" || a.action === "self_study") &&
        !a.teacherId) ||
      (a.action === "merge" && !a.mergeWith),
  );
  async function checkPlan() {
    if (busy || !assignments.length || incomplete) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        await api("calendar/day-plan/preview", workspace, "POST", {
          revision: s.revision,
          day,
          teacherId: teacher,
          name: name.trim() || "Замена",
          assignments,
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview || busy) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api("editor/apply", workspace, "POST", {
        revision: preview.revision,
        token: preview.token,
      });
      await onState(saved);
      setPreview(null);
      setPlan(null);
      setChoices({});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const set = (id: string, patch: Partial<Choice>) =>
    setChoices({ ...choices, [id]: { ...(choices[id] || none), ...patch } });
  return (
    <div className="substitution-plan">
      <p className="info-line">
        Выберите отсутствующего учителя: «Атлас» предложит замену для каждого
        его урока на дату {day}, учитывая занятость, отсутствия и баллы подбора.
        Ничего не сохраняется, пока вы не проверите весь план и не примените его
        одним действием.
      </p>
      <div className="dated-fields">
        <label className="field">
          <span>Отсутствующий учитель</span>
          <select value={teacher} onChange={(e) => setTeacher(e.target.value)}>
            <option value="">Выберите учителя</option>
            {s.teachers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>Дата</span>
          <strong>{day}</strong>
          <small>Меняется в поле «Дата расписания» выше.</small>
        </div>
      </div>
      <div className="modal-actions">
        <button className="primary" disabled={busy || !teacher} onClick={load}>
          Подобрать замены на день
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {preview ? (
        <ChangePreview
          preview={preview}
          busy={busy}
          onApply={apply}
          onCancel={() => setPreview(null)}
          title="Проверка замен на день"
        />
      ) : (
        plan && (
          <>
            <p className="info-line">
              {plan.absences.length
                ? `Отсутствие: ${plan.absences.map((a) => `${a.name} · ${a.startTime}–${a.endTime}`).join("; ")}.`
                : "Отсутствие не внесено — уроки всё равно можно распределить."}
            </p>
            {!plan.lessons.length && (
              <p className="empty-state">У учителя нет уроков в этот день.</p>
            )}
            {plan.lessons.map((l) => {
              const c = choices[l.row.id] || none;
              const pick = l.candidates.find((x) => x.id === c.teacherId);
              const merge = l.mergeOptions.find((o) => o.rowId === c.mergeWith);
              const rest = s.teachers.filter(
                (t) =>
                  t.id !== teacher &&
                  !l.row.teacherIds.includes(t.id) &&
                  !l.candidates.some((x) => x.id === t.id),
              );
              return (
                <article key={l.row.id}>
                  <div className="dated-time">
                    {l.row.start}–{l.row.end}
                    <small>{l.row.rooms || "Кабинет не назначен"}</small>
                  </div>
                  <div>
                    <h3>
                      {l.row.subject} · {l.row.group}
                    </h3>
                    {l.row.note && <small>{l.row.note}</small>}
                    <label className="field">
                      <span>Действие</span>
                      <select
                        value={c.action}
                        onChange={(e) =>
                          set(l.row.id, { action: e.target.value as Action })
                        }
                      >
                        {l.row.actions.map((a) => (
                          <option key={a} value={a}>
                            {ACTIONS[a]}
                          </option>
                        ))}
                      </select>
                    </label>
                    {l.proposal.action === "none" && c.action === "none" && (
                      <small>{l.proposal.reason}</small>
                    )}
                    {c.action === "substitution" && (
                      <label className="field">
                        <span>Кто заменяет</span>
                        <select
                          value={c.teacherId}
                          onChange={(e) =>
                            set(l.row.id, { teacherId: e.target.value })
                          }
                        >
                          <option value="">Выберите учителя</option>
                          {l.candidates.map((x) => (
                            <option key={x.id} value={x.id}>
                              {`${x.name} · ${x.score} балл.${x.checked ? "" : " · не проверен полностью"}${x.available ? "" : " · занят"}`}
                            </option>
                          ))}
                          {!!rest.length && (
                            <optgroup label="Остальные учителя (без оценки)">
                              {rest.map((t) => (
                                <option key={t.id} value={t.id}>
                                  {t.name}
                                </option>
                              ))}
                            </optgroup>
                          )}
                        </select>
                        {pick && (
                          <small>
                            {pick.factors.map(factorLine).join(" · ")}
                          </small>
                        )}
                        {pick && !pick.available && (
                          <small>{pick.reasons.join("; ")}</small>
                        )}
                        {l.busyCount > 0 && (
                          <small>
                            Занятых или отсутствующих учителей: {l.busyCount}.
                          </small>
                        )}
                      </label>
                    )}
                    {c.action === "self_study" && (
                      <label className="field">
                        <span>Ответственный сотрудник</span>
                        <select
                          value={c.teacherId}
                          onChange={(e) =>
                            set(l.row.id, { teacherId: e.target.value })
                          }
                        >
                          <option value="">Выберите сотрудника</option>
                          {s.teachers
                            .filter((t) => t.id !== teacher)
                            .map((t) => (
                              <option key={t.id} value={t.id}>
                                {t.name}
                              </option>
                            ))}
                        </select>
                      </label>
                    )}
                    {c.action === "merge" && (
                      <label className="field">
                        <span>Присоединить к уроку</span>
                        <select
                          value={c.mergeWith}
                          onChange={(e) =>
                            set(l.row.id, { mergeWith: e.target.value })
                          }
                        >
                          <option value="">
                            {l.mergeOptions.length
                              ? "Выберите урок"
                              : "В это время других уроков нет"}
                          </option>
                          {l.mergeOptions.map((o) => (
                            <option key={o.rowId} value={o.rowId}>
                              {o.start}–{o.end} · {o.group} · {o.subject} ·{" "}
                              {o.teachers || "без учителя"}
                              {o.fits === false ? " · не вмещает" : ""}
                            </option>
                          ))}
                        </select>
                        {merge && (
                          <small>
                            Вместимость: {merge.capacity ?? "не задана"},
                            участников {merge.attendance ?? "неизвестно"}.
                            Преподаватель и кабинет берутся у выбранного урока (
                            {named(s.teachers, merge.teacherIds[0])}).
                          </small>
                        )}
                      </label>
                    )}
                    {c.action === "cancel" && (
                      <small>Урок будет отменён на эту дату.</small>
                    )}
                  </div>
                </article>
              );
            })}
            {!!plan.lessons.length && (
              <>
                <label className="field">
                  <span>Причина / примечание</span>
                  <input
                    required
                    maxLength={160}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <div className="modal-actions">
                  <button
                    className="primary"
                    disabled={busy || !assignments.length || incomplete}
                    onClick={checkPlan}
                  >
                    Проверить весь план
                  </button>
                </div>
                {incomplete && (
                  <p className="muted">
                    Для замены и самостоятельной работы выберите сотрудника, для
                    объединения — урок.
                  </p>
                )}
              </>
            )}
          </>
        )
      )}
    </div>
  );
}
