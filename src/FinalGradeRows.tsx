import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { finalLabel, names, needsReason } from "./finalGrades";
import FinalGradeRowDetails from "./FinalGradeRowDetails";
import FinalSelect from "./FinalSelect";
import type { CategoryConfig } from "./assessmentCategories";
import type { Book, Named, Row, Scale, Sheet } from "./finalGradesTypes";
import { useBusy } from "./useBusy";
import "./FinalGrades.css";

const ACTION_LABELS: Record<string, string> = {
  approve: "Утвердить выбранные",
  return: "Вернуть на исправление",
  publish: "Опубликовать выбранные",
  reopen: "Открыть исправление",
  withdraw: "Отозвать публикацию",
};
const PAGE = 50;
const NO_FILTER = { classId: "", subjectId: "", status: "all" };

// «Все строки»: every row of the statement as a list with filters and paging, for the bulk actions of the administrator.
export default function FinalGradeRows({
  workspace,
  book,
  readOnly,
  scales,
  teachers,
  categories,
  epoch,
  lastScale,
  leave,
  onDirty,
  onChanged,
}: {
  workspace: string;
  // The statement; an archived year comes as a statement that is not current.
  book: Book;
  readOnly: boolean;
  scales: Scale[];
  teachers: Named[];
  categories: CategoryConfig | null;
  // Raised after changes made elsewhere on the screen and after the changes made here: the list is read again.
  epoch: number;
  lastScale: { current: string };
  leave: () => boolean;
  onDirty: (table: string, dirty: boolean) => void;
  onChanged: () => void;
}) {
  const { busy, error, notice, setError, setNotice, run } = useBusy();
  const [sheet, setSheet] = useState<Sheet | null>(null),
    [query, setQuery] = useState({ filter: NO_FILTER, offset: 0 }),
    [selected, setSelected] = useState<string[]>([]),
    [teacherId, setTeacherId] = useState(""),
    [reason, setReason] = useState(""),
    [opened, setOpened] = useState<Row | null>(null);
  const generation = useRef(0);
  const { filter, offset } = query;

  // Two loads can overlap; only the newest answer is shown.
  async function load() {
    const mine = ++generation.current;
    const r: Sheet = await api(
      `final-grades/books/${book.id}?${new URLSearchParams({ ...filter, offset: String(offset) })}`,
      workspace,
    );
    if (mine !== generation.current) return;
    setSheet(r);
    setSelected([]);
  }
  useEffect(() => {
    void load().catch((e) =>
      setError(e instanceof Error ? e.message : "Не удалось загрузить итоги."),
    );
  }, [book.id, epoch, query]);

  async function act(action: string) {
    if (!sheet || !selected.length || !leave()) return;
    if (
      action === "publish" &&
      !window.confirm(
        `Опубликовать выбранные итоги (${selected.length}) ученикам и родителям?`,
      )
    )
      return;
    await run(async () => {
      await api("final-grades/actions", workspace, "POST", {
        action,
        rows: sheet.items
          .filter((r) => selected.includes(r.id))
          .map((r) => ({ id: r.id, version: r.version })),
        teacherId,
        reason: needsReason(action) ? reason.trim() : "",
      });
      setReason("");
      setOpened(null);
      onChanged();
      setNotice(
        `${names[action] || action}: выполнено для ${selected.length} строк.`,
      );
    });
  }
  function refilter(next: Partial<typeof filter>) {
    if (!leave()) return;
    setOpened(null);
    setQuery({ filter: { ...filter, ...next }, offset: 0 });
  }
  function page(to: number) {
    if (!leave()) return;
    setOpened(null);
    setQuery({ filter, offset: to });
  }
  if (!sheet)
    return error ? (
      <p role="alert" className="error">
        {error}
      </p>
    ) : (
      <p role="status">Загрузка…</p>
    );
  return (
    <section className="finals-rows" aria-label="Все строки">
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <div className="dated-toolbar">
        <FinalSelect
          label="Класс"
          value={filter.classId}
          empty="Все"
          items={sheet.classes}
          onChange={(classId) => refilter({ classId })}
        />
        <FinalSelect
          label="Предмет"
          value={filter.subjectId}
          empty="Все"
          items={sheet.subjects}
          onChange={(subjectId) => refilter({ subjectId })}
        />
        <label className="field">
          Статус
          <select
            value={filter.status}
            onChange={(e) => refilter({ status: e.target.value })}
          >
            <option value="all">Все</option>
            {["draft", "submitted", "approved", "published"].map((s) => (
              <option key={s} value={s}>
                {names[s]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p>
        Строк: {sheet.total}. Показаны {sheet.items.length ? offset + 1 : 0}–
        {offset + sheet.items.length}.
      </p>
      {!readOnly && (
        <>
          <label>
            <input
              type="checkbox"
              checked={
                !!sheet.items.length && selected.length === sheet.items.length
              }
              onChange={(e) =>
                setSelected(
                  e.target.checked ? sheet.items.map((r) => r.id) : [],
                )
              }
            />{" "}
            Выбрать страницу
          </label>
          <div className="dated-toolbar">
            <FinalSelect
              label="Ответственный учитель"
              value={teacherId}
              onChange={setTeacherId}
              items={teachers}
              empty="Снять назначение"
            />
            <button
              disabled={!selected.length || !book.current || busy}
              onClick={() => void act("assign")}
            >
              Назначить выбранным
            </button>
          </div>
          <p>
            Назначение даёт учителю доступ к итогам и работам только выбранных
            учеников и предметов.
          </p>
          <label className="field">
            Причина возврата, исправления или отзыва
            <input
              value={reason}
              maxLength={1000}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <div className="dated-toolbar">
            {Object.keys(ACTION_LABELS).map((action) => (
              <button
                key={action}
                disabled={
                  busy ||
                  !selected.length ||
                  (!book.current && action !== "withdraw") ||
                  (needsReason(action) && reason.trim().length < 3)
                }
                onClick={() => void act(action)}
              >
                {ACTION_LABELS[action]}
              </button>
            ))}
          </div>
        </>
      )}
      <div className="attendance-cards">
        {sheet.items.map((r) => (
          <article className="absence-card" key={r.id}>
            {!readOnly && (
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(r.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, r.id]
                        : selected.filter((id) => id !== r.id),
                    )
                  }
                />{" "}
                Выбрать {r.source.student.name}, {r.source.subject}
              </label>
            )}
            <h4>
              {r.source.student.name} · {r.source.student.className}
            </h4>
            <p>
              {r.source.subject} · {names[r.status]}
            </p>
            <p>
              {r.proposal ? finalLabel(r.proposal) : "Итог не подготовлен"} ·
              работ с баллами: {r.source.numeric}
            </p>
            <p>
              Ответственный:{" "}
              {teachers.find((t) => t.id === r.assigned)?.name ||
                (r.assigned ? "Учётная запись недоступна" : "Не назначен")}
            </p>
            {r.published && (
              <p>
                В табеле: {finalLabel(r.published)}
                {r.status !== "published"
                  ? " — сохраняется до новой публикации"
                  : ""}
              </p>
            )}
            <button
              onClick={() => {
                if (leave()) setOpened(r);
              }}
            >
              Итог и работы: {r.source.student.name}, {r.source.subject}
            </button>
          </article>
        ))}
      </div>
      <div className="dated-toolbar">
        <button
          disabled={offset === 0}
          onClick={() => page(Math.max(0, offset - PAGE))}
        >
          Предыдущая страница итогов
        </button>
        <button
          disabled={sheet.nextOffset === null}
          onClick={() => page(sheet.nextOffset!)}
        >
          Следующая страница итогов
        </button>
        <a
          href={`/api/final-grades/books/${book.id}/export?${new URLSearchParams({ workspace, ...filter })}`}
          download
        >
          CSV всей ведомости
        </a>
      </div>
      {opened && (
        <FinalGradeRowDetails
          key={opened.id}
          workspace={workspace}
          row={opened}
          scales={scales}
          teachers={teachers}
          admin
          current={book.current}
          readOnly={readOnly}
          categories={categories}
          lastScale={lastScale}
          onDirty={onDirty}
          onChanged={onChanged}
          onClose={() => {
            if (leave()) setOpened(null);
          }}
        />
      )}
    </section>
  );
}
