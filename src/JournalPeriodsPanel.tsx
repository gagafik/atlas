import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  attendanceNames,
  documentRows,
  gradeLabel,
  journalCsv,
  type JournalDocument,
} from "./journal";

type Period = {
  id: string;
  name: string;
  start: string;
  end: string;
  kind: string;
  version: number;
  status: string;
  everClosed: boolean;
};
type Options = {
  admin: boolean;
  items: Period[];
  yearStart: string;
  yearEnd: string;
};
type Preview = {
  previewId: string;
  expires: string;
  counts: Record<string, number>;
  published: number;
  draftChanges: number;
  requiresAcknowledgement: boolean;
  canClose: boolean;
};
type Closure = {
  version: number;
  created: string;
  actor?: string;
  reason?: string;
  published?: number;
};
type Lesson = {
  lessonKey: string;
  publishedVersion: number;
  date: string;
  start: string;
  subject: string;
  group: string;
  teachers: string;
};
type Archived = {
  lessonKey: string;
  publishedVersion: number;
  document: JournalDocument;
};
type History = {
  id: number;
  version: number;
  action: string;
  created: string;
  actor: string;
  reason: string;
};
const kinds: Record<string, string> = {
  quarter: "Четверть",
  semester: "Семестр",
  year: "Учебный год",
  custom: "Другой период",
};
const statuses: Record<string, string> = {
  missing: "Журнал не создан",
  draft: "Только черновик",
  incomplete: "Заполнен не полностью",
  stale: "Нужна синхронизация",
  orphaned: "Публикация вне расписания",
  upcoming: "Урок ещё не закончился",
  complete: "Заполнен",
};
const actions: Record<string, string> = {
  created: "Период создан",
  updated: "Период изменён",
  closed: "Журналы закрыты",
  reopened: "Журналы открыты для исправлений",
};

export default function JournalPeriodsPanel({
  workspace,
  onChanged,
}: {
  workspace: string;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false),
    [options, setOptions] = useState<Options | null>(null);
  const [selected, setSelected] = useState<Period | null>(null),
    [name, setName] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [kind, setKind] = useState("quarter");
  const [reason, setReason] = useState(""),
    [ack, setAck] = useState(false),
    [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [closures, setClosures] = useState<Closure[]>([]),
    [nextClosure, setNextClosure] = useState<number | null>(null),
    [closure, setClosure] = useState<Closure | null>(null);
  const [lessons, setLessons] = useState<Lesson[]>([]),
    [nextLesson, setNextLesson] = useState<number | null>(null),
    [archived, setArchived] = useState<Archived | null>(null);
  const [history, setHistory] = useState<History[]>([]),
    [nextHistory, setNextHistory] = useState<number | null>(null);
  const detail = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (archived) {
      detail.current?.focus({ preventScroll: true });
      detail.current?.scrollIntoView({ block: "start" });
    }
  }, [archived]);
  useEffect(() => {
    if (!expanded || options) return;
    let live = true;
    setBusy(true);
    api("journal/periods", workspace)
      .then((r: Options) => {
        if (live) {
          setOptions(r);
          setStart(r.yearStart);
          setEnd(r.yearStart);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [expanded, options, workspace]);
  const choose = (p: Period | null) => {
    setSelected(p);
    setName(p?.name || "");
    setStart(p?.start || options?.yearStart || "");
    setEnd(p?.end || options?.yearStart || "");
    setKind(p?.kind || "quarter");
    setReason("");
    setPreview(null);
    setAck(false);
    setClosures([]);
    setNextClosure(null);
    setClosure(null);
    setLessons([]);
    setArchived(null);
    setHistory([]);
    setNextHistory(null);
    setMessage("");
    setError("");
  };
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const refreshed = async (p: Period) => {
    const value: Options = await api("journal/periods", workspace);
    setOptions(value);
    choose(value.items.find((r) => r.id === p.id) || null);
    onChanged();
  };
  const save = async () => {
    const id = selected?.id || crypto.randomUUID().replaceAll("-", "");
    const value: Period = await api(`journal/periods/${id}`, workspace, "PUT", {
      version: selected?.version || 0,
      name,
      start,
      end,
      kind,
      reason,
    });
    await refreshed(value);
    setMessage("Учебный период сохранён.");
  };
  const inspect = async () => {
    const p: Preview = await api(
      `journal/periods/${selected!.id}/preview-close`,
      workspace,
      "POST",
      { version: selected!.version },
    );
    setPreview(p);
    setAck(false);
  };
  const transition = async (action: "close" | "reopen") => {
    const p: Period = await api(
      `journal/periods/${selected!.id}/${action}`,
      workspace,
      "POST",
      {
        version: selected!.version,
        reason,
        ...(action === "close"
          ? { previewId: preview!.previewId, acknowledgeIssues: ack }
          : {}),
      },
    );
    await refreshed(p);
    setMessage(
      action === "close"
        ? "Период закрыт. Архив публикаций сохранён."
        : "Период открыт для исправлений. Прежние архивы сохранены; другие закрытые периоды продолжают действовать.",
    );
  };
  const loadClosures = async (before?: number) => {
    const r = await api(
      `journal/periods/${selected!.id}/closures${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setClosures((old) => (before ? [...old, ...r.items] : r.items));
    setNextClosure(r.nextBefore);
    if (!r.items.length && !before)
      setMessage("У этого периода ещё нет архивов закрытия.");
  };
  const loadLessons = async (c: Closure, offset = 0) => {
    const r = await api(
      `journal/periods/${selected!.id}/closures/${c.version}/lessons?offset=${offset}`,
      workspace,
    );
    setClosure(c);
    setLessons((old) => (offset ? [...old, ...r.items] : r.items));
    setNextLesson(r.nextOffset);
    if (!offset) setArchived(null);
  };
  const loadDetail = async (l: Lesson) => {
    setArchived(
      await api(
        `journal/periods/${selected!.id}/closures/${closure!.version}/lessons?lessonKey=${encodeURIComponent(l.lessonKey)}`,
        workspace,
      ),
    );
  };
  const loadHistory = async (before?: number) => {
    const r = await api(
      `journal/periods/${selected!.id}/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setHistory((old) => (before ? [...old, ...r.items] : r.items));
    setNextHistory(r.nextBefore);
  };
  const unsavedPeriod =
    !!selected &&
    (name !== selected.name ||
      start !== selected.start ||
      end !== selected.end ||
      kind !== selected.kind);
  return (
    <details
      className="absence-panel period-panel"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Учебные периоды и закрытие журналов</summary>
      {expanded && (
        <>
          <p>
            Закрытие сохраняет опубликованные журналы в отдельном архиве и
            запрещает их изменение. Открыть период для исправлений может
            администратор с указанием причины. Пересекающиеся закрытые периоды
            действуют одновременно.
          </p>
          <p>
            Архив содержит темы, оценки и исходные отметки учителя на момент
            закрытия. Решения по объяснительным учитываются отдельно и могут
            изменять текущую сводку посещаемости. Итоговые оценки автоматически
            не выставляются.
          </p>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          {message && <p role="status">{message}</p>}
          {busy && <p role="status">Обрабатываю учебный период…</p>}
          {options && (
            <>
              <fieldset disabled={busy} className="access-fields">
                <label className="field">
                  <span>Учебный период</span>
                  <select
                    value={selected?.id || ""}
                    onChange={(e) =>
                      choose(
                        options.items.find((p) => p.id === e.target.value) ||
                          null,
                      )
                    }
                  >
                    <option value="">
                      {options.admin ? "Новый период" : "Выберите период"}
                    </option>
                    {options.items.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} · {p.start}–{p.end} ·{" "}
                        {p.status === "closed" ? "Закрыт" : "Открыт"}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() =>
                    void run(async () => {
                      const p = selected?.id;
                      const r: Options = await api(
                        "journal/periods",
                        workspace,
                      );
                      setOptions(r);
                      choose(r.items.find((i) => i.id === p) || null);
                    })
                  }
                >
                  Обновить список периодов
                </button>
              </fieldset>
              {selected && (
                <p>
                  <strong>{selected.name}</strong> · {kinds[selected.kind]} ·{" "}
                  {selected.start}–{selected.end} ·{" "}
                  {selected.status === "closed" ? "Закрыт" : "Открыт"}
                </p>
              )}
              {options.admin && (!selected || !selected.everClosed) && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(save);
                  }}
                >
                  <fieldset disabled={busy} className="access-fields">
                    <div className="dated-toolbar">
                      <label className="field">
                        <span>Название периода</span>
                        <input
                          required
                          maxLength={120}
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        <span>Вид периода</span>
                        <select
                          value={kind}
                          onChange={(e) => setKind(e.target.value)}
                        >
                          {Object.entries(kinds).map(([v, l]) => (
                            <option key={v} value={v}>
                              {l}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>Начало периода</span>
                        <input
                          required
                          type="date"
                          min={options.yearStart}
                          max={options.yearEnd}
                          value={start}
                          onChange={(e) => setStart(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        <span>Конец периода</span>
                        <input
                          required
                          type="date"
                          min={start || options.yearStart}
                          max={options.yearEnd}
                          value={end}
                          onChange={(e) => setEnd(e.target.value)}
                        />
                      </label>
                    </div>
                    <label className="field">
                      <span>Причина создания или изменения</span>
                      <textarea
                        required
                        minLength={3}
                        maxLength={1000}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </label>
                    <button>Сохранить период</button>
                  </fieldset>
                </form>
              )}
              {options.admin && selected && (
                <section className="absence-card">
                  {selected.everClosed && (
                    <p>
                      Название и границы после первого закрытия неизменны.
                      Каждое повторное закрытие создаёт новый архив.
                    </p>
                  )}
                  {selected.status === "open" && (
                    <button
                      disabled={busy || unsavedPeriod}
                      onClick={() => void run(inspect)}
                    >
                      Проверить перед закрытием
                    </button>
                  )}
                  {unsavedPeriod && (
                    <p>
                      Сохраните изменения названия и дат перед проверкой
                      закрытия.
                    </p>
                  )}
                  {preview && !unsavedPeriod && (
                    <div aria-label="Проверка перед закрытием">
                      <p>
                        Ожидаемые уроки: {preview.counts.expected}. Отменённые:{" "}
                        {preview.counts.cancelled}. В архив попадут публикации:{" "}
                        {preview.published}. Есть более новые черновики:{" "}
                        {preview.draftChanges}.
                      </p>
                      <ul>
                        {Object.entries(statuses).map(([v, l]) => (
                          <li key={v}>
                            {l}: {preview.counts[v]}
                          </li>
                        ))}
                      </ul>
                      <p>
                        Проверка действительна до{" "}
                        {new Date(preview.expires).toLocaleTimeString("ru-RU")};
                        изменение расписания или журналов потребует новой
                        проверки.
                      </p>
                      {!preview.canClose && (
                        <p className="error-banner">
                          Период ещё не закончился или содержит незавершённые
                          уроки. Закрытие пока недоступно.
                        </p>
                      )}
                      {preview.requiresAcknowledgement && (
                        <label className="period-ack">
                          <input
                            type="checkbox"
                            disabled={busy}
                            checked={ack}
                            onChange={(e) => setAck(e.target.checked)}
                          />{" "}
                          Подтверждаю закрытие с указанными пробелами. Черновики
                          и отсутствующие записи не станут публикациями.
                        </label>
                      )}
                    </div>
                  )}
                  {((preview && !unsavedPeriod) ||
                    selected.status === "closed") && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(() =>
                          transition(
                            selected.status === "closed" ? "reopen" : "close",
                          ),
                        );
                      }}
                    >
                      <fieldset disabled={busy} className="access-fields">
                        <label className="field">
                          <span>
                            {selected.status === "closed"
                              ? "Причина открытия для исправлений"
                              : "Причина закрытия"}
                          </span>
                          <textarea
                            required
                            minLength={3}
                            maxLength={1000}
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                          />
                        </label>
                        <button
                          disabled={
                            busy ||
                            (selected.status === "open" &&
                              (!preview?.canClose ||
                                (preview.requiresAcknowledgement && !ack)))
                          }
                        >
                          {selected.status === "closed"
                            ? "Открыть для исправлений"
                            : "Закрыть период и сохранить архив"}
                        </button>
                      </fieldset>
                    </form>
                  )}
                  <button
                    disabled={busy}
                    onClick={() => void run(() => loadHistory())}
                  >
                    История решений
                  </button>
                  {history.map((h) => (
                    <article key={h.id}>
                      <p>
                        <strong>{actions[h.action]}</strong> ·{" "}
                        {new Date(h.created).toLocaleString("ru-RU")} ·{" "}
                        {h.actor}
                      </p>
                      <p>{h.reason}</p>
                    </article>
                  ))}
                  {nextHistory !== null && (
                    <button
                      disabled={busy}
                      onClick={() => void run(() => loadHistory(nextHistory))}
                    >
                      Предыдущие решения
                    </button>
                  )}
                </section>
              )}
              {selected && (
                <section className="absence-card">
                  <h3>Архивы закрытия</h3>
                  <p>
                    {options.admin
                      ? "Доступны публикации всей школы."
                      : "Доступны публикации с назначенными вам преподавателями."}{" "}
                    Текущие изменения и повторное открытие периода не меняют
                    сохранённый архив.
                  </p>
                  <button
                    disabled={busy}
                    onClick={() => void run(() => loadClosures())}
                  >
                    Показать архивы
                  </button>
                  {closures.map((c) => (
                    <article key={c.version}>
                      <p>
                        Закрытие №{c.version} ·{" "}
                        {new Date(c.created).toLocaleString("ru-RU")}
                        {c.actor && ` · ${c.actor}`}
                        {c.published !== undefined &&
                          ` · публикаций: ${c.published}`}
                      </p>
                      {c.reason && <p>{c.reason}</p>}
                      <button
                        disabled={busy}
                        onClick={() => void run(() => loadLessons(c))}
                      >
                        Уроки закрытия №{c.version}
                      </button>
                    </article>
                  ))}
                  {nextClosure !== null && (
                    <button
                      disabled={busy}
                      onClick={() => void run(() => loadClosures(nextClosure))}
                    >
                      Предыдущие архивы
                    </button>
                  )}
                  {closure && (
                    <section aria-label="Уроки архива">
                      <h4>Уроки закрытия №{closure.version}</h4>
                      {!lessons.length && (
                        <p>Доступных публикаций в этом архиве нет.</p>
                      )}
                      {lessons.map((l) => (
                        <article key={l.lessonKey}>
                          <p>
                            {l.date} · {l.start} · {l.subject} · {l.group} ·{" "}
                            {l.teachers}
                          </p>
                          <button
                            disabled={busy}
                            onClick={() => void run(() => loadDetail(l))}
                          >
                            Просмотреть публикацию
                          </button>
                        </article>
                      ))}
                      {nextLesson !== null && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(() => loadLessons(closure, nextLesson))
                          }
                        >
                          Следующие уроки архива
                        </button>
                      )}
                    </section>
                  )}
                  {archived && (
                    <section
                      ref={detail}
                      tabIndex={-1}
                      aria-label="Архивная публикация"
                      className="period-archive"
                    >
                      <h3>
                        {archived.document.lesson.date} ·{" "}
                        {archived.document.lesson.subject} ·{" "}
                        {archived.document.lesson.group}
                      </h3>
                      <p>
                        Сохранённая публикация №{archived.publishedVersion} ·
                        только просмотр
                      </p>
                      <p className="journal-text">
                        <strong>Тема: </strong>
                        {archived.document.content.topic || "Не заполнена"}
                      </p>
                      <p className="journal-text">
                        <strong>Домашнее задание: </strong>
                        {archived.document.content.homework || "Не задано"}
                      </p>
                      <button
                        onClick={() =>
                          journalCsv(
                            documentRows(
                              archived.document,
                              archived.lessonKey,
                              archived.publishedVersion,
                            ),
                            `Архив-журнала-${archived.document.lesson.date}.csv`,
                          )
                        }
                      >
                        Скачать публикацию CSV
                      </button>
                      {documentRows(
                        archived.document,
                        archived.lessonKey,
                        archived.publishedVersion,
                      ).map((r) => (
                        <article key={r.student.id}>
                          <h4>{r.student.name}</h4>
                          <p>
                            {attendanceNames[r.record.attendance]}
                            {r.record.lateMinutes
                              ? ` · ${r.record.lateMinutes} мин`
                              : ""}
                          </p>
                          {r.record.comment && <p>{r.record.comment}</p>}
                          {r.content.assessments.map((a) => {
                            const g = r.record.grades.find(
                              (g) => g.assessmentId === a.id,
                            );
                            return (
                              <p key={a.id}>
                                {a.name}: {gradeLabel(g, a)}
                                {g?.comment && ` · ${g.comment}`}
                              </p>
                            );
                          })}
                        </article>
                      ))}
                    </section>
                  )}
                </section>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}
