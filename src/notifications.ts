// Inbox entries as the server sends them: a title and the child are prepared per recipient, the link is opened through the server.
export interface NoticeChild {
  id: string;
  name: string;
}
export interface Notice {
  id: number;
  workspace: string;
  revision: number;
  created: string;
  action: string;
  read: boolean;
  category: string;
  title?: string;
  students?: NoticeChild[];
  // The entry leads to a record: the server checks access again when it is opened.
  link?: boolean;
  // The account no longer sees the child this entry was about.
  restricted?: boolean;
}
export interface NoticeGroup {
  key: string;
  label: string;
  items: Notice[];
}
export interface OpenResult {
  module: string;
  workspace: string;
  recordId?: string | null;
  studentId?: string | null;
  day?: string | null;
  formId?: string;
  eventId?: string;
}
// The only text a background push may show; the payload carries a category id and nothing else.
export const PUSH_LABELS: Record<string, string> = {
  grades: "Новая оценка",
  homework: "Новое ДЗ",
  journal: "Обновление журнала",
  absences: "Объяснительная",
  quizzes: "Тест",
  assessments: "Контрольная работа",
  behaviour: "Запись о поведении",
  messages: "Новое сообщение",
  meetings: "Встреча",
  forms: "Форма или заявление",
  timetable: "Изменение расписания",
  duty: "Дежурство",
  other: "Обновление в Атласе",
  system: "Проверка уведомлений",
};
export const pushLabel = (category: string | undefined) =>
  category && Object.hasOwn(PUSH_LABELS, category)
    ? PUSH_LABELS[category]
    : PUSH_LABELS.other;
export const TIMETABLE_ACTIONS = [
  "saved",
  "undo",
  "redo",
  "timetable.published",
];
// Entries about several children are grouped by child; a family with one child sees a plain list.
export function groupNotices(items: Notice[]): NoticeGroup[] {
  const children = new Set(
    items.flatMap((n) => (n.students || []).map((s) => s.id)),
  );
  if (children.size < 2) return [{ key: "all", label: "", items }];
  const groups: NoticeGroup[] = [];
  for (const item of items) {
    const kids = item.students || [];
    const key =
      kids.length === 1 ? kids[0].id : kids.length > 1 ? "many" : "general";
    const label =
      kids.length === 1
        ? kids[0].name
        : kids.length > 1
          ? "Несколько детей"
          : "Общее";
    let group = groups.find((g) => g.key === key);
    if (!group) groups.push((group = { key, label, items: [] }));
    group.items.push(item);
  }
  return groups;
}
const EVENTS: Record<string, string> = {
  journal: "atlas-open-journal",
  quizzes: "atlas-open-quizzes",
  timetable: "atlas-open-schedule",
  duty: "atlas-open-duties",
  behaviour: "atlas-open-behaviour",
  assessments: "atlas-open-assessments",
  meetings: "atlas-open-meetings",
  forms: "atlas-open-forms",
  messages: "atlas-open-messages",
  message_reports: "atlas-open-messages",
  absences: "atlas-open-absences",
  homework: "atlas-open-homework",
  clubs: "atlas-open-clubs",
  meeting_events: "atlas-open-meetings",
  meeting_waitlist: "atlas-open-meetings",
  form_items: "atlas-open-forms",
  behaviour_staff: "atlas-open-behaviour",
  // The report card opens in the journal section, where the final-grades panel lives.
  finals: "atlas-open-journal",
};
// Which screen to open for the server's answer: [event name, event detail], or null for an unknown module.
export function openEvent(
  result: OpenResult,
): [string, Record<string, string | null | undefined>] | null {
  if (!Object.hasOwn(EVENTS, result.module)) return null;
  const detail: Record<string, string | null | undefined> = {
    workspace: result.workspace,
  };
  if (result.module === "journal") {
    detail.studentId = result.studentId;
    detail.day = result.day;
  } else if (result.module === "finals") {
    detail.studentId = result.studentId;
    detail.finalsPeriodId = result.recordId;
  } else if (result.module === "timetable") detail.studentId = result.studentId;
  else if (result.module === "messages") detail.threadId = result.recordId;
  return [EVENTS[result.module], detail];
}
