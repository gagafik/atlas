/** Uploads retry only when the server says it is busy (Retry-After). */
export interface UploadOptions {
  contentType?: string;
  signal?: AbortSignal;
  /** True while the client waits to retry after a busy answer; false when it finishes. */
  onBusy?: (busy: boolean) => void;
  attempts?: number;
}

function pause(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => new DOMException("Загрузка отменена.", "AbortError");
    if (signal?.aborted) return reject(signal.reason ?? abort());
    const stop = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? abort());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", stop);
      resolve();
    }, ms);
    signal?.addEventListener("abort", stop, { once: true });
  });
}

/**
 * POSTs a file. A 429/503 that carries Retry-After is retried with backoff and
 * jitter; any other answer, and every network error, is returned or thrown at
 * once: the server may already have stored the file, so a blind retry could
 * duplicate it.
 */
export async function uploadWithRetry(
  url: string,
  body: Blob,
  options: UploadOptions = {},
): Promise<Response> {
  const attempts = Math.max(1, options.attempts ?? 6);
  try {
    for (let n = 0; ; n++) {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "X-Atlas-Request": "1",
          "Content-Type": options.contentType ?? "application/octet-stream",
        },
        body,
        signal: options.signal,
      });
      const retryAfter = response.headers.get("Retry-After");
      if (
        (response.status === 429 || response.status === 503) &&
        retryAfter &&
        n + 1 < attempts
      ) {
        const seconds = parseInt(retryAfter, 10);
        const wait =
          Math.min(
            30000,
            Math.max(
              (Number.isNaN(seconds) ? 5 : seconds) * 1000,
              1000 * 2 ** n,
            ),
          ) *
          (0.8 + Math.random() * 0.4);
        options.onBusy?.(true);
        await pause(wait, options.signal);
        continue;
      }
      return response;
    }
  } finally {
    options.onBusy?.(false);
  }
}

/** Uploads and parses the JSON answer; errors carry the server's message and `status`. */
export async function uploadFile<T = any>(
  url: string,
  body: Blob,
  options: UploadOptions & { fallback?: string; unreadable?: string } = {},
): Promise<T> {
  const response = await uploadWithRetry(url, body, options);
  let result: any;
  try {
    result = await response.json();
  } catch {
    throw new Error(
      options.unreadable ?? "Сервер не принял файл. Повторите загрузку.",
    );
  }
  if (!response.ok)
    throw Object.assign(
      new Error(
        typeof result?.detail === "string"
          ? result.detail
          : (options.fallback ?? "Файл не принят."),
      ),
      { status: response.status },
    );
  return result;
}

export interface DecodedImage {
  width: number;
  height: number;
  draw(width: number, height: number): Promise<Blob | null>;
  close(): void;
}
export interface ImageTools {
  decode(file: Blob): Promise<DecodedImage>;
}
export interface PhotoLimits {
  maxSide: number;
  maxBytes: number;
  quality: number;
}
const PHOTO_LIMITS: PhotoLimits = {
  maxSide: 2560,
  maxBytes: 3 * 1024 * 1024,
  quality: 0.85,
};

/** createImageBitmap bakes the EXIF orientation into the pixels; re-encoding drops the metadata. */
export function browserImageTools(quality = 0.85): ImageTools | null {
  if (typeof createImageBitmap !== "function") return null;
  return {
    async decode(file) {
      const bitmap = await createImageBitmap(file, {
        imageOrientation: "from-image",
      });
      return {
        width: bitmap.width,
        height: bitmap.height,
        async draw(width, height) {
          const options = { type: "image/jpeg", quality };
          const paint = (
            context:
              CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
          ) => {
            context.fillStyle = "#fff"; // JPEG has no alpha: PNG transparency becomes white.
            context.fillRect(0, 0, width, height);
            context.drawImage(bitmap, 0, 0, width, height);
          };
          if (typeof OffscreenCanvas === "function") {
            const context = new OffscreenCanvas(width, height);
            const pen = context.getContext("2d");
            if (!pen) return null;
            paint(pen);
            return context.convertToBlob(options);
          }
          const canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
          const pen = canvas.getContext("2d");
          if (!pen) return null;
          paint(pen);
          return new Promise<Blob | null>((resolve) =>
            canvas.toBlob(resolve, options.type, options.quality),
          );
        },
        close: () => bitmap.close(),
      };
    },
  };
}

/**
 * Shrinks a large JPEG/PNG photo before upload (long side above maxSide or size
 * above maxBytes) to a JPEG. Anything else, any failure, and any result that is
 * not smaller than the original keeps the original file.
 */
export async function downscalePhoto(
  file: File,
  limits: PhotoLimits = PHOTO_LIMITS,
  tools: ImageTools | null = browserImageTools(limits.quality),
): Promise<File> {
  if (
    !tools ||
    !/\.(jpe?g|png)$/i.test(file.name) ||
    !["image/jpeg", "image/png", ""].includes(file.type)
  )
    return file;
  let image: DecodedImage | null = null;
  try {
    image = await tools.decode(file);
    const longest = Math.max(image.width, image.height);
    if (
      !(longest > 0) ||
      (longest <= limits.maxSide && file.size <= limits.maxBytes)
    )
      return file;
    const scale = Math.min(1, limits.maxSide / longest);
    const blob = await image.draw(
      Math.max(1, Math.round(image.width * scale)),
      Math.max(1, Math.round(image.height * scale)),
    );
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", {
      type: "image/jpeg",
      lastModified: file.lastModified,
    });
  } catch {
    return file;
  } finally {
    try {
      image?.close();
    } catch {
      /* the original upload must not fail because of cleanup */
    }
  }
}
