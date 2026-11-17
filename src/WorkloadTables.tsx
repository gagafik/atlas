import {
  OUTCOMES,
  filterLessons,
  hours,
  lessonNotes,
  outcomeTone,
  partsLine,
  signed,
  weekLabel,
  weekdayDate,
  type LessonFilter,
  type WorkloadFigures,
  type WorkloadLesson,
  type WorkloadTeacher,
  type WorkloadWeek,
} from "./workloadReport";

/** How many lesson rows a teacher shows before «Показать ещё». */
export const LESSON_PAGE = 50;

/** The id of the name button of a teacher: the page puts the focus there when the list comes back. */
export const workloadNameId = (teacherId: string) =>
  `workload-name-${teacherId}`;

/** The id of the lessons block of a teacher: the page puts the focus on a row there after «Показать ещё». */
export const workloadLessonsId = (teacherId: string) =>
  `workload-lessons-${teacherId}`;

// The tables of the workload dialog. They have no state: which teacher is open, which tab and how many rows are shown is
// decided by the dialog.

export function WorkloadFiguresStrip({ t }: { t: WorkloadFigures }) {
  const taught = partsLine([
    ["свои", t.own],
    ["замены", t.substitutions],
    ["дополнительные", t.extra],
    ["по журналу вне расписания", t.journalOnly],
  ]);
  const missed = partsLine([
    ["при отсутствии", t.missedAbsent],
    ["без замены", t.uncovered],
    ["отменено", t.cancelled],
  ]);
  return (
    <dl className="workload-figures">
      <div>
        <dt>По расписанию на дату</dt>
        <dd>{t.plannedToDate}</dd>
      </div>
      <div>
        <dt>Проведено уроков</dt>
        <dd>{t.taught}</dd>
        {taught && <small>{taught}</small>}
      </div>
      <div>
        <dt>Часы по звонкам</dt>
        <dd>{hours(t.minutes)}</dd>
        <small>{t.minutes} мин</small>
      </div>
      <div>
        <dt>Разница с расписанием</dt>
        <dd>{signed(t.delta)}</dd>
      </div>
      <div>
        <dt>Пропущено</dt>
        <dd>{t.missed}</dd>
        {missed && <small>{missed}</small>}
      </div>
      <div>
        <dt>Предстоит</dt>
        <dd>{t.upcoming}</dd>
      </div>
      <div>
        <dt>Подтверждено журналом</dt>
        <dd>{t.confirmed}</dd>
        {t.differs > 0 && <small>расхождений с журналом: {t.differs}</small>}
      </div>
      <div>
        <dt>Присоединено к другим урокам</dt>
        <dd>{t.merged}</dd>
        {t.merged > 0 && <small>часов не добавляет</small>}
      </div>
    </dl>
  );
}

// All teachers with the totals; the name opens one teacher.
export function TeachersTable({
  teachers,
  totals,
  onPick,
}: {
  teachers: WorkloadTeacher[];
  totals: WorkloadFigures;
  onPick: (t: WorkloadTeacher) => void;
}) {
  return (
    <div className="workload-table">
      <table aria-label="Учителя">
        <thead>
          <tr>
            <th scope="col">Учитель</th>
            <th scope="col">По расписанию на дату</th>
            <th scope="col">Проведено</th>
            <th scope="col">Часы</th>
            <th scope="col">Замены</th>
            <th scope="col">Пропущено</th>
            <th scope="col">Без замены</th>
            <th scope="col">Подтверждено журналом</th>
            <th scope="col">Предстоит</th>
          </tr>
        </thead>
        <tbody>
          {teachers.map((t) => (
            <tr key={t.teacherId}>
              <th scope="row">
                <button
                  id={workloadNameId(t.teacherId)}
                  className="workload-name"
                  onClick={() => onPick(t)}
                >
                  {t.name}
                </button>
              </th>
              <td>{t.plannedToDate}</td>
              <td>{t.taught}</td>
              <td>{hours(t.minutes)}</td>
              <td>{t.substitutions}</td>
              <td>{t.missed}</td>
              <td>{t.uncovered}</td>
              <td>{t.confirmed}</td>
              <td>{t.upcoming}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Итого</th>
            <td>{totals.plannedToDate}</td>
            <td>{totals.taught}</td>
            <td>{hours(totals.minutes)}</td>
            <td>{totals.substitutions}</td>
            <td>{totals.missed}</td>
            <td>{totals.uncovered}</td>
            <td>{totals.confirmed}</td>
            <td>{totals.upcoming}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// The weeks of one teacher; the button of a row opens the lessons of that week.
export function WeeksTable({
  weeks,
  onOpenWeek,
  label = "Недели",
}: {
  weeks: WorkloadWeek[];
  onOpenWeek: (week: WorkloadWeek) => void;
  label?: string;
}) {
  return (
    <div className="workload-table">
      <table aria-label={label}>
        <thead>
          <tr>
            <th scope="col">Неделя</th>
            <th scope="col">По расписанию на дату</th>
            <th scope="col">Проведено</th>
            <th scope="col">Часы</th>
            <th scope="col">Замены</th>
            <th scope="col">Пропущено</th>
            <th scope="col">Предстоит</th>
            <th scope="col">Разница</th>
            <th scope="col" className="workload-noprint">
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {weeks.map((w) => (
            <tr key={w.start}>
              <th scope="row">{weekLabel(w)}</th>
              <td>{w.plannedToDate}</td>
              <td>{w.taught}</td>
              <td>{hours(w.minutes)}</td>
              <td>{w.substitutions}</td>
              <td>{w.missed}</td>
              <td>{w.upcoming}</td>
              <td>{signed(w.delta)}</td>
              <td className="workload-noprint workload-c-actions">
                <button
                  className="workload-row-btn"
                  aria-label={`Уроки недели ${weekLabel(w)}`}
                  onClick={() => onOpenWeek(w)}
                >
                  Уроки
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// The lessons of one teacher: the week and the filter narrow them, the first rows are shown and «Показать ещё» adds more.
export function LessonsTable({
  lessons,
  filter,
  week,
  limit = LESSON_PAGE,
  onMore,
  id,
  label = "Уроки",
}: {
  lessons: WorkloadLesson[];
  filter: LessonFilter;
  week: { start: string; end: string } | null;
  limit?: number;
  /** Gets the index of the first row that «Показать ещё» reveals, so that the page can put the focus there. */
  onMore: (firstNew: number) => void;
  id?: string;
  label?: string;
}) {
  if (!lessons.length) return <p>За период уроков нет.</p>;
  const found = filterLessons(lessons, filter, week);
  if (!found.length) return <p>Таких уроков нет.</p>;
  const shown = found.slice(0, limit);
  return (
    <div className="workload-lessons" id={id}>
      <table aria-label={label}>
        <thead>
          <tr>
            <th scope="col">Дата</th>
            <th scope="col">Время</th>
            <th scope="col">Класс</th>
            <th scope="col">Предмет</th>
            <th scope="col">Итог</th>
            <th scope="col">Примечание</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((l, i) => (
            <tr
              key={i}
              tabIndex={-1}
              className={
                i === 0 || shown[i - 1].date !== l.date
                  ? "workload-lesson workload-day-start"
                  : "workload-lesson"
              }
            >
              <td className="workload-c-date">{weekdayDate(l.date)}</td>
              <td className="workload-c-time">
                {l.start}–{l.end}
              </td>
              <td className="workload-c-group">{l.group}</td>
              <td className="workload-c-subject">{l.subject}</td>
              <td className="workload-c-outcome">
                <span
                  className="workload-badge"
                  data-tone={outcomeTone(l.outcome)}
                >
                  {OUTCOMES[l.outcome] ?? l.outcome}
                </span>
              </td>
              <td className="workload-c-notes">{lessonNotes(l) || null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {found.length > shown.length && (
        <div className="workload-more">
          <span>
            Показано {shown.length} из {found.length}
          </span>
          <button
            className="workload-row-btn workload-noprint"
            onClick={() => onMore(shown.length)}
          >
            Показать ещё
          </button>
        </div>
      )}
    </div>
  );
}
