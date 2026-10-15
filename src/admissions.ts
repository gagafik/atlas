export type Question = {
  id: string;
  title: string;
  kind: "text" | "single" | "multiple" | "consent" | "date" | "number";
  required: boolean;
  choices: string[];
};
export type CampaignData = {
  title: string;
  year: string;
  description: string;
  contact: string;
  privacyNotice: string;
  grades: number[];
  opensAt: string;
  closesAt: string;
  documents: { id: string; title: string; required: boolean }[];
  questions: Question[];
};
export type Campaign = {
  id: string;
  version: number;
  status: string;
  published: boolean;
  accepting: boolean;
  data: CampaignData;
  reviewers?: { id: string; current: boolean }[];
};
export type Answer = string | string[] | boolean | number | null;
export type ApplicationData = {
  childName: string;
  birthDate: string | null;
  grade: number | null;
  guardianName: string;
  email: string;
  phone: string;
  previousSchool: string;
  answers: Record<string, Answer>;
  files: { id: string; kind: string }[];
  consent: boolean;
};
export type Application = {
  id: string;
  campaignId: string;
  version: number;
  status: string;
  data: ApplicationData;
  submittedAt: string | null;
  replyUntil: string | null;
  campaign: Campaign;
};
export type HistoryItem = {
  id: number;
  created: string;
  actor: string;
  action: string;
  message: string;
  data: ApplicationData | null;
};
export type DocumentFile = {
  id: string;
  filename: string;
  size: number;
  linked: boolean;
};
export type Family = {
  id: string;
  login: string;
  name: string;
  version: number;
};
export const admissionLabels: Record<string, string> = {
  draft: "Черновик",
  open: "Приём открыт",
  closed: "Приём закрыт",
  archived: "В архиве",
  submitted: "Подана",
  in_review: "На рассмотрении",
  returned: "Нужны уточнения",
  waitlisted: "Лист ожидания",
  accepted: "Принято положительное решение",
  rejected: "Отказ",
  withdrawn: "Отозвана",
  reopen: "Решение пересмотрено",
  note: "Заметка комиссии",
  message: "Сообщение семье",
};
export function recoveryCode() {
  return Array.from(crypto.getRandomValues(new Uint8Array(24)), (n) =>
    n.toString(16).padStart(2, "0"),
  ).join("");
}
export function editableApplication(application: Application) {
  return (
    ["draft", "returned"].includes(application.status) &&
    application.campaign.status !== "archived"
  );
}
export async function admissionRequest(
  path: string,
  method = "GET",
  body?: unknown,
) {
  const response = await fetch("/admissions-api/" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Atlas-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return admissionResponse(response);
}
export async function admissionResponse(response: Response) {
  let result: any;
  try {
    result = await response.json();
  } catch {
    throw new Error(
      "Сервер недоступен. Сохранённые ранее сведения останутся в кабинете.",
    );
  }
  if (!response.ok)
    throw Object.assign(
      new Error(
        typeof result.detail === "string"
          ? result.detail
          : Array.isArray(result.detail)
            ? result.detail.map((x: any) => x.msg).join("\n")
            : "Не удалось выполнить действие.",
      ),
      { status: response.status },
    );
  return result;
}
// A retry after an interrupted response keeps its identity, until the payload changes.
export function mutationId(
  previous: { payload: string; id: string } | null,
  payload: unknown,
) {
  const value = JSON.stringify(payload);
  return previous?.payload === value
    ? previous
    : { payload: value, id: crypto.randomUUID() };
}
