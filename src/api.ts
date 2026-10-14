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
    const detail = result.detail;
    throw Object.assign(
      new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((x: any) => x.msg).join("\n")
            : "Не удалось выполнить действие.",
      ),
      { status: response.status },
    );
  }
  return result;
}
