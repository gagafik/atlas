import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { api } from "./api";
import { uploadFile } from "./uploads";
import { inlineImage } from "./messages";

export type MessageFile = {
  id: string;
  filename: string;
  size: number;
  created: string;
};
export function messageFileUrl(workspace: string, id: string, inline = false) {
  return `/api/messages/files/${id}?${new URLSearchParams(inline ? { workspace, inline: "true" } : { workspace })}`;
}
/** Files of a posted message. A picture the browser can show appears above its link; where it cannot be decoded, the link remains. */
export function MessageFileList({
  workspace,
  files,
}: {
  workspace: string;
  files: MessageFile[];
}) {
  const [failed, setFailed] = useState<string[]>([]);
  if (!files.length) return null;
  return (
    <ul className="homework-files">
      {files.map((f) => (
        <li key={f.id}>
          {inlineImage(f.filename) && !failed.includes(f.id) && (
            <img
              loading="lazy"
              alt={f.filename}
              className="message-image"
              src={messageFileUrl(workspace, f.id, true)}
              onError={() => setFailed((old) => [...old, f.id])}
            />
          )}
          <a href={messageFileUrl(workspace, f.id)} download>
            {f.filename}
          </a>{" "}
          · {Math.max(1, Math.ceil(f.size / 1024))} КБ
        </li>
      ))}
    </ul>
  );
}
export default function MessageAttachments({
  workspace,
  draftId,
  threadId,
  items,
  setItems,
  busy,
  run,
}: {
  workspace: string;
  draftId: string;
  threadId?: string;
  items: MessageFile[];
  setItems: Dispatch<SetStateAction<MessageFile[]>>;
  busy: boolean;
  run: (fn: () => Promise<void>) => Promise<void>;
}) {
  const [available, setAvailable] = useState<MessageFile[]>(items),
    [serverBusy, setServerBusy] = useState(false);
  const alive = useRef(true),
    controller = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  return (
    <fieldset className="message-attachments" disabled={busy}>
      <legend>Вложения</legend>
      <label className="field">
        <span>Приложить файлы</span>
        <input
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg,.heic,.heif,.txt,.doc,.docx,.xls,.xlsx,.pptx"
          disabled={items.length >= 5}
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            e.target.value = "";
            if (!files.length) return;
            void run(async () => {
              if (files.length + items.length > 5)
                throw Error("В сообщении может быть не больше пяти файлов.");
              if (files.some((f) => f.size > 10 * 1024 * 1024 || !f.size))
                throw Error("Выберите непустые файлы размером до 10 МБ.");
              controller.current = new AbortController();
              for (const file of files) {
                const params = new URLSearchParams({
                  workspace,
                  draftId,
                  filename: file.name,
                });
                if (threadId) params.set("threadId", threadId);
                const result = await uploadFile(
                  `/api/messages/files?${params}`,
                  file,
                  {
                    signal: controller.current.signal,
                    onBusy: (b) => alive.current && setServerBusy(b),
                    unreadable:
                      "Ответ загрузки не получен. Нажмите «Найти мои загрузки» перед повтором.",
                  },
                );
                if (!alive.current) return;
                setAvailable((old) => [...old, result]);
                setItems((old) => [...old, result]);
              }
            });
          }}
        />
        <small>
          До 5 файлов по 10 МБ: фото (JPG, PNG, HEIC, HEIF), PDF, TXT, Word
          (DOC, DOCX), Excel (XLS, XLSX) и PPTX. До отправки их видите только
          вы.
        </small>
        {serverBusy && <small role="status">Сервер занят, повторяем…</small>}
      </label>
      {!!available.length && (
        <div className="message-file-choices">
          {available.map((file) => (
            <div key={file.id}>
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={items.some((f) => f.id === file.id)}
                  disabled={
                    !items.some((f) => f.id === file.id) && items.length >= 5
                  }
                  onChange={(e) =>
                    setItems((old) =>
                      e.target.checked
                        ? [...old, file]
                        : old.filter((f) => f.id !== file.id),
                    )
                  }
                />
                <span>
                  {file.filename} · {Math.max(1, Math.ceil(file.size / 1024))}{" "}
                  КБ
                </span>
              </label>
              <a href={messageFileUrl(workspace, file.id)} download>
                Скачать
              </a>
              <button
                onClick={() =>
                  void run(async () => {
                    await api(`messages/files/${file.id}`, workspace, "DELETE");
                    if (alive.current) {
                      setAvailable((old) =>
                        old.filter((f) => f.id !== file.id),
                      );
                      setItems((old) => old.filter((f) => f.id !== file.id));
                    }
                  })
                }
              >
                Удалить
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        onClick={() =>
          void run(async () => {
            const r = await api(`messages/files?draftId=${draftId}`, workspace);
            if (alive.current) {
              setAvailable(r.items);
              setItems((old) =>
                old.filter((f) =>
                  r.items.some((item: MessageFile) => item.id === f.id),
                ),
              );
            }
          })
        }
      >
        Найти мои загрузки
      </button>
    </fieldset>
  );
}
