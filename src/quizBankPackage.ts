// Question bank packages: limits, selection and transfer helpers (kept free of React so they can be tested).
import { uploadWithRetry, type UploadOptions } from "./uploads";

export const MAX_SELECTION = 100;
export const MAX_PACKAGE = 55 * 1024 * 1024;
export const MAX_JSON = 2 * 1024 * 1024;

export interface PreviewItem {
  subject: string;
  tags: string[];
  kind: string;
  text: string;
  points: number;
  files: number;
  archived: boolean;
}
export interface PackagePreview {
  packageHash: string;
  format: "zip" | "json";
  size: number;
  questions: number;
  archived: number;
  files: number;
  totalSize: number;
  subjects: string[];
  kinds: Record<string, number>;
  items: PreviewItem[];
}
export interface ImportResult {
  imported: number;
  archived: number;
  files: number;
  questionIds: string[];
}

const GENERIC = "Не удалось выполнить действие.";
const STALE_HINT =
  "Список обновлён. Если ошибка повторится, нажмите «Снять выбор» и отметьте вопросы заново.";

export class PackageError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function packageError(file: { name: string; size: number }) {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".zip") && !name.endsWith(".json"))
    return "Выберите ZIP-пакет Атласа или JSON по шаблону.";
  if (file.name.length > 180)
    return "Имя файла не должно быть длиннее 180 символов.";
  if (!file.size) return "Нельзя загрузить пустой файл.";
  if (name.endsWith(".json") && file.size > MAX_JSON)
    return "JSON должен быть не больше 2 МБ.";
  if (name.endsWith(".zip") && file.size > MAX_PACKAGE)
    return "ZIP-пакет должен быть не больше 55 МБ.";
  return "";
}

export function formatSize(bytes: number) {
  const round = (value: number) =>
    String(Math.round(value * 10) / 10).replace(".", ",");
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${round(bytes / 1024)} КБ`;
  return `${round(bytes / (1024 * 1024))} МБ`;
}

export function toggleSelected(
  selected: ReadonlyMap<string, number>,
  item: { id: string; version: number },
) {
  const next = new Map(selected);
  if (next.delete(item.id)) return { selected: next, error: "" };
  if (next.size >= MAX_SELECTION)
    return {
      selected: next,
      error: `Можно выбрать не больше ${MAX_SELECTION} вопросов.`,
    };
  next.set(item.id, item.version);
  return { selected: next, error: "" };
}

// A saved edit raises the version, and export selects by identity.
export function refreshSelected(
  selected: ReadonlyMap<string, number>,
  items: { id: string; version: number }[],
) {
  const next = new Map(selected);
  for (const item of items)
    if (next.has(item.id)) next.set(item.id, item.version);
  return next;
}

export function exportFailure(error: { message: string; status?: number }) {
  return error.status === 409
    ? `${error.message} ${STALE_HINT}`
    : error.message;
}

export function previewSummary(p: PackagePreview) {
  return [
    `Вопросов: ${p.questions}` +
      (p.archived ? ` (в архиве: ${p.archived})` : ""),
    p.subjects.length ? `предметы: ${p.subjects.join(", ")}` : "",
    `файлов: ${p.files}` + (p.files ? `, ${formatSize(p.totalSize)}` : ""),
  ]
    .filter(Boolean)
    .join(" · ");
}

export function downloadName(
  contentDisposition: string | null,
  fallback: string,
) {
  const name = /(?:^|[;\s])filename="([^"]+)"/i
    .exec(contentDisposition || "")?.[1]
    ?.trim();
  return name && !/[\\/]/.test(name) ? name : fallback;
}

async function failure(response: Response) {
  if (response.status === 401 && typeof window !== "undefined")
    window.dispatchEvent(new Event("atlas-auth-required"));
  let detail: unknown;
  try {
    detail = (await response.json()).detail;
  } catch {
    detail = undefined;
  }
  return new PackageError(
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((x: any) => x.msg).join("\n")
        : GENERIC,
    response.status,
  );
}

/** Both transfers wait out a busy server (429 with Retry-After) like every other upload. */
type TransferOptions = Pick<UploadOptions, "signal" | "onBusy">;

export async function sendPackage(
  path: "import/preview" | "import",
  workspace: string,
  file: File,
  extra: Record<string, string>,
  options: TransferOptions = {},
) {
  const params = new URLSearchParams({
    workspace,
    filename: file.name,
    ...extra,
  });
  const response = await uploadWithRetry(
    `/api/journal/quiz-bank/${path}?${params}`,
    file,
    options,
  );
  if (!response.ok) throw await failure(response);
  try {
    return await response.json();
  } catch {
    throw new Error(GENERIC);
  }
}

export async function exportPackage(
  workspace: string,
  selected: ReadonlyMap<string, number>,
  options: TransferOptions = {},
) {
  const response = await uploadWithRetry(
    `/api/journal/quiz-bank/export?${new URLSearchParams({ workspace })}`,
    new Blob(
      [
        JSON.stringify({
          items: Array.from(selected, ([id, version]) => ({ id, version })),
        }),
      ],
      { type: "application/json" },
    ),
    { ...options, contentType: "application/json" },
  );
  if (!response.ok) throw await failure(response);
  const json = (response.headers.get("Content-Type") || "").includes("json");
  return {
    blob: await response.blob(),
    filename: downloadName(
      response.headers.get("Content-Disposition"),
      json ? "atlas-quiz-bank.json" : "atlas-quiz-bank.zip",
    ),
  };
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
