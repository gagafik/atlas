export type Named = { id: string; name: string; className?: string };
export type Definition = {
  title: string;
  kind: string;
  series: string;
  text: string;
  fields: { key: string; label: string; required: boolean }[];
};
export type Template = {
  id: string;
  version: number;
  active: boolean;
  definition: Definition;
};
export type Snapshot = {
  title: string;
  text: string;
  schoolName: string;
  subjectName: string;
  number: string;
  date: string;
};
export type Item = {
  id: string;
  version: number;
  status: string;
  archived: boolean;
  kind: string;
  subjectName: string;
  title: string;
  number: string | null;
  created: string;
  updated: string;
  issued: string | null;
  canPrepare?: boolean;
  definition?: Definition;
  values?: Record<string, string>;
  templateVersion?: number;
  assigneeId?: string;
  subjectId?: string;
  templateId?: string;
  snapshot?: Snapshot;
  revocationReason?: string;
  recipients?: Named[];
};
export type Options = {
  canManage: boolean;
  students: Named[];
  employees: Named[];
  templates: Template[];
  year: number;
};
export type Preview = {
  previewHash: string;
  snapshot?: Snapshot;
  recipients: Named[];
  previousRecipients?: Named[];
};
export type History = {
  seq: number;
  created: string;
  actor: string;
  action: string;
  reason: string;
  document: Partial<Item & Template>;
};
export const kinds: Record<string, string> = {
  student: "Для ученика",
  employee: "Для сотрудника",
  internal: "Внутренний документ",
};
export const statuses: Record<string, string> = {
  draft: "Черновик",
  submitted: "На проверке",
  returned: "На исправлении",
  cancelled: "Подготовка отменена",
  issued: "Выдан",
  revoked: "Отозван",
};
export const actions: Record<string, string> = {
  created: "Создан",
  changed: "Шаблон изменён",
  saved: "Черновик сохранён",
  assigned: "Ответственный назначен",
  submit: "Отправлен на проверку",
  withdraw: "Возвращён в черновики",
  return: "Возвращён на исправление",
  cancel: "Подготовка отменена",
  issue: "Выдан",
  revoke: "Отозван",
  archive: "В архиве",
  unarchive: "Возвращён из архива",
  recipients: "Доступ получателей обновлён",
};
export const blankTemplate = (): Template => ({
  id: "",
  version: 0,
  active: false,
  definition: {
    title: "Новый документ",
    kind: "student",
    series: "DOC",
    text: "{{personName}}\n{{purpose}}\n\nДата: {{date}}\nНомер: {{number}}",
    fields: [{ key: "purpose", label: "Содержание", required: true }],
  },
});
