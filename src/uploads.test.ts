import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  downscalePhoto,
  uploadFile,
  uploadWithRetry,
  type ImageTools,
} from "./uploads";

const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), { status, headers });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
const settle = <T>(promise: Promise<T>) =>
  promise.then(
    () => null,
    (error: any) => error,
  );

describe("uploadFile", () => {
  it("retries a busy answer after Retry-After and reports the busy state", async () => {
    fetchMock
      .mockResolvedValueOnce(
        json({ detail: "busy" }, 429, { "Retry-After": "2" }),
      )
      .mockResolvedValueOnce(json({ id: "x" }));
    const onBusy = vi.fn();
    const body = new Blob(["file"]);
    const promise = uploadFile("/api/up", body, { onBusy });
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onBusy).toHaveBeenCalledWith(true);
    await vi.advanceTimersByTimeAsync(1);
    await expect(promise).resolves.toEqual({ id: "x" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onBusy.mock.calls).toEqual([[true], [false]]);
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/up");
    expect(init).toMatchObject({
      method: "POST",
      body,
      headers: {
        "X-Atlas-Request": "1",
        "Content-Type": "application/octet-stream",
      },
    });
  });

  it("retries a 503 with Retry-After and uses the given content type", async () => {
    fetchMock
      .mockResolvedValueOnce(
        json({ detail: "db" }, 503, { "Retry-After": "1" }),
      )
      .mockResolvedValueOnce(json({ ok: true }));
    const promise = uploadFile("/u", new Blob(["a"]), {
      contentType: "image/png",
    });
    await vi.advanceTimersByTimeAsync(1000);
    await expect(promise).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls[1][1].headers["Content-Type"]).toBe(
      "image/png",
    );
  });

  it("backs off further on repeated busy answers", async () => {
    fetchMock.mockImplementation(async () =>
      json({ detail: "busy" }, 429, { "Retry-After": "1" }),
    );
    const outcome = settle(uploadFile("/u", new Blob(["a"]), { attempts: 4 }));
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(4000);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect((await outcome).message).toBe("busy");
  });

  it("does not retry a 429 without Retry-After", async () => {
    fetchMock.mockResolvedValue(
      json({ detail: "Слишком много попыток." }, 429),
    );
    const error = await settle(uploadFile("/u", new Blob(["a"])));
    expect(error.message).toBe("Слишком много попыток.");
    expect(error.status).toBe(429);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("throws the server detail with the status for other errors", async () => {
    fetchMock.mockResolvedValue(
      json({ detail: "Файл должен быть не больше 10 МБ." }, 413),
    );
    const error = await settle(uploadFile("/u", new Blob(["a"])));
    expect(error.message).toBe("Файл должен быть не больше 10 МБ.");
    expect(error.status).toBe(413);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("uses the fallback when the detail is not text", async () => {
    fetchMock.mockResolvedValue(json({ detail: [{ msg: "bad" }] }, 422));
    const error = await settle(
      uploadFile("/u", new Blob(["a"]), { fallback: "Материал не принят." }),
    );
    expect(error.message).toBe("Материал не принят.");
    expect(error.status).toBe(422);
  });

  it("gives up after the allowed attempts and shows the last server answer", async () => {
    fetchMock.mockImplementation(async () =>
      json({ detail: "Сервер принимает другие файлы." }, 429, {
        "Retry-After": "1",
      }),
    );
    const onBusy = vi.fn();
    const outcome = settle(
      uploadFile("/u", new Blob(["a"]), { attempts: 3, onBusy }),
    );
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(2000);
    const error = await outcome;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(error.message).toBe("Сервер принимает другие файлы.");
    expect(onBusy).toHaveBeenLastCalledWith(false);
  });

  it("stops waiting at once when aborted and sends nothing more", async () => {
    fetchMock.mockImplementation(async () =>
      json({ detail: "busy" }, 429, { "Retry-After": "5" }),
    );
    const controller = new AbortController();
    const onBusy = vi.fn();
    const outcome = settle(
      uploadFile("/u", new Blob(["a"]), { signal: controller.signal, onBusy }),
    );
    await vi.advanceTimersByTimeAsync(100);
    controller.abort();
    const error = await outcome;
    expect(error.name).toBe("AbortError");
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onBusy).toHaveBeenLastCalledWith(false);
  });

  it("never retries a network error: the file may already be stored", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await settle(uploadFile("/u", new Blob(["a"])));
    expect(error).toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports an unreadable answer", async () => {
    fetchMock.mockResolvedValue(new Response("<html>", { status: 200 }));
    const error = await settle(uploadFile("/u", new Blob(["a"])));
    expect(error.message).toBe("Сервер не принял файл. Повторите загрузку.");
    fetchMock.mockResolvedValue(new Response("<html>", { status: 502 }));
    const custom = await settle(
      uploadFile("/u", new Blob(["a"]), { unreadable: "Ответ не получен." }),
    );
    expect(custom.message).toBe("Ответ не получен.");
  });
});

describe("uploadWithRetry", () => {
  it("returns the response untouched for the caller to interpret", async () => {
    const response = json({ detail: "x" }, 409);
    fetchMock.mockResolvedValue(response);
    await expect(uploadWithRetry("/u", new Blob(["a"]))).resolves.toBe(
      response,
    );
  });
});

describe("downscalePhoto", () => {
  const photo = (name: string, size: number, type = "image/jpeg") =>
    new File([new Uint8Array(size)], name, { type, lastModified: 123 });
  function fake(width: number, height: number, produced = 1_000_000) {
    const draw = vi.fn(
      async (_w: number, _h: number): Promise<Blob | null> =>
        new Blob([new Uint8Array(produced)]),
    );
    const close = vi.fn();
    const decode = vi.fn(async () => ({ width, height, draw, close }));
    return { tools: { decode } as ImageTools, draw, close, decode };
  }
  const MB = 1024 * 1024;

  it("shrinks a large camera JPEG to 2560 px and re-encodes it as JPEG", async () => {
    const { tools, draw, close } = fake(4000, 3000);
    const original = photo("photo.JPG", 5 * MB);
    const result = await downscalePhoto(original, undefined, tools);
    expect(draw).toHaveBeenCalledWith(2560, 1920);
    expect(result).not.toBe(original);
    expect(result.name).toBe("photo.jpg");
    expect(result.type).toBe("image/jpeg");
    expect(result.lastModified).toBe(123);
    expect(result.size).toBe(1_000_000);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("keeps a portrait photo portrait", async () => {
    const { tools, draw } = fake(3000, 4000);
    await downscalePhoto(photo("p.jpg", 5 * MB), undefined, tools);
    expect(draw).toHaveBeenCalledWith(1920, 2560);
  });

  it("re-encodes a heavy photo that is already small in pixels, without enlarging", async () => {
    const { tools, draw } = fake(2000, 1500);
    const result = await downscalePhoto(
      photo("heavy.jpeg", 4 * MB),
      undefined,
      tools,
    );
    expect(draw).toHaveBeenCalledWith(2000, 1500);
    expect(result.name).toBe("heavy.jpg");
  });

  it("leaves a small photo untouched", async () => {
    const { tools, draw, close } = fake(1200, 900);
    const original = photo("small.jpg", 1 * MB);
    expect(await downscalePhoto(original, undefined, tools)).toBe(original);
    expect(draw).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("does not touch PDFs and other documents", async () => {
    const { tools, decode } = fake(4000, 3000);
    const pdf = photo("scan.pdf", 9 * MB, "application/pdf");
    expect(await downscalePhoto(pdf, undefined, tools)).toBe(pdf);
    const docx = photo("work.docx", 9 * MB, "");
    expect(await downscalePhoto(docx, undefined, tools)).toBe(docx);
    const mismatch = photo("picture.jpg", 9 * MB, "application/pdf");
    expect(await downscalePhoto(mismatch, undefined, tools)).toBe(mismatch);
    expect(decode).not.toHaveBeenCalled();
  });

  it("accepts an empty type for a known image extension", async () => {
    const { tools, draw } = fake(4000, 3000);
    await downscalePhoto(photo("camera.png", 5 * MB, ""), undefined, tools);
    expect(draw).toHaveBeenCalled();
  });

  it("keeps the original when decoding fails", async () => {
    const decode = vi.fn(async () => {
      throw new Error("HEIC renamed to .jpg");
    });
    const original = photo("iphone.jpg", 12 * MB);
    expect(await downscalePhoto(original, undefined, { decode })).toBe(
      original,
    );
  });

  it("keeps the original when drawing fails or is not smaller", async () => {
    const original = photo("a.jpg", 5 * MB);
    const failing = fake(4000, 3000);
    failing.draw.mockRejectedValue(new Error("out of memory"));
    expect(await downscalePhoto(original, undefined, failing.tools)).toBe(
      original,
    );
    expect(failing.close).toHaveBeenCalledTimes(1);
    const empty = fake(4000, 3000);
    empty.draw.mockResolvedValue(null);
    expect(await downscalePhoto(original, undefined, empty.tools)).toBe(
      original,
    );
    const bigger = fake(4000, 3000, 6 * MB);
    expect(await downscalePhoto(original, undefined, bigger.tools)).toBe(
      original,
    );
  });

  it("keeps the original when the browser has no image tools", async () => {
    const original = photo("a.jpg", 12 * MB);
    expect(await downscalePhoto(original, undefined, null)).toBe(original);
  });

  it("names a re-encoded PNG screenshot .jpg", async () => {
    const { tools, draw } = fake(3000, 1000);
    const result = await downscalePhoto(
      photo("scan.PNG", 4 * MB, "image/png"),
      undefined,
      tools,
    );
    expect(draw).toHaveBeenCalledWith(2560, 853);
    expect(result.name).toBe("scan.jpg");
    expect(result.type).toBe("image/jpeg");
  });

  it("still closes the image when closing throws", async () => {
    const { tools, close } = fake(4000, 3000);
    close.mockImplementation(() => {
      throw new Error("detached");
    });
    const result = await downscalePhoto(
      photo("a.jpg", 5 * MB),
      undefined,
      tools,
    );
    expect(result.name).toBe("a.jpg");
    expect(result.size).toBe(1_000_000);
  });
});
