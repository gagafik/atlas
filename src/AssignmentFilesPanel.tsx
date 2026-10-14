import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { JournalEntry } from "./journal";

export interface Material {
  id: string;
  filename: string;
  size: number;
  created: string;
}

export function SavedMaterialLinks({
  ids,
  workspace,
  lessonKey,
  day,
}: {
  ids: string[];
  workspace: string;
  lessonKey: string;
  day: string;
}) {
  const [items, setItems] = useState<Material[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api(
      `journal/assignment-files?${new URLSearchParams({ lessonKey, day })}`,
      workspace,
    )
      .then((r) => {
        if (live) {
          setItems(r.items);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [lessonKey, day, workspace]);
  return (
    <>
      {error && <p role="alert">{error}</p>}
      <MaterialLinks
        items={items.filter((f) => ids.includes(f.id))}
        workspace={workspace}
      />
    </>
  );
}

export function MaterialLinks({
  items,
  workspace,
  studentId,
}: {
  items: Material[];
  workspace: string;
  studentId?: string;
}) {
  if (!items.length) return null;
  return (
    <ul className="homework-files">
      {items.map((f) => (
        <li key={f.id}>
          <a
            download
            href={`/api/journal/assignment-files/${f.id}?${new URLSearchParams({ workspace, ...(studentId ? { studentId } : {}) })}`}
          >
            {f.filename}
          </a>
          <small> · {Math.max(1, Math.ceil(f.size / 1024))} КБ</small>
        </li>
      ))}
    </ul>
  );
}

export default function AssignmentFilesPanel({
  entry,
  workspace,
  disabled,
  change,
  onUploading,
}: {
  entry: JournalEntry;
  workspace: string;
  disabled: boolean;
  change: (ids: string[]) => void;
  onUploading: (busy: boolean) => void;
}) {
  const [items, setItems] = useState<Material[]>([]),
    [error, setError] = useState(""),
    [uploading, setUploading] = useState(false);
  const controller = useRef<AbortController | null>(null),
    locked = useRef(false),
    alive = useRef(true);
  const selected = entry.document.content.homeworkFiles || [];
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    let live = true;
    api(
      `journal/assignment-files?${new URLSearchParams({ lessonKey: entry.lessonKey, day: entry.document.lesson.date })}`,
      workspace,
    )
      .then((r) => {
        if (live) {
          setItems(r.items);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [entry.lessonKey, entry.version, entry.document.lesson.date, workspace]);
  async function upload(picked: File[]) {
    if (!picked.length || locked.current || disabled) return;
    locked.current = true;
    setUploading(true);
    onUploading(true);
    setError("");
    controller.current = new AbortController();
    try {
      if (picked.length + selectedRef.current.length > 5)
        throw Error("К заданию можно приложить до пяти материалов.");
      if (picked.some((f) => !f.size || f.size > 10 * 1024 * 1024))
        throw Error("Каждый файл должен быть непустым и не больше 10 МБ.");
      for (const f of picked) {
        const params = new URLSearchParams({
          workspace,
          lessonKey: entry.lessonKey,
          day: entry.document.lesson.date,
          revision: String(entry.revision),
          version: String(entry.version),
          filename: f.name,
        });
        const response = await fetch(
          `/api/journal/assignment-files?${params}`,
          {
            method: "POST",
            headers: {
              "X-Atlas-Request": "1",
              "Content-Type": "application/octet-stream",
            },
            body: f,
            signal: controller.current.signal,
          },
        );
        const result = await response.json();
        if (!response.ok)
          throw Error(
            typeof result.detail === "string"
              ? result.detail
              : "Материал не принят.",
          );
        if (!alive.current) return;
        setItems((old) => [...old, result]);
        selectedRef.current = [...selectedRef.current, result.id];
        change(selectedRef.current);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message || "Загрузка прервана.");
    } finally {
      locked.current = false;
      if (alive.current) {
        setUploading(false);
        onUploading(false);
      }
    }
  }
  return (
    <div className="assignment-materials">
      <h3>Материалы домашнего задания</h3>
      <p className="muted">
        До пяти файлов по 10 МБ. Ученики и родители увидят выбранные материалы
        после публикации журнала. Прежние версии сохраняются в истории.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {uploading && <p role="status">Загружаю материалы…</p>}
      <label className="field">
        <span>Добавить материалы задания</span>
        <input
          type="file"
          multiple
          disabled={
            disabled ||
            uploading ||
            selected.length >= 5 ||
            !entry.document.content.homework.trim()
          }
          accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx"
          onChange={(e) => {
            const picked = Array.from(e.target.files || []);
            e.target.value = "";
            void upload(picked);
          }}
        />
      </label>
      {!entry.document.content.homework.trim() && (
        <p className="muted">Сначала добавьте описание домашнего задания.</p>
      )}
      <MaterialLinks
        items={items.filter((f) => selected.includes(f.id))}
        workspace={workspace}
      />
      {!!items.length && (
        <details>
          <summary>Выбрать из загруженных материалов ({items.length})</summary>
          {items.map((f) => (
            <label className="check-line" key={f.id}>
              <input
                type="checkbox"
                checked={selected.includes(f.id)}
                disabled={
                  disabled ||
                  uploading ||
                  !entry.document.content.homework.trim() ||
                  (!selected.includes(f.id) && selected.length >= 5)
                }
                onChange={(e) =>
                  change(
                    e.target.checked
                      ? [...selected, f.id]
                      : selected.filter((id) => id !== f.id),
                  )
                }
              />
              {f.filename}
            </label>
          ))}
        </details>
      )}
    </div>
  );
}
