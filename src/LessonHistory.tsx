import { SavedPlanTopic } from "./LessonTopic";
import LessonMaterials from "./LessonMaterials";
import { SavedMaterialLinks } from "./AssignmentFilesPanel";
import PublishedRows from "./PublishedRows";
import type { CategoryConfig } from "./assessmentCategories";
import {
  absenceCodeNames,
  documentRows,
  type JournalEntry,
  type JournalHistory,
} from "./journal";

// The saved revisions of the record, newest first, each one to open for viewing; they sit inside the «История записи» block.
export function LessonHistoryList({
  history,
  historyMore,
  failed = false,
  retry,
  busy,
  historyPage,
  showVersion,
}: {
  history: JournalHistory[];
  historyMore: boolean;
  // The last read of the list did not work: the list may be incomplete, so it is not said to be empty.
  failed?: boolean;
  retry?: () => void;
  busy: boolean;
  historyPage: (before?: number) => Promise<boolean | void>;
  showVersion: (version: number) => Promise<void>;
}) {
  if (!history.length)
    return (
      <p className="lesson-hint">
        {busy
          ? "Загружаю редакции…"
          : failed
            ? "Не удалось загрузить редакции. "
            : "Сохранённых редакций нет."}
        {failed && !busy && retry && (
          <button type="button" onClick={retry}>
            Повторить
          </button>
        )}
      </p>
    );
  return (
    <>
      {history.map((h) => (
        <div key={h.version}>
          <span>
            № {h.version} · {new Date(h.updated).toLocaleString("ru-RU")} ·{" "}
            {h.actor} ·{" "}
            {h.action === "journal.published"
              ? "Опубликовано"
              : h.action === "journal.withdrawn"
                ? "Скрыто"
                : "Черновик"}
          </span>
          <button disabled={busy} onClick={() => void showVersion(h.version)}>
            Посмотреть редакцию {h.version}
          </button>
        </div>
      ))}
      {historyMore && (
        <button
          disabled={busy}
          onClick={() => void historyPage(history.at(-1)!.version)}
        >
          Более ранние редакции
        </button>
      )}
    </>
  );
}

// An old revision of the record, read-only: the settings, the materials and the marks it held.
export function LessonHistoryPreview({
  workspace,
  entry,
  historical,
  categories,
  closeVersion,
}: {
  workspace: string;
  entry: JournalEntry;
  historical: JournalHistory;
  categories: CategoryConfig | null;
  closeVersion: () => void;
}) {
  if (!historical.document) return null;
  return (
    <section className="journal-history-preview">
      <h3>Редакция {historical.version} · только просмотр</h3>
      <p>
        Поздняя сдача:{" "}
        {historical.document.content.homeworkLatePolicy === "block"
          ? "приём закрывается после срока"
          : "разрешена с отметкой"}
        .
      </p>
      {historical.document.content.homeworkSubmission === "offline" && (
        <p>Сдача: без онлайн-сдачи.</p>
      )}
      <SavedPlanTopic value={historical.document.content.curriculum} />
      <LessonMaterials
        items={historical.document.content.materials || []}
        workspace={workspace}
        lessonKey={entry.lessonKey}
      />
      <SavedMaterialLinks
        ids={historical.document.content.homeworkFiles || []}
        workspace={workspace}
        lessonKey={entry.lessonKey}
        day={historical.document.lesson.date}
      />
      <button onClick={() => closeVersion()}>Закрыть редакцию</button>
      <PublishedRows
        rows={documentRows(
          historical.document,
          entry.lessonKey,
          historical.version,
        )}
        categories={categories}
        codeNames={absenceCodeNames(entry.absenceCodes)}
      />
    </section>
  );
}
