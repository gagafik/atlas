import { useRef, useState } from "react";
import Library from "./TeachingMaterialsLoader";
import { materialFileUrl, type MaterialSnapshot } from "./teachingMaterials";
import "./teachingMaterials.css";
import TeachingMediaPlayer from "./TeachingMediaPlayer";

export function MaterialView({
  item,
  workspace,
  lessonKey,
}: {
  item: MaterialSnapshot;
  workspace: string;
  lessonKey?: string;
}) {
  return (
    <article className="teaching-material-card">
      <h4>
        {item.title} <small>Версия {item.version}</small>
      </h4>
      {(item.subject || item.topics.length > 0) && (
        <p className="muted">
          {[item.subject, ...item.topics].filter(Boolean).join(" · ")}
        </p>
      )}
      {item.text && <p className="journal-text">{item.text}</p>}
      {item.files.length > 0 && (
        <ul className="homework-files">
          {item.files.map((file) => (
            <li key={file.id}>
              <TeachingMediaPlayer
                filename={file.filename}
                url={materialFileUrl(workspace, file.id, item, lessonKey)}
              />
              <a
                href={
                  materialFileUrl(workspace, file.id, item, lessonKey) +
                  "&download=true"
                }
              >
                {file.filename}
              </a>{" "}
              <small>{Math.max(1, Math.ceil(file.size / 1024))} КБ</small>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

const MAX_MATERIALS = 10;

export default function LessonMaterials({
  items,
  workspace,
  lessonKey,
  disabled,
  onChange,
}: {
  items: MaterialSnapshot[];
  workspace: string;
  lessonKey: string;
  disabled?: boolean;
  onChange?: (items: MaterialSnapshot[]) => void;
}) {
  const [choosing, setChoosing] = useState(false);
  const saved = useRef(
    new Set(items.map((item) => item.id + ":" + item.version)),
  );
  if (!items.length && !onChange) return null;
  if (!onChange) {
    // The family's view of the published materials.
    return (
      <section className="lesson-materials" aria-label="Материалы урока">
        <h3>Материалы урока</h3>
        {items.map((item) => (
          <div key={item.id}>
            <MaterialView
              item={item}
              workspace={workspace}
              lessonKey={lessonKey}
            />
          </div>
        ))}
      </section>
    );
  }
  // The teacher's editor: one row with the count and the button; the list appears only when there are materials.
  return (
    <section
      className="lesson-materials lesson-materials-editor"
      aria-label="Материалы урока"
    >
      <div className="lesson-row">
        <strong>
          Материалы урока · {items.length}/{MAX_MATERIALS}
        </strong>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setChoosing(!choosing)}
          aria-expanded={choosing}
        >
          {choosing ? "Закрыть выбор материала" : "Выбрать из библиотеки"}
        </button>
        <small className="lesson-hint">
          До десяти материалов; семья увидит их после публикации. Файлы к
          заданию добавляйте ниже.
        </small>
      </div>
      {items.length > 0 && (
        <ul className="lesson-material-list">
          {items.map((item) => (
            <li key={item.id}>
              <details>
                <summary>
                  {item.title} <small>версия {item.version}</small>
                </summary>
                <MaterialView
                  item={item}
                  workspace={workspace}
                  lessonKey={
                    saved.current.has(item.id + ":" + item.version)
                      ? lessonKey
                      : undefined
                  }
                />
              </details>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(items.filter((m) => m.id !== item.id))}
              >
                Убрать «{item.title}» из урока
              </button>
            </li>
          ))}
        </ul>
      )}
      {choosing && !disabled && (
        <Library
          workspace={workspace}
          onChoose={(item) => {
            if (
              items.length >= MAX_MATERIALS &&
              !items.some((m) => m.id === item.id)
            ) {
              window.alert("В урок можно добавить до десяти материалов.");
              return;
            }
            onChange?.([...items.filter((m) => m.id !== item.id), item]);
            setChoosing(false);
          }}
        />
      )}
    </section>
  );
}
