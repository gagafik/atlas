export type TitleData = {
  title: string;
  author: string;
  isbn: string;
  publisher: string;
  year: number | null;
  kind: string;
  grade: string;
  subject: string;
  description: string;
};
export type Book = {
  id: string;
  version: number;
  archived: boolean;
  data: TitleData;
  total: number;
  available: number;
};
export type CopyData = {
  inventory: string;
  location: string;
  condition: string;
  status: string;
};
export type Copy = CopyData & {
  id: string;
  version: number;
  title: TitleData;
  onLoan: boolean;
  archived: boolean;
};
export type Person = {
  id: string;
  name: string;
  classId?: string;
  className?: string;
};
export type Config = {
  version: number;
  assignments: {
    accountId: string;
    wholeSchool: boolean;
    classIds: string[];
  }[];
  loanDays: number;
  maxRenewals: number;
  maxLoans: number;
};
export type Options = {
  admin: boolean;
  canCatalog: boolean;
  canIssue: boolean;
  students: Person[];
  employees: Person[];
  classes: Person[];
  today: string;
  defaultDue: string;
  config: Config | null;
};
export type Loan = {
  id: string;
  version: number;
  kind: string;
  borrowerId: string;
  borrowerName: string;
  copyId: string;
  title: TitleData;
  inventory: string;
  status: string;
  issued: string;
  due: string;
  returned: string | null;
  renewals: number;
  overdue: boolean;
  conditionOut: string;
  conditionIn: string | null;
  canManage: boolean;
};
export type CartRow = {
  copyId: string;
  copyVersion: number;
  borrower: { kind: string; id: string };
  label: string;
};
export type Preview = {
  previewHash: string;
  due: string;
  items: { copy: Copy; borrower: Person }[];
};
export type Audit = {
  id: string;
  version: number;
  title: string;
  status: string;
  created: string;
  closed: string | null;
  counts: Record<string, number>;
  changed: number;
};
export type AuditItem = {
  copyId: string;
  version: number;
  copy: Copy;
  result: string;
  note: string;
  changed: boolean;
};
export type AuditDetail = {
  audit: Audit;
  items: AuditItem[];
  nextBefore: number | null;
};
export type History = {
  seq: number;
  actor: string;
  created: string;
  action: string;
  reason: string;
  document: any;
};
export const kinds: Record<string, string> = {
  book: "Книга",
  textbook: "Учебник",
};
export const conditions: Record<string, string> = {
  good: "Хорошее",
  worn: "Есть износ",
  damaged: "Повреждён",
};
export const copyStatuses: Record<string, string> = {
  available: "В фонде",
  repair: "В ремонте",
  lost: "Не найден",
  withdrawn: "Списан",
};
export const loanStatuses: Record<string, string> = {
  active: "На руках",
  lost: "Потеряна читателем",
  returned: "Возвращена",
  resolved: "Списание согласовано",
  cancelled: "Ошибочная выдача отменена",
};
export const auditResults: Record<string, string> = {
  pending: "Не проверен",
  seen: "На месте",
  missing: "Не найден при сверке",
  loaned: "Выдача не закрыта",
};
export const actions: Record<string, string> = {
  configured: "Настройки изменены",
  title_saved: "Название сохранено",
  copy_created: "Экземпляр добавлен",
  copy_changed: "Экземпляр изменён",
  issued: "Выдано",
  return: "Возвращено",
  renew: "Срок продлён",
  lost: "Сообщено о потере",
  found: "Найдено и возвращено",
  writeoff: "Списание согласовано",
  cancel: "Выдача отменена",
  remind: "Напоминание отправлено",
  audit_started: "Сверка начата",
  audit_checked: "Результат сверки записан",
  audit_close: "Сверка завершена",
  audit_cancel: "Сверка отменена",
};
export const blankBook = (): Book => ({
  id: "",
  version: 0,
  archived: false,
  total: 0,
  available: 0,
  data: {
    title: "",
    author: "",
    isbn: "",
    publisher: "",
    year: null,
    kind: "book",
    grade: "",
    subject: "",
    description: "",
  },
});
