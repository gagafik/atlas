import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { api } from "./api";

export type MessageFile = {
  id: string;
  filename: string;
  size: number;
  created: string;
};
export function messageFileUrl(workspace: string, id: string) {
  return `/api/messages/files/${id}?${new URLSearchParams({ workspace })}`;
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
  const [available, setAvailable] = useState<MessageFile[]>(items);
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
          accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx"
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
                const response = await fetch(`/api/messages/files?${params}`, {
                  method: "POST",
                  headers: {
                    "X-Atlas-Request": "1",
                    "Content-Type": "application/octet-stream",
                  },
                  body: file,
                  signal: controller.current.signal,
                });
                let result: any;
                try {
                  result = await response.json();
                } catch {
                  throw Error(
                    "Ответ загрузки не получен. Нажмите «Найти мои загрузки» перед повтором.",
                  );
                }
                if (!response.ok)
                  throw Error(
                    typeof result.detail === "string"
                      ? result.detail
                      : "Файл не принят.",
                  );
                if (!alive.current) return;
                setAvailable((old) => [...old, result]);
                setItems((old) => [...old, result]);
              }
            });
          }}
        />
        <small>До 5 файлов по 10 МБ. До отправки их видите только вы.</small>
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
