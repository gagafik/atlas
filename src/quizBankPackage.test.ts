import { afterEach, expect, it, vi } from "vitest";
import {
  MAX_SELECTION,
  downloadName,
  exportFailure,
  exportPackage,
  formatSize,
  packageError,
  previewSummary,
  refreshSelected,
  sendPackage,
  toggleSelected,
  type PackagePreview,
} from "./quizBankPackage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const MB = 1024 * 1024;

it("accepts only ZIP and JSON files inside the server limits", () => {
  expect(packageError({ name: "bank.rar", size: 10 })).toBe(
    "Выберите ZIP-пакет Атласа или JSON по шаблону.",
  );
  expect(packageError({ name: "bank.json", size: 3 * MB })).toBe(
    "JSON должен быть не больше 2 МБ.",
  );
  expect(packageError({ name: "bank.zip", size: 56 * MB })).toBe(
    "ZIP-пакет должен быть не больше 55 МБ.",
  );
  expect(packageError({ name: "bank.zip", size: 0 })).toBe(
    "Нельзя загрузить пустой файл.",
  );
  expect(packageError({ name: "x".repeat(177) + ".zip", size: 5 })).toContain(
    "180",
  );
  expect(packageError({ name: "Bank.ZIP", size: 1024 })).toBe("");
  expect(packageError({ name: "template.json", size: 2 * MB })).toBe("");
});

it("selects and unselects without mutating its input and stops at one hundred", () => {
  const empty = new Map<string, number>();
  const one = toggleSelected(empty, { id: "a", version: 2 });
  expect(one.error).toBe("");
  expect([...one.selected]).toEqual([["a", 2]]);
  expect(empty.size).toBe(0);
  const none = toggleSelected(one.selected, { id: "a", version: 2 });
  expect(none.selected.size).toBe(0);
  expect(one.selected.size).toBe(1);
  const full = new Map(
    Array.from(
      { length: MAX_SELECTION },
      (_, i) => [`q${i}`, 1] as [string, number],
    ),
  );
  const refused = toggleSelected(full, { id: "extra", version: 1 });
  expect(refused.error).toBe("Можно выбрать не больше 100 вопросов.");
  expect(refused.selected.has("extra")).toBe(false);
  expect(refused.selected.size).toBe(MAX_SELECTION);
  expect(full.size).toBe(MAX_SELECTION);
  expect(toggleSelected(full, { id: "q5", version: 1 }).selected.size).toBe(99);
});

it("formats sizes with Russian decimal commas", () => {
  expect(formatSize(1572864)).toBe("1,5 МБ");
  expect(formatSize(2 * MB)).toBe("2 МБ");
  expect(formatSize(1536)).toBe("1,5 КБ");
  expect(formatSize(900)).toBe("900 Б");
});

const preview: PackagePreview = {
  packageHash: "a".repeat(64),
  format: "zip",
  size: 100,
  questions: 3,
  archived: 1,
  files: 2,
  totalSize: 1572864,
  subjects: ["Физика", "Химия"],
  kinds: { essay: 3 },
  items: [],
};

it("summarizes a preview with the counts the teacher must confirm", () => {
  expect(previewSummary(preview)).toBe(
    "Вопросов: 3 (в архиве: 1) · предметы: Физика, Химия · файлов: 2, 1,5 МБ",
  );
  expect(
    previewSummary({
      ...preview,
      archived: 0,
      subjects: [],
      files: 0,
      totalSize: 0,
    }),
  ).toBe("Вопросов: 3 · файлов: 0");
});

it("reads the download name only from the plain filename form", () => {
  expect(
    downloadName(
      'attachment; filename="atlas-quiz-bank-2026-08-17.zip"',
      "x.zip",
    ),
  ).toBe("atlas-quiz-bank-2026-08-17.zip");
  expect(downloadName("attachment; filename*=utf-8''bank.zip", "x.zip")).toBe(
    "x.zip",
  );
  expect(downloadName(null, "x.zip")).toBe("x.zip");
  expect(downloadName('attachment; filename="../evil.zip"', "x.zip")).toBe(
    "x.zip",
  );
});

it("exports the selected question versions and saves the named file", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(new Blob(["zip"]), {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition":
          'attachment; filename="atlas-quiz-bank-2026-08-17.zip"',
      },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const result = await exportPackage(
    "demo",
    new Map([
      ["q1", 3],
      ["q2", 1],
    ]),
  );
  expect(result.filename).toBe("atlas-quiz-bank-2026-08-17.zip");
  expect(await result.blob.text()).toBe("zip");
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/journal/quiz-bank/export?workspace=demo");
  expect(init.method).toBe("POST");
  expect(init.headers["X-Atlas-Request"]).toBe("1");
  expect(init.headers["Content-Type"]).toBe("application/json");
  expect(JSON.parse(await init.body.text())).toEqual({
    items: [
      { id: "q1", version: 3 },
      { id: "q2", version: 1 },
    ],
  });
});

it("names a JSON export when the server sends no file name", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("{}", { headers: { "Content-Type": "application/json" } }),
      ),
  );
  expect((await exportPackage("demo", new Map([["q", 1]]))).filename).toBe(
    "atlas-quiz-bank.json",
  );
});

it("turns server errors into messages and falls back for unreadable bodies", async () => {
  const reply = (body: string, status: number) =>
    vi.fn().mockResolvedValue(new Response(body, { status }));
  vi.stubGlobal(
    "fetch",
    reply(
      JSON.stringify({ detail: "Вопрос отсутствует в доступном банке." }),
      403,
    ),
  );
  await expect(exportPackage("demo", new Map([["q", 1]]))).rejects.toThrow(
    "Вопрос отсутствует в доступном банке.",
  );
  vi.stubGlobal("fetch", reply("<html>Bad gateway</html>", 502));
  await expect(exportPackage("demo", new Map([["q", 1]]))).rejects.toThrow(
    "Не удалось выполнить действие.",
  );
  vi.stubGlobal(
    "fetch",
    reply(
      JSON.stringify({
        detail: [{ msg: "Слишком коротко" }, { msg: "Неверно" }],
      }),
      422,
    ),
  );
  await expect(
    sendPackage("import/preview", "demo", new File(["x"], "a.json"), {}),
  ).rejects.toThrow("Слишком коротко\nНеверно");
});

it("sends the raw file with the request identity in the address", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ imported: 2 }), { status: 200 }),
    );
  vi.stubGlobal("fetch", fetchMock);
  const file = new File(["{}"], "Банк 1.json");
  const requestId = "0123456789abcdef0123456789abcdef";
  const packageHash = "b".repeat(64);
  const result = await sendPackage("import", "demo", file, {
    requestId,
    packageHash,
  });
  expect(result).toEqual({ imported: 2 });
  const [url, init] = fetchMock.mock.calls[0];
  const query = new URL(url, "http://localhost").searchParams;
  expect(url.startsWith("/api/journal/quiz-bank/import?")).toBe(true);
  expect(query.get("workspace")).toBe("demo");
  expect(query.get("filename")).toBe("Банк 1.json");
  expect(query.get("requestId")).toBe(requestId);
  expect(query.get("packageHash")).toBe(packageHash);
  expect(init.method).toBe("POST");
  expect(init.body).toBe(file);
  expect(init.headers["X-Atlas-Request"]).toBe("1");
  expect(init.headers["Content-Type"]).toBe("application/octet-stream");
});

it("refreshes the versions of selected questions only and never adds or mutates", () => {
  const selected = new Map([
    ["a", 1],
    ["b", 4],
  ]);
  const saved = refreshSelected(selected, [
    { id: "a", version: 2 },
    { id: "other", version: 9 },
  ]);
  expect([...saved]).toEqual([
    ["a", 2],
    ["b", 4],
  ]);
  expect(selected.get("a")).toBe(1);
  expect(refreshSelected(selected, []).size).toBe(2);
});

it("keeps the selection exportable after the edit leaves the list", () => {
  // The saved question is archived, so the reloaded page no longer contains it;
  // the version returned by the save still has to reach the selection.
  const afterSave = refreshSelected(new Map([["q", 1]]), [
    { id: "q", version: 2 },
  ]);
  const reloadedPage = refreshSelected(afterSave, []);
  expect(reloadedPage.get("q")).toBe(2);
});

it("marks a refused export as stale only for conflicts", async () => {
  const refuse = (status: number, detail: string) =>
    vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ detail }), { status }));
  vi.stubGlobal(
    "fetch",
    refuse(409, "Вопрос в банке изменился. Обновите банк."),
  );
  const conflict = await exportPackage("demo", new Map([["q", 1]])).catch(
    (e) => e,
  );
  expect(conflict.status).toBe(409);
  expect(exportFailure(conflict)).toContain("Вопрос в банке изменился.");
  expect(exportFailure(conflict)).toContain("Снять выбор");
  vi.stubGlobal("fetch", refuse(403, "Вопрос отсутствует в доступном банке."));
  const denied = await exportPackage("demo", new Map([["q", 1]])).catch(
    (e) => e,
  );
  expect(denied.status).toBe(403);
  expect(exportFailure(denied)).toBe("Вопрос отсутствует в доступном банке.");
});

const busy = () =>
  new Response(
    JSON.stringify({
      detail: "Сервер принимает другие файлы. Повторите немного позже.",
    }),
    { status: 429, headers: { "Retry-After": "2" } },
  );

it("waits out a busy server and sends the same import again", async () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(busy())
    .mockResolvedValueOnce(new Response(JSON.stringify({ imported: 2 })));
  vi.stubGlobal("fetch", fetchMock);
  const onBusy = vi.fn();
  const file = new File(["{}"], "bank.json");
  const extra = { requestId: "a".repeat(32), packageHash: "b".repeat(64) };
  const sent = sendPackage("import", "demo", file, extra, { onBusy });
  await vi.advanceTimersByTimeAsync(1999);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(onBusy).toHaveBeenCalledWith(true);
  await vi.advanceTimersByTimeAsync(1);
  await expect(sent).resolves.toEqual({ imported: 2 });
  expect(onBusy.mock.calls).toEqual([[true], [false]]);
  // The retry carries the same request identity, so the server cannot import twice.
  expect(fetchMock.mock.calls[1][0]).toBe(fetchMock.mock.calls[0][0]);
  expect(fetchMock.mock.calls[1][0]).toContain("requestId=" + extra.requestId);
  expect(fetchMock.mock.calls[1][1].body).toBe(file);
});

it("waits out a busy server before an export too", async () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(busy())
    .mockResolvedValueOnce(
      new Response(new Blob(["zip"]), {
        headers: { "Content-Type": "application/zip" },
      }),
    );
  vi.stubGlobal("fetch", fetchMock);
  const onBusy = vi.fn();
  const exported = exportPackage("demo", new Map([["q", 1]]), { onBusy });
  await vi.advanceTimersByTimeAsync(2000);
  expect((await exported).filename).toBe("atlas-quiz-bank.zip");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(onBusy.mock.calls).toEqual([[true], [false]]);
});

it("does not retry a refusal that is not a busy answer", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "Лимит хранилища достигнут." }), {
      status: 413,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const error = await sendPackage(
    "import/preview",
    "demo",
    new File(["x"], "a.zip"),
    {},
  ).catch((e) => e);
  expect(error.message).toBe("Лимит хранилища достигнут.");
  expect(error.status).toBe(413);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
