import { useEffect, useState } from "react";
import { api } from "./api";
import { defaultDraft, finalLabel, names, needsReason } from "./finalGrades";
import FinalSelect from "./FinalSelect";
import { ROW_ACTIONS, rowActionAllowed } from "./reportCardsView";
import { unsavedMessage } from "./useUnsavedGuard";
import { useBusy } from "./useBusy";
import CategoryBadge from "./CategoryBadge";
import { categoryOf, type CategoryConfig } from "./assessmentCategories";
import type {
  Calculation,
  Evidence,
  History,
  Named,
  Proposal,
  Row,
  Scale,
} from "./finalGradesTypes";

const ACTION_LABELS: Record<string, string> = {
  approve: "Утвердить",
  return: "Вернуть на исправление",
  publish: "Опубликовать",
  reopen: "Открыть исправление",
  withdraw: "Отозвать публикацию",
};
const percentText = (value: string) =>
  Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 });

// How a recommendation was reached: the share of every category, the method and the rule that was pinned with the book.
export function CalculationDetails({ value }: { value: Calculation }) {
  if (value.basis === "periods")
    return (
      <>
        <p>
          Годовая по итогам периодов:{" "}
          {(value.periods ?? [])
            .map((p) => `${p.name} — ${p.label || "нет оценки"}`)
            .join("; ")}
          ; среднее {value.points?.replace(".", ",")}.
          {!!value.missing?.length &&
            ` Нет итога за: ${value.missing.join(", ")}.`}
        </p>
        {!!value.periods?.some((p) => !p.current) && (
          <p className="error">
            Итог одного из периодов опубликован по более раннему закрытию:
            период открывали заново. Проверьте его перед утверждением.
          </p>
        )}
      </>
    );
  if (!value.categories) return null;
  const missing = (value.missing ?? []).map(
    (id) => value.categories?.find((c) => c.id === id)?.name ?? id,
  );
  return (
    <>
      <p className="final-categories">
        {value.categories.map((c) => (
          <span key={c.id}>
            <CategoryBadge category={c} />
            {percentText(c.share)} % —{" "}
            {c.percent === null ? "нет оценок" : `${percentText(c.percent)} %`}
            ;{" "}
          </span>
        ))}
      </p>
      <p>
        Метод:{" "}
        {value.method === "points"
          ? "сумма баллов"
          : "среднее процентов с весом"}
        . Правило: {value.rule}.
        {!!missing.length && ` Нет оценок в категориях: ${missing.join(", ")}.`}
        {value.blocked &&
          " Правило требует оценок во всех категориях, поэтому рекомендации нет."}
        {!!value.excluded?.length &&
          " Оценки категорий без доли в расчёт не входят."}
      </p>
    </>
  );
}

// One final grade: the editor of the proposal, the evidence from the closing archive, the history and, for the administrator,
// the actions on this single row. The unsaved draft is reported to the screen under the key «row».
export default function FinalGradeRowDetails({
  workspace,
  row: handle,
  scales,
  teachers,
  admin,
  current,
  readOnly = false,
  categories,
  lastScale,
  onDirty,
  onChanged,
  onClose,
}: {
  workspace: string;
  // The row as the list or the table knows it; the details read it again.
  row: Row;
  scales: Scale[];
  teachers: Named[];
  admin: boolean;
  // False for a statement of an older closure and for an archived year: nothing is saved there.
  current: boolean;
  // An archived school year: the actions on the row are not offered at all.
  readOnly?: boolean;
  categories: CategoryConfig | null;
  lastScale: { current: string };
  onDirty: (table: string, dirty: boolean) => void;
  onChanged: () => void;
  onClose: () => void;
}) {
  const { busy, error, notice, setNotice, run } = useBusy();
  const [row, setRow] = useState<Row | null>(null),
    [marks, setMarks] = useState<Evidence[]>([]),
    [nextMark, setNextMark] = useState<number | null>(null),
    [history, setHistory] = useState<History[]>([]),
    [nextHistory, setNextHistory] = useState<number | null>(null),
    [calculations, setCalculations] = useState<Record<string, Calculation>>({}),
    [draft, setDraft] = useState({
      scaleId: "",
      scaleVersion: 0,
      outcome: "graded",
      label: "",
      comment: "",
      explanation: "",
    }),
    [dirty, setDirty] = useState(false),
    [teacherId, setTeacherId] = useState(handle.assigned),
    [reason, setReason] = useState("");

  async function load(id: string) {
    const r = await api(`final-grades/rows/${id}/evidence`, workspace);
    setRow(r.row);
    setCalculations(r.recommendations);
    setMarks(r.items);
    setNextMark(r.nextAfter);
    setHistory([]);
    setNextHistory(null);
    setTeacherId(r.row.assigned);
    const p = r.row.proposal as Proposal | null;
    setDraft(defaultDraft(p, scales, r.recommendations, lastScale.current));
    setDirty(false);
  }
  useEffect(() => {
    void run(() => load(handle.id));
  }, [handle.id]);
  useEffect(() => {
    onDirty("row", dirty);
  }, [dirty]);
  useEffect(() => () => onDirty("row", false), []);

  const activeScale = scales.find(
    (s) =>
      s.id === draft.scaleId && s.version === draft.scaleVersion && s.active,
  );
  const draftScale =
    activeScale ||
    (row?.proposal?.scale.id === draft.scaleId
      ? row.proposal.scale
      : undefined);
  const calculation =
    calculations[draft.scaleId + ":" + draft.scaleVersion] ||
    (row?.proposal?.scale.id === draft.scaleId &&
    row.proposal.scale.version === draft.scaleVersion
      ? row.proposal.calculation
      : undefined);
  const updateDraft = (update: Partial<typeof draft>) => {
    setDraft({ ...draft, ...update });
    setDirty(true);
  };
  async function act(action: string) {
    if (!row) return;
    if (dirty && !window.confirm(unsavedMessage(["row"]))) return;
    if (
      action === "publish" &&
      !window.confirm(
        `Опубликовать итог (${row.source.student.name}, ${row.source.subject}) ученику и родителям?`,
      )
    )
      return;
    await run(async () => {
      await api("final-grades/actions", workspace, "POST", {
        action,
        rows: [{ id: row.id, version: row.version }],
        teacherId: action === "assign" ? teacherId : "",
        reason: needsReason(action) ? reason.trim() : "",
      });
      setReason("");
      await load(row.id);
      onChanged();
      setNotice(`${names[action] || action}: выполнено.`);
    });
  }
  async function save(submit: boolean) {
    if (!row) return;
    await run(async () => {
      const r = await api(`final-grades/rows/${row.id}`, workspace, "PUT", {
        ...draft,
        version: row.version,
        submit,
      });
      lastScale.current = draft.scaleId;
      await load(r.id);
      onChanged();
      setNotice(
        submit ? "Итог отправлен на утверждение." : "Черновик сохранён.",
      );
    });
  }
  return (
    <article
      className="absence-card final-grade-editor"
      aria-label={`Итог: ${handle.source.student.name}, ${handle.source.subject}`}
    >
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <div className="finals-details-head">
        <h3>
          {handle.source.student.name} · {handle.source.subject}
        </h3>
        <button onClick={onClose}>Закрыть</button>
      </div>
      {row && (
        <>
          <p>
            {row.source.student.className} · {names[row.status]}
            {row.proposal ? ` · ${finalLabel(row.proposal)}` : ""}
            {admin &&
              ` · Ответственный: ${
                teachers.find((t) => t.id === row.assigned)?.name ||
                (row.assigned ? "Учётная запись недоступна" : "Не назначен")
              }`}
          </p>
          {row.published && (
            <p>
              В табеле: {finalLabel(row.published)}
              {row.status !== "published"
                ? " — сохраняется до новой публикации"
                : ""}
            </p>
          )}
          <p>
            Запланировано уроков: {row.source.expected}; опубликовано:{" "}
            {row.source.published}. Учтено работ с баллами: {row.source.numeric}
            ; текстовых: {row.source.text}; без отметки: {row.source.missing}; с
            нулевым весом: {row.source.zeroWeight}.
          </p>
          {(!row.source.expected ||
            row.source.incompleteRoster > 0 ||
            row.source.published < row.source.expected) && (
            <p className="error">
              Есть пробелы или расхождения с расписанием. Проверьте исходные
              журналы перед утверждением.
            </p>
          )}
          <fieldset disabled={busy || row.status !== "draft" || !current}>
            <FinalSelect
              label="Шкала для итога"
              value={draft.scaleId}
              items={scales.filter((s) => s.active || s.id === draft.scaleId)}
              onChange={(id) => {
                const s = scales.find((s) => s.id === id);
                updateDraft({
                  scaleId: id,
                  scaleVersion: s?.version || 0,
                  label:
                    (s && calculations[`${id}:${s.version}`]?.recommended) ||
                    "",
                });
              }}
            />
            {!activeScale && draft.scaleId && (
              <p className="error">
                Сохранённая редакция шкалы недоступна для новой записи. Выберите
                актуальную шкалу.
              </p>
            )}
            {activeScale && !calculation && (
              <button
                onClick={() =>
                  void run(async () => {
                    const r = await api(
                      `final-grades/rows/${row.id}/evidence`,
                      workspace,
                    );
                    setCalculations(r.recommendations);
                  })
                }
              >
                Рассчитать рекомендацию
              </button>
            )}
            {calculation && (
              <p>
                Рекомендация: {calculation.recommended || "нет"}. Процент:{" "}
                {calculation.rawPercent || "нет"}; после округления:{" "}
                {calculation.roundedPercent || "нет"}. Минимум работ:{" "}
                {draftScale?.minCount}.
              </p>
            )}
            {calculation && <CalculationDetails value={calculation} />}
            <label className="field">
              Вид итога
              <select
                value={draft.outcome}
                onChange={(e) =>
                  updateDraft({
                    outcome: e.target.value,
                    label:
                      e.target.value === "graded"
                        ? calculation?.recommended || ""
                        : "",
                  })
                }
              >
                {["graded", "not_assessed", "exempt"].map((s) => (
                  <option value={s} key={s}>
                    {names[s]}
                  </option>
                ))}
              </select>
            </label>
            {draft.outcome === "graded" && (
              <FinalSelect
                label="Итоговая оценка"
                value={draft.label}
                onChange={(label) => updateDraft({ label })}
                items={(draftScale?.bands || []).map((b) => ({
                  id: b.label,
                  name: b.label,
                }))}
              />
            )}
            <label className="field">
              Комментарий ученику и родителям
              <textarea
                value={draft.comment}
                maxLength={1000}
                onChange={(e) => updateDraft({ comment: e.target.value })}
              />
            </label>
            <label className="field">
              Обоснование для школы
              <textarea
                value={draft.explanation}
                maxLength={1000}
                onChange={(e) => updateDraft({ explanation: e.target.value })}
              />
            </label>
            <p>
              Обоснование обязательно при отклонении от рекомендации, отсутствии
              рекомендации по пороговой шкале, неаттестации или освобождении.
              Для шкалы с ручным выбором оценки оно необязательно. Оно не
              публикуется семье.
            </p>
            <div className="dated-toolbar">
              {[false, true].map((submit) => (
                <button
                  key={String(submit)}
                  disabled={!activeScale || !draft.scaleId}
                  onClick={() => void save(submit)}
                >
                  {submit ? "Отправить на утверждение" : "Сохранить черновик"}
                </button>
              ))}
            </div>
          </fieldset>
          {row.proposal?.carriedFrom && row.status === "draft" && (
            <p className="error">
              Итог перенесён из прошлого закрытия. Проверьте работы и отправьте
              заново.
            </p>
          )}
          {row.proposal && (
            <p>
              Сохранённый итог: {finalLabel(row.proposal)}. Рекомендация:{" "}
              {row.proposal.calculation.recommended || "нет"}. Процент:{" "}
              {row.proposal.calculation.rawPercent || "нет"}; после округления:{" "}
              {row.proposal.calculation.roundedPercent || "нет"}. Шкала «
              {row.proposal.scale.name}», редакция {row.proposal.scale.version}.{" "}
              {row.proposal.approval &&
                `Утвердил: ${row.proposal.approval.actor}.`}
            </p>
          )}
          {row.proposal && (
            <CalculationDetails value={row.proposal.calculation} />
          )}
          {admin && !readOnly && (
            <section className="finals-row-actions" aria-label="Действия">
              <h4>Действия над итогом</h4>
              <div className="dated-toolbar">
                <FinalSelect
                  label="Ответственный учитель"
                  value={teacherId}
                  onChange={setTeacherId}
                  items={teachers}
                  empty="Снять назначение"
                />
                <button
                  disabled={
                    busy || !rowActionAllowed("assign", row.status, current)
                  }
                  onClick={() => void act("assign")}
                >
                  Назначить
                </button>
              </div>
              <label className="field">
                Причина возврата, исправления или отзыва
                <input
                  value={reason}
                  maxLength={1000}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <div className="dated-toolbar">
                {ROW_ACTIONS.map((action) => (
                  <button
                    key={action}
                    disabled={
                      busy ||
                      !rowActionAllowed(action, row.status, current) ||
                      (needsReason(action) && reason.trim().length < 3)
                    }
                    onClick={() => void act(action)}
                  >
                    {ACTION_LABELS[action]}
                  </button>
                ))}
              </div>
            </section>
          )}
          <h4>Работы из архива закрытия</h4>
          {!marks.length && <p>Проверочных работ нет.</p>}
          <div className="final-evidence">
            {marks.map((m) => (
              <p key={m.id}>
                {m.date}, {m.start} ·{" "}
                <CategoryBadge category={categoryOf(m, categories)} />
                {m.assessment}:{" "}
                {m.score !== null
                  ? `${m.score} / ${m.maxScore}`
                  : m.label || "Нет отметки"}{" "}
                · вес {m.weight}
              </p>
            ))}
          </div>
          {nextMark !== null && (
            <button
              onClick={() =>
                void run(async () => {
                  const r = await api(
                    `final-grades/rows/${row.id}/evidence?after=${nextMark}`,
                    workspace,
                  );
                  setMarks([...marks, ...r.items]);
                  setNextMark(r.nextAfter);
                })
              }
            >
              Ещё работы
            </button>
          )}
          <button
            onClick={() =>
              void run(async () => {
                const r = await api(
                  `final-grades/rows/${row.id}/history`,
                  workspace,
                );
                setHistory(r.items);
                setNextHistory(r.nextBefore);
              })
            }
          >
            История итоговой оценки
          </button>
          {history.map((h) => (
            <details key={h.id}>
              <summary>
                {names[h.action] || h.action} · {h.actor} ·{" "}
                {new Date(h.created).toLocaleString("ru-RU")}
              </summary>
              <p>{h.reason}</p>
              {h.row.proposal && (
                <>
                  <p>
                    {finalLabel(h.row.proposal)} · {h.row.proposal.scale.name},
                    редакция {h.row.proposal.scale.version}
                  </p>
                  <p>{h.row.proposal.explanation}</p>
                </>
              )}
            </details>
          ))}
          {nextHistory !== null && (
            <button
              onClick={() =>
                void run(async () => {
                  const r = await api(
                    `final-grades/rows/${row.id}/history?before=${nextHistory}`,
                    workspace,
                  );
                  setHistory([...history, ...r.items]);
                  setNextHistory(r.nextBefore);
                })
              }
            >
              Ещё события итоговой оценки
            </button>
          )}
        </>
      )}
    </article>
  );
}
