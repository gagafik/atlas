import type { Dispatch, RefObject, SetStateAction } from "react";
import HomeworkPanel from "./HomeworkPanel";
import HomeworkParallelPanel from "./HomeworkParallelPanel";
import LessonMaterials from "./LessonMaterials";
import LessonTopic from "./LessonTopic";
import AssignmentFilesPanel from "./AssignmentFilesPanel";
import LessonAssessments from "./LessonAssessments";
import { LessonHistoryList, LessonHistoryPreview } from "./LessonHistory";
import LessonPupilTable from "./LessonPupilTable";
import type { LessonMarks } from "./LessonMarks";
import type { CategoryConfig } from "./assessmentCategories";
import { attendanceCounts, summaryParts } from "./lessonEditorModel";
import {
  absenceCodeNames,
  documentRows,
  journalCsv,
  withNoAbsentees,
  type Assessment,
  type Grade,
  type JournalContent,
  type JournalEntry,
  type JournalHistory,
  type PupilRecord,
} from "./journal";

// What the editor asks of the panel that owns the state: every change, save and request goes through it.
export interface LessonEditorActions {
  content: (update: (c: JournalContent) => JournalContent) => void;
  patchWork: (id: string, change: Partial<Assessment>) => void;
  pupil: (id: string, update: (p: PupilRecord) => PupilRecord) => void;
  grade: (id: string, a: Assessment, update: Partial<Grade>) => void;
  save: (publish: boolean, override?: JournalContent) => Promise<void>;
  openEntry: (key: string, sync?: boolean) => Promise<void>;
  historyPage: (before?: number) => Promise<void>;
  dueNextLesson: () => Promise<void>;
  withdraw: () => Promise<void>;
  showVersion: (version: number) => Promise<void>;
  closeVersion: () => void;
  uploading: (value: boolean) => void;
  // The published lesson changed elsewhere: the day list is read again.
  refreshDay: () => void;
  // A homework mark was published from the list of works under the lesson.
  homeworkGraded: () => void;
}

// The editor of the open lesson. It holds no state of its own: the panel keeps the entry, the unsaved flag and the
// save lock, so leaving the lesson, the unsaved-changes prompt and the payload stay where they were.
export default function LessonEditor({
  workspace,
  entry,
  panelRef,
  dirty,
  busy,
  editable,
  syncRoster,
  categories,
  lessonMarks,
  customWeights,
  setCustomWeights,
  dueNote,
  dueBusy,
  nobodyAbsent,
  history,
  historyMore,
  historical,
  actions,
}: {
  workspace: string;
  entry: JournalEntry;
  panelRef: RefObject<HTMLElement | null>;
  dirty: boolean;
  busy: boolean;
  editable: boolean;
  syncRoster: boolean;
  categories: CategoryConfig | null;
  lessonMarks: LessonMarks;
  customWeights: Set<string>;
  setCustomWeights: Dispatch<SetStateAction<Set<string>>>;
  dueNote: string;
  dueBusy: boolean;
  nobodyAbsent: boolean;
  history: JournalHistory[];
  historyMore: boolean;
  historical: JournalHistory | null;
  actions: LessonEditorActions;
}) {
  const {
    content,
    patchWork,
    pupil,
    grade,
    save,
    openEntry,
    historyPage,
    dueNextLesson,
    withdraw,
    showVersion,
    closeVersion,
    uploading,
    refreshDay,
    homeworkGraded,
  } = actions;
  const summary = summaryParts(
    attendanceCounts(entry.document.content, entry.document.lesson.students),
  );
  return (
    <section
      className="journal-editor"
      ref={panelRef}
      tabIndex={-1}
      aria-label="Редактор журнала урока"
    >
      <header>
        <div>
          <h2>
            {entry.document.lesson.subject} · {entry.document.lesson.group}
          </h2>
          <p>
            {entry.document.lesson.date} · {entry.document.lesson.start}–
            {entry.document.lesson.end} · {entry.document.lesson.teachers}
          </p>
        </div>
        <span>
          {dirty
            ? "Есть несохранённые поля"
            : entry.version
              ? `Редакция ${entry.version}`
              : "Новая запись"}
        </span>
      </header>
      <p className="info-line">
        {entry.attendanceDefaulted
          ? "Все отмечены присутствующими по умолчанию. Семья увидит отметки после сохранения."
          : entry.hasPublished &&
              entry.lessonBegun &&
              entry.publishedVersion === entry.version
            ? "Семья видит эту редакцию; каждое сохранение сразу видно семье."
            : entry.hasPublished
              ? `Ученики видят редакцию ${entry.publishedVersion}. Изменения черновика появятся после публикации.`
              : "Запись ещё не видна ученикам."}
        {entry.updated &&
          ` Последнее изменение: ${entry.actor}, ${new Date(entry.updated).toLocaleString("ru-RU")}.`}
      </p>
      {!entry.document.lesson.rosterComplete && (
        <p className="error-banner">
          Состав группы не подтверждён полностью. Здесь показаны только
          внесённые ученики.
        </p>
      )}
      {!entry.writable && (
        <p className="info-line">
          {entry.periodLocks?.length
            ? `Период закрыт: ${entry.periodLocks.map((p) => p.name).join(", ")}. Для исправления администратор должен открыть все закрытые периоды с указанием причины.`
            : "Запись доступна только для просмотра: урок отменён, удалён или назначен другому преподавателю."}
        </p>
      )}
      {entry.futureLesson && (
        <p className="info-line">
          Урок ещё не прошёл. Тему, домашнее задание и материалы можно
          опубликовать заранее; посещаемость и оценки публикуются в день урока.
        </p>
      )}
      {entry.rosterFrozen && entry.hasPublished && (
        <p className="info-line">
          Состав, время и учителя урока зафиксированы при публикации:
          последующие изменения расписания и составов их не меняют.
        </p>
      )}
      {entry.needsSync && entry.writable && (
        <div className="info-line">
          <p>
            Расписание или состав группы изменились. Просмотрите обновление
            перед редактированием. Прежние ученики и их оценки сохранятся; новые
            ученики появятся без отметок.
            {entry.currentDate !== entry.document.lesson.date &&
              ` Текущая дата урока: ${entry.currentDate}.`}
          </p>
          <button
            disabled={busy}
            onClick={() => void openEntry(entry.lessonKey, true)}
          >
            Обновить состав и время
          </button>
        </div>
      )}
      {syncRoster && (
        <p className="info-line">
          Обновлённый состав показан для проверки. Он сохранится вместе с
          записью.
        </p>
      )}
      <fieldset disabled={!editable} className="journal-content-fields">
        <label className="field">
          <span>Тема урока</span>
          <textarea
            maxLength={2000}
            value={entry.document.content.topic}
            onChange={(e) => content((c) => ({ ...c, topic: e.target.value }))}
          />
        </label>
        <div id="journal-lesson-topic">
          <LessonTopic
            workspace={workspace}
            lessonKey={entry.lessonKey}
            day={entry.document.lesson.date}
            value={entry.document.content.curriculum}
            materials={entry.document.content.materials || []}
            disabled={!editable}
            onChange={(curriculum, materials) =>
              content((c) => ({
                ...c,
                curriculum,
                materials: materials ?? c.materials,
                topic:
                  curriculum &&
                  (curriculum.planId !== c.curriculum?.planId ||
                    curriculum.version !== c.curriculum?.version ||
                    curriculum.topicId !== c.curriculum?.topicId)
                    ? curriculum.title
                    : c.topic,
              }))
            }
          />
        </div>
        <label className="field" id="journal-homework">
          <span>Домашнее задание</span>
          <textarea
            maxLength={4000}
            value={entry.document.content.homework}
            onChange={(e) =>
              content((c) => ({
                ...c,
                homework: e.target.value,
                dueDate: e.target.value ? c.dueDate : null,
                homeworkFiles: e.target.value ? c.homeworkFiles : [],
                homeworkLatePolicy: e.target.value
                  ? c.homeworkLatePolicy
                  : "allow",
                homeworkSubmission: e.target.value
                  ? c.homeworkSubmission
                  : "online",
              }))
            }
          />
        </label>
        <label className="field">
          <span>Срок домашнего задания</span>
          <input
            type="date"
            disabled={!entry.document.content.homework}
            value={entry.document.content.dueDate || ""}
            onChange={(e) =>
              content((c) => ({
                ...c,
                dueDate: e.target.value || null,
                homeworkLatePolicy: e.target.value
                  ? c.homeworkLatePolicy
                  : "allow",
              }))
            }
          />
        </label>
        <div className="journal-due-next">
          <button
            type="button"
            disabled={!entry.document.content.homework || dueBusy}
            onClick={() => void dueNextLesson()}
          >
            К следующему уроку
          </button>
          {dueNote && <small role="status">{dueNote}</small>}
        </div>
        <label className="field">
          <span>Сдача работы</span>
          <select
            disabled={!entry.document.content.homework}
            value={entry.document.content.homeworkSubmission || "online"}
            onChange={(e) =>
              content((c) => ({
                ...c,
                homeworkSubmission: e.target.value as "online" | "offline",
                homeworkLatePolicy:
                  e.target.value === "offline" ? "allow" : c.homeworkLatePolicy,
              }))
            }
          >
            <option value="online">Ученики отправляют ответ в Атласе</option>
            <option value="offline">
              Без онлайн-сдачи (показывают на уроке)
            </option>
          </select>
        </label>
        <label className="field">
          <span>Приём после срока</span>
          <select
            disabled={
              !entry.document.content.dueDate ||
              entry.document.content.homeworkSubmission === "offline"
            }
            value={entry.document.content.homeworkLatePolicy || "allow"}
            onChange={(e) =>
              content((c) => ({
                ...c,
                homeworkLatePolicy: e.target.value as "allow" | "block",
              }))
            }
          >
            <option value="allow">Разрешать с отметкой об опоздании</option>
            <option value="block">Закрывать приём после срока</option>
          </select>
          <small>
            Срок заканчивается в 23:59 по времени школы (Кызылорда). При
            закрытом приёме для повторной сдачи после возврата тоже потребуется
            продление срока.
          </small>
        </label>
        <LessonMaterials
          key={workspace + entry.lessonKey + entry.version}
          items={entry.document.content.materials || []}
          workspace={workspace}
          lessonKey={entry.lessonKey}
          disabled={!editable}
          onChange={(materials) => content((c) => ({ ...c, materials }))}
        />
        <AssignmentFilesPanel
          key={workspace + entry.lessonKey}
          entry={entry}
          workspace={workspace}
          disabled={!editable}
          change={(ids) => content((c) => ({ ...c, homeworkFiles: ids }))}
          onUploading={uploading}
        />
        <LessonAssessments
          entry={entry}
          editable={editable}
          categories={categories}
          customWeights={customWeights}
          setCustomWeights={setCustomWeights}
          content={content}
          patchWork={patchWork}
        />
      </fieldset>
      <section className="lesson-pupils-block">
        <div className="lesson-pupils-head">
          <h3 id="journal-pupil-cards">Ученики</h3>
          {!!entry.document.lesson.students.length && (
            <span className="lesson-pupils-summary">
              {summary.length
                ? summary.map(([label, n]) => `${label}: ${n}`).join(" · ")
                : "Все присутствуют"}
            </span>
          )}
          {nobodyAbsent ? (
            <button
              type="button"
              className="primary"
              disabled={!editable}
              title="Отметит присутствующими всех, кто не отмечен, и сразу сохранит урок для семьи."
              onClick={() =>
                void save(
                  true,
                  withNoAbsentees(
                    entry.document.content,
                    entry.document.lesson.students,
                    entry.attendanceApprovals,
                  ),
                )
              }
            >
              Отсутствующих нет
            </button>
          ) : (
            <button
              type="button"
              disabled={!editable || !!entry.futureLesson}
              onClick={() =>
                content((c) =>
                  withNoAbsentees(
                    c,
                    entry.document.lesson.students,
                    entry.attendanceApprovals,
                  ),
                )
              }
            >
              Неотмеченные присутствуют
            </button>
          )}
        </div>
        <p className="lesson-pupils-hint">
          Комментарии видны ученику и родителю после публикации.
        </p>
        <LessonPupilTable
          entry={entry}
          categories={categories}
          lessonMarks={lessonMarks}
          onPupil={pupil}
          onGrade={grade}
          readOnly={!editable}
        />
      </section>
      <div className="dated-toolbar journal-savebar">
        {entry.lessonBegun ? (
          <>
            <button
              className="primary"
              disabled={!editable}
              onClick={() => void save(true)}
            >
              Сохранить
            </button>
            {!entry.hasPublished && (
              <button disabled={!editable} onClick={() => void save(false)}>
                Сохранить без показа семье
              </button>
            )}
          </>
        ) : (
          <>
            <button
              className="primary"
              disabled={!editable}
              onClick={() => void save(false)}
            >
              Сохранить черновик
            </button>
            <button disabled={!editable} onClick={() => void save(true)}>
              Показать семье заранее
            </button>
          </>
        )}
        {entry.canWithdraw && (
          <button disabled={busy || dirty} onClick={() => void withdraw()}>
            Скрыть от учеников
          </button>
        )}
        <button
          onClick={() =>
            journalCsv(
              documentRows(
                entry.document,
                entry.lessonKey,
                entry.version,
                entry.attendanceApprovals,
              ),
              undefined,
              categories,
              absenceCodeNames(entry.absenceCodes),
            )
          }
        >
          CSV текущих полей
        </button>
        <button
          disabled={!entry.version || busy}
          onClick={() => void historyPage()}
        >
          История записи
        </button>
      </div>
      <LessonHistoryList
        history={history}
        historyMore={historyMore}
        busy={busy}
        historyPage={historyPage}
        showVersion={showVersion}
      />
      {entry.hasPublished &&
        entry.publishedVersion === entry.version &&
        entry.document.content.homework.trim() && (
          <HomeworkParallelPanel
            key={workspace + entry.lessonKey + entry.version}
            workspace={workspace}
            entry={entry}
            disabled={dirty || busy}
            onPublished={refreshDay}
          />
        )}
      {!!entry.version && (
        <HomeworkPanel
          key={workspace + entry.lessonKey + entry.publishedVersion}
          workspace={workspace}
          lessonKey={entry.lessonKey}
          onGraded={homeworkGraded}
        />
      )}
      {historical?.document && (
        <LessonHistoryPreview
          workspace={workspace}
          entry={entry}
          historical={historical}
          categories={categories}
          closeVersion={closeVersion}
        />
      )}
    </section>
  );
}
