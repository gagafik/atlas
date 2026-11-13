import { afterEach, expect, it, vi } from "vitest";
import {
  MAX_JSON,
  MAX_PACKAGE,
  MAX_SELECTION,
  downloadName,
  exportFailure,
  exportPackage,
  packageError,
  refreshSelected,
  sendPackage,
  toggleSelected,
} from "./quizBankPackage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const MB = 1024 * 1024;

const names = (n: number) => "x".repeat(n - 4) + ".zip";
it.each([
  { label: "a RAR file", name: "bank.rar", size: 10 },
  {
    label: "a JSON file over its limit",
    name: "bank.json",
    size: MAX_JSON + 1,
  },
  {
    label: "a ZIP file over its limit",
    name: "bank.zip",
    size: MAX_PACKAGE + 1,
  },
  { label: "an empty file", name: "bank.zip", size: 0 },
  { label: "a name of 181 characters", name: names(181), size: 5 },
])("refuses $label", ({ name, size }) => {
  expect(packageError({ name, size })).not.toBe("");
});

it.each([
  { label: "a ZIP in capitals", name: "Bank.ZIP", size: 1024 },
  { label: "a JSON file at its limit", name: "template.json", size: MAX_JSON },
])("accepts $label", ({ name, size }) => {
  expect(packageError({ name, size })).toBe("");
});

const fullSelection = () =>
  new Map(
    Array.from(
      { length: MAX_SELECTION },
      (_, i) => [`q${i}`, 1] as [string, number],
    ),
  );

it("selects and unselects without mutating its input", () => {
  const empty = new Map<string, number>();
  const one = toggleSelected(empty, { id: "a", version: 2 });
  expect(one.error).toBe("");
  expect([...one.selected]).toEqual([["a", 2]]);
  expect(empty.size).toBe(0);
  const none = toggleSelected(one.selected, { id: "a", version: 2 });
  expect(none.selected.size).toBe(0);
  expect(one.selected.size).toBe(1);
});

it("refuses a question beyond the selection limit", () => {
  const full = fullSelection();
  const refused = toggleSelected(full, { id: "extra", version: 1 });
  expect(refused.error).not.toBe("");
  expect(refused.selected.has("extra")).toBe(false);
  expect(refused.selected.size).toBe(MAX_SELECTION);
  expect(full.size).toBe(MAX_SELECTION);
});

it("lets a question go from a full selection", () => {
  const next = toggleSelected(fullSelection(), { id: "q5", version: 1 });
  expect(next.selected.size).toBe(MAX_SELECTION - 1);
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
  expect(url).toContain("workspace=demo");
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

const reply = (body: string, status: number) =>
  vi.fn().mockResolvedValue(new Response(body, { status }));

it("carries the server's message and status of a refused export", async () => {
  vi.stubGlobal(
    "fetch",
    reply(
      JSON.stringify({ detail: "Вопрос отсутствует в доступном банке." }),
      403,
    ),
  );
  await expect(
    exportPackage("demo", new Map([["q", 1]])),
  ).rejects.toMatchObject({
    message: "Вопрос отсутствует в доступном банке.",
    status: 403,
  });
});

it("gives an unreadable error body a message and keeps the status", async () => {
  vi.stubGlobal("fetch", reply("<html>Bad gateway</html>", 502));
  const error = await exportPackage("demo", new Map([["q", 1]])).catch(
    (e) => e,
  );
  expect(error.message).not.toBe("");
  expect(error.status).toBe(502);
});

it("shows every message of a refused import", async () => {
  vi.stubGlobal(
    "fetch",
    reply(
      JSON.stringify({
        detail: [{ msg: "Слишком коротко" }, { msg: "Неверно" }],
      }),
      422,
    ),
  );
  const error = await sendPackage(
    "import/preview",
    "demo",
    new File(["x"], "a.json"),
    {},
  ).catch((e) => e);
  expect(error.message).toContain("Слишком коротко");
  expect(error.message).toContain("Неверно");
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
  expect(query.get("workspace")).toBe("demo");
  expect(query.get("filename")).toBe("Банк 1.json");
  expect(query.get("requestId")).toBe(requestId);
  expect(query.get("packageHash")).toBe(packageHash);
  expect(init.body).toBe(file);
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

it("adds advice to a conflict of an export and leaves other refusals as they are", () => {
  const advised = exportFailure({ message: "Вопрос изменился.", status: 409 });
  expect(advised.startsWith("Вопрос изменился.")).toBe(true);
  expect(advised.length).toBeGreaterThan("Вопрос изменился.".length);
  expect(exportFailure({ message: "Нет доступа.", status: 403 })).toBe(
    "Нет доступа.",
  );
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
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(busy())
    .mockResolvedValueOnce(new Response(JSON.stringify({ imported: 2 })));
  vi.stubGlobal("fetch", fetchMock);
  const file = new File(["{}"], "bank.json");
  const extra = { requestId: "a".repeat(32), packageHash: "b".repeat(64) };
  const sent = sendPackage("import", "demo", file, extra);
  await vi.runAllTimersAsync();
  await expect(sent).resolves.toEqual({ imported: 2 });
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
