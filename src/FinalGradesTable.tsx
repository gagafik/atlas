import { useEffect, useId, useRef, useState } from "react";
import { api } from "./api";
import {
  changedRows,
  chunked,
  finalLabel,
  names,
  needsExplanation,
  tablePrefill,
  tableScale,
  type TableEntry,
} from "./finalGrades";
import type { Row, Scale, Sheet } from "./finalGradesTypes";

const percent = (value: string | null | undefined) =>
  value == null
    ? "нет"
    : `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} %`;
const selectValue = (e: TableEntry) =>
  e.outcome !== "graded" ? `o:${e.outcome}` : e.label ? `g:${e.label}` : "";
const SKIPPED: Record<string, string> = {
  none: "без рекомендации",
  manual: "уже выбран другой итог",
  status: "не черновик",
};

// One class and subject as a table: the computed grade is already selected, nothing is saved until a button is pressed.
export default function FinalGradesTable({
  workspace,
  bookId,
  classId,
  subjectId,
  scales,
  phrases,
  current,
  lastScale,
  onDirty,
  onChanged,
}: {
  workspace: string;
  bookId: string;
  classId: string;
  subjectId: string;
  scales: Scale[];
  phrases: string[];
  current: boolean;
  lastScale: { current: string };
  onDirty: (dirty: boolean) => void;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<Row[]>([]),
    [scaleId, setScaleId] = useState(""),
    [values, setValues] = useState<Record<string, TableEntry>>({}),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const lock = useRef(false),
    generation = useRef(0),
    inputs = useRef<Record<string, HTMLInputElement | null>>({}),
    requests = useRef(new Map<string, string>()),
    list = useId();
  const scale = scales.find((s) => s.id === scaleId && s.active);

  async function load(id: string) {
    const all: Row[] = [];
    let offset = 0;
    for (;;) {
      const page: Sheet = await api(
        `final-grades/books/${bookId}?${new URLSearchParams({ classId, subjectId, ...(id ? { scaleId: id } : {}), offset: String(offset) })}`,
        workspace,
      );
      all.push(...page.items);
      if (page.nextOffset === null) return all;
      offset = page.nextOffset;
    }
  }
  function show(loaded: Row[], chosen: Scale | undefined) {
    setRows(loaded);
    setScaleId(chosen?.id || "");
    setValues(
      chosen
        ? Object.fromEntries(loaded.map((r) => [r.id, tablePrefill(r, chosen)]))
        : {},
    );
    setDirty(false);
    onDirty(false);
  }
  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : "Не удалось выполнить действие.");
  }
  // The scale decides the advice, so the class is read once with the likely scale and again only if the saved results point elsewhere.
  async function init() {
    const mine = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const active = scales.filter((s) => s.active);
      const guess =
        active.find((s) => s.id === lastScale.current) ||
        (active.length === 1 ? active[0] : undefined);
      let loaded = await load(guess?.id || "");
      const chosen = tableScale(loaded, scales, lastScale.current);
      if (chosen && chosen.id !== guess?.id) loaded = await load(chosen.id);
      if (mine === generation.current) show(loaded, chosen);
    } catch (e) {
      if (mine === generation.current) fail(e);
    } finally {
      if (mine === generation.current) setBusy(false);
    }
  }
  useEffect(() => {
    void init();
    return () => {
      generation.current++;
      onDirty(false);
    };
  }, [bookId, classId, subjectId]);

  async function run(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
      lock.current = false;
    }
  }
  async function reload(id: string) {
    show(
      await load(id),
      scales.find((s) => s.id === id && s.active),
    );
  }
  function edit(id: string, patch: Partial<TableEntry>) {
    setValues((v) => ({ ...v, [id]: { ...v[id], ...patch } }));
    setDirty(true);
    onDirty(true);
  }
  function choose(id: string, value: string) {
    if (value.startsWith("o:"))
      edit(id, { outcome: value.slice(2), label: "" });
    else edit(id, { outcome: "graded", label: value.slice(2) });
  }
  // A retry of the same request reuses its id, so a lost response is never saved twice.
  function requestId(key: string) {
    let id = requests.current.get(key);
    if (!id) requests.current.set(key, (id = crypto.randomUUID()));
    return id;
  }
  async function post(kind: string, body: Record<string, unknown>) {
    const key = kind + JSON.stringify(body);
    const result = await api(
      `final-grades/books/${bookId}/${kind}`,
      workspace,
      "POST",
      { requestId: requestId(key), ...body },
    );
    requests.current.delete(key);
    return result;
  }
  async function pick(id: string) {
    if (
      dirty &&
      !window.confirm(
        "Несохранённые изменения будут потеряны: итоги пересчитаются по новой шкале. Продолжить?",
      )
    )
      return;
    await run(() => reload(id));
  }
  async function accept() {
    const drafts = rows.filter((r) => r.status === "draft");
    if (!scale || !drafts.length) return;
    if (
      dirty &&
      !window.confirm(
        "Введённые, но не сохранённые итоги будут заменены рекомендациями. Продолжить?",
      )
    )
      return;
    await run(async () => {
      let saved = 0;
      const skipped: Record<string, number> = {};
      for (const part of chunked(drafts, 100)) {
        const r = await post("accept", {
          scaleId: scale.id,
          scaleVersion: scale.version,
          rows: part.map((x) => ({ id: x.id, version: x.version })),
          submit: false,
          overwrite: false,
        });
        saved += r.items.length;
        for (const s of r.skipped)
          skipped[s.reason] = (skipped[s.reason] || 0) + 1;
      }
      lastScale.current = scale.id;
      await reload(scale.id);
      onChanged();
      const left = Object.entries(skipped).map(
        ([reason, n]) => `${SKIPPED[reason] || reason} — ${n}`,
      );
      setNotice(
        `Принято рекомендаций: ${saved}.${left.length ? ` Пропущено: ${left.join("; ")}.` : ""}`,
      );
    });
  }
  async function save(submit: boolean) {
    if (!scale) return;
    const todo = changedRows(rows, values, scale);
    if (!todo.length) {
      setError("");
      setNotice("Нет изменений для сохранения.");
      return;
    }
    const bad = todo.find(
      (t) =>
        needsExplanation(t, rows.find((r) => r.id === t.id)?.advice, scale) &&
        t.explanation.trim().length < 3,
    );
    if (bad) {
      const name = rows.find((r) => r.id === bad.id)?.source.student.name;
      setNotice("");
      setError(
        `${name}: объясните итог без рекомендации или отклонение от неё (минимум 3 символа).`,
      );
      inputs.current[bad.id]?.focus();
      return;
    }
    await run(async () => {
      let done = 0;
      try {
        for (const part of chunked(todo, 100)) {
          const r = await post("proposals", {
            scaleId: scale.id,
            scaleVersion: scale.version,
            submit,
            rows: part.map(
              ({ id, version, outcome, label, comment, explanation }) => ({
                id,
                version,
                outcome,
                label,
                comment,
                explanation,
              }),
            ),
          });
          done += r.items.length;
        }
      } catch (e) {
        // Typed values stay in the table; the user decides when to reload.
        throw new Error(
          `${done ? `Сохранено ${done} из ${todo.length}, остальные строки не изменены. ` : ""}${e instanceof Error ? e.message : ""} Нажмите «Обновить таблицу», чтобы загрузить актуальные данные.`,
        );
      }
      lastScale.current = scale.id;
      await reload(scale.id);
      onChanged();
      setNotice(
        submit
          ? `Отправлено на утверждение: ${done}.`
          : `Черновики сохранены: ${done}.`,
      );
    });
  }

  const editable = current && !!scale;
  return (
    <div className="final-table-block">
      {!current && (
        <p className="error">
          Ведомость устарела: итоги можно смотреть, но не менять.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка таблицы…</p>}
      <div className="dated-toolbar">
        <label className="field">
          Шкала
          <select
            value={scaleId}
            disabled={busy || !current}
            onChange={(e) => void pick(e.target.value)}
          >
            <option value="">Выберите шкалу</option>
            {scales
              .filter((s) => s.active)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
        <button disabled={busy} onClick={() => void run(() => reload(scaleId))}>
          Обновить таблицу
        </button>
        {editable && (
          <>
            <button
              disabled={busy || !rows.some((r) => r.status === "draft")}
              onClick={() => void accept()}
            >
              Принять рекомендации
            </button>
            <button disabled={busy} onClick={() => void save(false)}>
              Сохранить черновики
            </button>
            <button disabled={busy} onClick={() => void save(true)}>
              Сохранить и отправить
            </button>
          </>
        )}
      </div>
      {current && !scale && !busy && (
        <p>
          Выберите шкалу: без неё рекомендации не считаются и итоги нельзя
          сохранить.
        </p>
      )}
      <p>
        Выбранные оценки подставлены по расчёту и ещё не сохранены. Обоснование
        нужно, когда итог отличается от рекомендации, а также для неаттестации и
        освобождения.
      </p>
      {!rows.length && !busy && <p>В этом классе по предмету нет строк.</p>}
      {!!rows.length && (
        <div className="final-table-wrap">
          <datalist id={list}>
            {phrases.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <table className="final-table">
            <thead>
              <tr>
                <th>Ученик</th>
                <th>Работ</th>
                <th>Процент</th>
                <th>Рекомендация</th>
                <th>Итог</th>
                <th>Словесная оценка</th>
                <th>Обоснование</th>
                <th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const v = values[r.id];
                const open = editable && r.status === "draft" && !!v;
                const name = r.source.student.name;
                const advice = r.advice;
                return (
                  <tr key={r.id}>
                    <th scope="row">{name}</th>
                    <td>{r.source.numeric}</td>
                    <td>
                      {advice?.basis === "periods" && advice.points
                        ? `ср. балл ${advice.points.replace(".", ",")}`
                        : advice
                          ? percent(advice.roundedPercent ?? advice.rawPercent)
                          : "—"}
                    </td>
                    <td>{advice ? advice.recommended || "нет" : "—"}</td>
                    {open && scale ? (
                      <>
                        <td>
                          <select
                            aria-label={`Итог: ${name}`}
                            value={selectValue(v)}
                            onChange={(e) => choose(r.id, e.target.value)}
                          >
                            <option value="">Не выбран</option>
                            {scale.bands.map((b) => (
                              <option key={b.label} value={`g:${b.label}`}>
                                {b.label}
                              </option>
                            ))}
                            <option value="o:not_assessed">
                              {names.not_assessed}
                            </option>
                            <option value="o:exempt">{names.exempt}</option>
                          </select>
                        </td>
                        <td>
                          <input
                            aria-label={`Словесная оценка: ${name}`}
                            list={list}
                            maxLength={1000}
                            value={v.comment}
                            onChange={(e) =>
                              edit(r.id, { comment: e.target.value })
                            }
                          />
                        </td>
                        <td>
                          {needsExplanation(v, advice, scale) ? (
                            <input
                              aria-label={`Обоснование: ${name}`}
                              ref={(el) => {
                                inputs.current[r.id] = el;
                              }}
                              maxLength={1000}
                              value={v.explanation}
                              onChange={(e) =>
                                edit(r.id, { explanation: e.target.value })
                              }
                            />
                          ) : (
                            "не требуется"
                          )}
                        </td>
                      </>
                    ) : (
                      <>
                        <td>{r.proposal ? finalLabel(r.proposal) : "—"}</td>
                        <td>{r.proposal?.comment || "—"}</td>
                        <td>{r.proposal?.explanation || "—"}</td>
                      </>
                    )}
                    <td>
                      {names[r.status]}
                      {r.proposal?.carriedFrom && r.status === "draft" && (
                        <span className="error">
                          {" "}
                          Итог перенесён из прошлого закрытия
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
