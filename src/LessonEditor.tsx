import {
  useEffect,
  useState,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";
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
import { dayText } from "./pupilJournal";
import {
  attendanceCounts,
  editorState,
  lessonLine,
  summaryParts,
} from "./lessonEditorModel";
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
import "./LessonEditor.css";

// What the editor asks of the panel that owns the state: every change, save and request goes through it.
export interface LessonEditorActions {
  content: (update: (c: JournalContent) => JournalContent) => void;
  patchWork: (id: string, change: Partial<Assessment>) => void;
  pupil: (id: string, update: (p: PupilRecord) => PupilRecord) => void;
  grade: (id: string, a: Assessment, update: Partial<Grade>) => void;
  save: (publish: boolean, override?: JournalContent) => Promise<void>;
  openEntry: (key: string, sync?: boolean) => Promise<void>;
  // Resolves to false when the revisions could not be read.
  historyPage: (before?: number) => Promise<boolean>;
  dueNextLesson: () => Promise<void>;
  withdraw: () => Promise<void>;
  showVersion: (version: number) => Promise<void>;
  closeVersion: () => void;
  uploading: (value: boolean) => void;
  // The published lesson changed elsewhere: the day list is read again.
  refreshDay: () => void;
  // A homework mark was published from the list of works under the lesson.
  homeworkGraded: () => void;
  // Back to the day: asks about unsaved fields first.
  close: () => void;
}

// The editor of the open lesson: «Урок» on the left (what a teacher fills every lesson), «Ученики» on the right. It holds
// no state of the lesson: the panel keeps the entry, the unsaved flag and the save lock, so leaving the lesson, the
// unsaved-changes prompt and the payload stay where they were.
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
    close,
  } = actions;
  const lesson = entry.document.lesson;
  const body = entry.document.content;
  const summary = summaryParts(attendanceCounts(body, lesson.students));
  const state = editorState(entry, dirty);
  // The saved revisions are read each time the block opens, and again after a save has changed the record.
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => setHistoryOpen(false), [entry.lessonKey]);
  const [historyFailed, setHistoryFailed] = useState(false);
  const loadHistory = () => {
    setHistoryFailed(false);
    return historyPage().then((ok) => setHistoryFailed(!ok));
  };
  useEffect(() => {
    if (historyOpen && entry.version) void loadHistory();
    // The list is read every time the block opens and when the record changes; not on every render.
  }, [historyOpen, entry.lessonKey, entry.version]);
  const hasHomework = !!body.homework;
  // Read-only for the whole time the record cannot be changed; a save in progress only switches the fields off.
  const readOnly = !entry.writable || entry.needsSync;
  return (
    <section
      className="journal-editor"
      ref={panelRef}
      tabIndex={-1}
      aria-label="Редактор журнала урока"
    >
      <header className="lesson-editor-head">
        <button type="button" className="lesson-editor-back" onClick={close}>
          ← Журнал дня
        </button>
        <h2>
          {lesson.subject} · {lesson.group}
        </h2>
        <p className="lesson-editor-line">{lessonLine(lesson)}</p>
      </header>
      <div className="lesson-editor-notes">
        <p className="info-line">
          {entry.attendanceDefaulted
            ? "По умолчанию все присутствуют; семья увидит отметки после сохранения."
            : entry.hasPublished &&
                entry.lessonBegun &&
                entry.publishedVersion === entry.version
              ? "Семья видит эту редакцию; каждое сохранение сразу видно ей."
              : entry.hasPublished
                ? `Ученики видят редакцию ${entry.publishedVersion}; изменения появятся после публикации.`
                : "Запись ещё не видна ученикам."}
          {entry.updated &&
            ` Изменил: ${entry.actor}, ${new Date(entry.updated).toLocaleString("ru-RU")}.`}
        </p>
        {!lesson.rosterComplete && (
          <p className="error-banner">
            Состав группы подтверждён не полностью: показаны только внесённые
            ученики.
          </p>
        )}
        {!entry.writable && (
          <p className="info-line">
            {entry.periodLocks?.length
              ? `Период закрыт: ${entry.periodLocks.map((p) => p.name).join(", ")}. Исправить может администратор, открыв периоды с указанием причины.`
              : "Только просмотр: урок отменён, удалён или передан другому учителю."}
          </p>
        )}
        {entry.futureLesson && (
          <p className="info-line">
            Урок ещё не прошёл: тему, задание и материалы можно опубликовать
            заранее, посещаемость и оценки — в день урока.
          </p>
        )}
        {entry.rosterFrozen && entry.hasPublished && (
          <p className="info-line">
            Состав, время и учителя зафиксированы при публикации.
          </p>
        )}
        {entry.needsSync && entry.writable && (
          <div className="info-line">
            <p>
              Расписание или состав изменились. Просмотрите обновление: прежние
              ученики и оценки сохранятся, новые появятся без отметок.
              {entry.currentDate &&
                entry.currentDate !== lesson.date &&
                ` Текущая дата урока: ${dayText(entry.currentDate)}.`}
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void openEntry(entry.lessonKey, true)}
            >
              Обновить состав и время
            </button>
          </div>
        )}
        {syncRoster && (
          <p className="info-line">
            Обновлённый состав показан для проверки и сохранится вместе с
            записью.
          </p>
        )}
      </div>
      <div className="lesson-editor-body">
        <div className="lesson-editor-col lesson-editor-lesson">
          <h3 className="lesson-editor-title">Урок</h3>
          <fieldset disabled={!editable} className="lesson-editor-fields">
            <div className="lesson-editor-pane">
              <label className="field" id="journal-lesson-topic-text">
                <span>Тема урока</span>
                <textarea
                  rows={2}
                  maxLength={2000}
                  value={body.topic}
                  onChange={(e) =>
                    content((c) => ({ ...c, topic: e.target.value }))
                  }
                />
              </label>
              <div id="journal-lesson-topic">
                <LessonTopic
                  key={entry.lessonKey}
                  workspace={workspace}
                  lessonKey={entry.lessonKey}
                  day={lesson.date}
                  value={body.curriculum}
                  materials={body.materials || []}
                  disabled={readOnly}
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
            </div>
            <div className="lesson-editor-pane">
              <label className="field" id="journal-homework">
                <span>Домашнее задание</span>
                <textarea
                  rows={2}
                  maxLength={4000}
                  value={body.homework}
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
              {hasHomework && (
                <div className="lesson-hw">
                  <div className="lesson-row">
                    <label className="lesson-inline-field">
                      <span>Срок</span>
                      <input
                        type="date"
                        value={body.dueDate || ""}
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
                    <button
                      type="button"
                      disabled={dueBusy}
                      onClick={() => void dueNextLesson()}
                    >
                      К следующему уроку
                    </button>
                    <label className="lesson-inline-field">
                      <span>Сдача</span>
                      <select
                        value={body.homeworkSubmission || "online"}
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            homeworkSubmission: e.target.value as
                              "online" | "offline",
                            homeworkLatePolicy:
                              e.target.value === "offline"
                                ? "allow"
                                : c.homeworkLatePolicy,
                          }))
                        }
                      >
                        <option value="online">Онлайн в Атласе</option>
                        <option value="offline">Без онлайн-сдачи</option>
                      </select>
                    </label>
                    <label className="lesson-inline-field">
                      <span>После срока</span>
                      <select
                        disabled={
                          !body.dueDate || body.homeworkSubmission === "offline"
                        }
                        value={body.homeworkLatePolicy || "allow"}
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            homeworkLatePolicy: e.target.value as
                              "allow" | "block",
                          }))
                        }
                      >
                        <option value="allow">Принимать с пометкой</option>
                        <option value="block">Закрывать приём</option>
                      </select>
                    </label>
                  </div>
                  {dueNote && (
                    <small className="lesson-hint" role="status">
                      {dueNote}
                    </small>
                  )}
                  <small className="lesson-hint">
                    «Без онлайн-сдачи» — ответы показывают на уроке. Срок
                    заканчивается в 23:59 по времени школы (Кызылорда); при
                    закрытом приёме для повторной сдачи после возврата тоже
                    нужно продлить срок.
                  </small>
                </div>
              )}
              <AssignmentFilesPanel
                key={workspace + entry.lessonKey}
                entry={entry}
                workspace={workspace}
                disabled={!editable}
                change={(ids) => content((c) => ({ ...c, homeworkFiles: ids }))}
                onUploading={uploading}
              />
            </div>
            <div className="lesson-editor-pane">
              <LessonMaterials
                key={workspace + entry.lessonKey + entry.version}
                items={body.materials || []}
                workspace={workspace}
                lessonKey={entry.lessonKey}
                disabled={!editable}
                onChange={(materials) => content((c) => ({ ...c, materials }))}
              />
            </div>
            <div className="lesson-editor-pane">
              <LessonAssessments
                entry={entry}
                editable={editable}
                categories={categories}
                customWeights={customWeights}
                setCustomWeights={setCustomWeights}
                content={content}
                patchWork={patchWork}
              />
            </div>
          </fieldset>
        </div>
        <div className="lesson-editor-col lesson-editor-pupils">
          <section className="lesson-pupils-block">
            <div className="lesson-pupils-head">
              <h3 id="journal-pupil-cards">Ученики</h3>
              {!!lesson.students.length && (
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
                        body,
                        lesson.students,
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
                        lesson.students,
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
              key={entry.lessonKey}
              entry={entry}
              categories={categories}
              lessonMarks={lessonMarks}
              onPupil={pupil}
              onGrade={grade}
              readOnly={!editable}
            />
          </section>
        </div>
        <div className="lesson-editor-col lesson-editor-after">
          {entry.hasPublished &&
            entry.publishedVersion === entry.version &&
            body.homework.trim() && (
              <details className="lesson-editor-fold">
                <summary>Задание в других классах</summary>
                <HomeworkParallelPanel
                  key={workspace + entry.lessonKey + entry.version}
                  workspace={workspace}
                  entry={entry}
                  disabled={dirty || busy}
                  onPublished={refreshDay}
                />
              </details>
            )}
          {!!entry.version && (
            <HomeworkPanel
              key={workspace + entry.lessonKey + entry.publishedVersion}
              workspace={workspace}
              lessonKey={entry.lessonKey}
              onGraded={homeworkGraded}
            />
          )}
          {!!entry.version && (
            <details
              className="journal-history"
              open={historyOpen}
              onToggle={(e) => setHistoryOpen(e.currentTarget.open)}
            >
              <summary>История записи</summary>
              <LessonHistoryList
                history={history}
                historyMore={historyMore}
                failed={historyFailed}
                retry={() => void loadHistory()}
                busy={busy}
                historyPage={historyPage}
                showVersion={showVersion}
              />
              {historical?.document && (
                <LessonHistoryPreview
                  workspace={workspace}
                  entry={entry}
                  historical={historical}
                  categories={categories}
                  closeVersion={closeVersion}
                />
              )}
            </details>
          )}
        </div>
      </div>
      <div className="dated-toolbar journal-savebar">
        <span className="lesson-editor-state" data-state={state.kind}>
          {state.text}
        </span>
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
      </div>
    </section>
  );
}
