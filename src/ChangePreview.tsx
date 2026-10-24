import { useState } from "react";
import type { Report, ScheduleIssue } from "./types";

export interface Preview {
  token: string;
  revision: number;
  canApply: boolean;
  report: Report;
  newIssues: ScheduleIssue[];
  diff: {
    total: number;
    added: number;
    removed: number;
    changed: number;
    changes: {
      table: string;
      id: string;
      kind: string;
      before: string | null;
      after: string | null;
      fields: Record<
        string,
        {
          before: unknown;
          after: unknown;
          displayBefore?: string;
          displayAfter?: string;
        }
      >;
    }[];
  };
}
const TABLES: Record<string, string> = {
  placements: "Занятия",
  workloads: "Нагрузка",
  classes: "Классы",
  groups: "Группы",
  students: "Ученики",
  teachers: "Учителя",
  rooms: "Кабинеты",
  subjects: "Предметы",
  profiles: "Звонки",
  events: "Календарь",
  settings: "Параметры",
  importInfo: "Импорт",
  rules: "Ограничения",
  campus: "Здания и переходы",
  cycles: "Учебные циклы",
  courses: "Курсы",
  courseRequests: "Заявки учеников",
  datedLessons: "Изменения по датам",
  absences: "Отсутствия учителей",
  commitments: "Бронирования и дежурства",
};
const FIELDS: Record<string, string> = {
  groupIds: "Группы участников",
  requiredTeachers: "Требуется дежурных",
  eligibleTeacherIds: "Кандидаты",
  location: "Место",
  note: "Примечание",
  start: "Первый день",
  end: "Последний день",
  grades: "Подпись классов",
  onDate: "Дата занятия",
  sourceDate: "Исходная дата",
  placementId: "Исходный урок",
  startTime: "Начало",
  endTime: "Конец",
  teacherIds: "Учителя",
  effect: "Влияние на уроки",
  sections: "Секции и численность",
  studentId: "Ученик",
  studentIds: "Состав группы",
  courseIds: "Курсы по приоритету",
  required: "Обязательная заявка",
  lockedGroupId: "Закреплённая секция",
  patterns: "Недели расписания",
  periods: "Учебные периоды",
  cycleId: "Неделя",
  cycleIds: "Активные недели",
  cycleHours: "Часы по неделям",
  cycleLessonLengths: "Длины занятий по неделям",
  hours: "Часы",
  maxPerDay: "Максимум в день",
  teacherId: "Учитель",
  classTeacherId: "Классный руководитель",
  additionalTeacherIds: "Совместные преподаватели",
  teacherRequired: "Требуется учитель",
  requiredRoomIds: "Все постоянные кабинеты",
  roomRequired: "Требуется кабинет",
  studentCount: "Численность учеников",
  capacity: "Вместимость",
  buildingId: "Здание",
  buildings: "Здания",
  transfers: "Направления переходов",
  defaultTravelMinutes: "Общее время перехода",
  roomIds: "Кабинеты",
  subjectId: "Предмет",
  block: "Синхронный блок",
  allowedPeriods: "Допустимые уроки",
  value: "Значение",
  name: "Название",
  kind: "Тип правила",
  scope: "Кого касается",
  targetIds: "Выбранные записи",
  mode: "Обязательность",
  enabled: "Включено",
  weight: "Вес пожелания",
  minLessons: "Минимум уроков",
  maxLessons: "Максимум уроков",
  maxIdleMinutes: "Время между уроками",
  maxIdlePeriods: "Пустых периодов за день",
  maxIdleBlocks: "Окон за день",
  maxWeeklyIdlePeriods: "Пустых периодов за неделю",
  gapProfileId: "Звонки для окон",
  gapExcludedPeriods: "Исключённые периоды",
  subjectIds: "Первый набор предметов",
  otherSubjectIds: "Второй набор предметов",
  relation: "Связь",
  relationGapMinutes: "Перемена для непосредственного следования",
  maxConsecutive: "Уроков подряд",
  lessonLengths: "Длины занятий",
  maxLessonBreak: "Перемена внутри занятия",
};

export default function ChangePreview({
  preview,
  busy,
  onApply,
  onCancel,
  title = "Предпросмотр изменений",
}: {
  preview: Preview;
  busy: boolean;
  onApply: () => void;
  onCancel: () => void;
  title?: string;
}) {
  const [limit, setLimit] = useState(60);
  return (
    <section className="change-preview" aria-label="Предпросмотр изменений">
      <h3>{title}</h3>
      <p>
        Добавлено: {preview.diff.added} · изменено: {preview.diff.changed} ·
        удалено: {preview.diff.removed}. Базовая версия: {preview.revision}.
      </p>
      {!!preview.report.warningCount && (
        <p>
          Невыполненных пожеланий: {preview.report.warningCount}. Суммарный
          штраф: {preview.report.qualityPenalty}.
        </p>
      )}
      {!preview.report.completeChecks && (
        <p className="info-line">
          Есть неполные исходные данные. Отсутствие найденных конфликтов не
          означает полную проверку расписания.
        </p>
      )}
      {!!preview.report.datedWarnings?.length && (
        <details>
          <summary>
            Пожелания по датам: {preview.report.datedWarnings.length} · штраф{" "}
            {preview.report.datedPenalty}
          </summary>
          <ul>
            {preview.report.datedWarnings.map((issue, i) => (
              <li key={i}>
                {issue.date} · {issue.message}
              </li>
            ))}
          </ul>
        </details>
      )}
      {preview.newIssues.length > 0 && (
        <div className="generation-warning">
          <strong>Новые нарушения: {preview.newIssues.length}</strong>
          <ul>
            {preview.newIssues.slice(0, 30).map((issue, i) => (
              <li key={i}>
                {issue.date && `${issue.date} · `}
                {issue.message}
              </li>
            ))}
          </ul>
          {preview.newIssues.length > 30 && (
            <p>Остальные нарушения будут доступны в общей проверке.</p>
          )}
        </div>
      )}
      {!preview.canApply && (
        <p role="alert">
          Изменения не сохранены. Устраните новые нарушения или выберите другое
          время.
        </p>
      )}
      <div className="change-list">
        {preview.diff.changes.slice(0, limit).map((change) => (
          <article key={`${change.table}:${change.id}`}>
            <small>
              {TABLES[change.table] || change.table} ·{" "}
              {
                {
                  added: "Добавление",
                  removed: "Удаление",
                  changed: "Изменение",
                }[change.kind]
              }
            </small>
            {change.before && (
              <div className="change-before">Было: {change.before}</div>
            )}
            {change.after && <div>Будет: {change.after}</div>}
            {change.table !== "placements" && (
              <details>
                <summary>Изменённые поля</summary>
                {Object.entries(change.fields).map(([field, value]) => (
                  <div key={field}>
                    {FIELDS[field] || field}:{" "}
                    {value.displayBefore ?? JSON.stringify(value.before)} →{" "}
                    {value.displayAfter ?? JSON.stringify(value.after)}
                  </div>
                ))}
              </details>
            )}
          </article>
        ))}
      </div>
      {preview.diff.total > limit && (
        <button type="button" onClick={() => setLimit(limit + 100)}>
          Ещё изменения ({preview.diff.total - limit})
        </button>
      )}
      <div className="modal-actions">
        <button type="button" disabled={busy} onClick={onCancel}>
          Отбросить предпросмотр
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || !preview.canApply || !preview.diff.total}
          onClick={onApply}
        >
          {busy ? "Применяем…" : "Применить изменения"}
        </button>
      </div>
    </section>
  );
}
