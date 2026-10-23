export function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((x: any) => String(x?.msg ?? "").replace(/^Value error, /, ""))
      .join("\n");
  return "Не удалось выполнить действие.";
}
export async function api(
  path: string,
  workspace: string,
  method = "GET",
  body?: unknown,
) {
  const response = await fetch(
    `/api/${path}${path.includes("?") ? "&" : "?"}workspace=${encodeURIComponent(workspace)}`,
    {
      method,
      headers: { "Content-Type": "application/json", "X-Atlas-Request": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
  );
  let result: any;
  try {
    result = await response.json();
  } catch {
    throw new Error("Сервер недоступен. Запустите приложение через start.ps1.");
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("auth/"))
      window.dispatchEvent(new Event("atlas-auth-required"));
    throw Object.assign(new Error(errorText(result.detail)), {
      status: response.status,
    });
  }
  return result;
}
