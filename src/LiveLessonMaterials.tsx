import { useState } from "react";
import TeachingMaterialsPanel from "./TeachingMaterialsLoader";
import type { MaterialSnapshot } from "./teachingMaterials";
import "./LiveLessonMedia.css";
import TeachingMediaPlayer from "./TeachingMediaPlayer";

export type MaterialPart = "materials" | "solutionMaterials";
export type QuestionMaterials = {
  materials?: MaterialSnapshot[];
  solutionMaterials?: MaterialSnapshot[];
};

export function MaterialEditor({
  workspace,
  question,
  onChange,
}: {
  workspace: string;
  question: QuestionMaterials;
  onChange: (part: MaterialPart, items: MaterialSnapshot[]) => void;
}) {
  const [picker, setPicker] = useState<MaterialPart | null>(null);
  return (
    <div className="live-material-editor">
      {(["materials", "solutionMaterials"] as const).map((part) => (
        <div key={part}>
          <strong>
            {part === "materials" ? "Материалы задания" : "Материалы разбора"}
          </strong>
          <ul>
            {(question[part] || []).map((m) => (
              <li key={m.id}>
                {m.title} · версия {m.version}{" "}
                <button
                  onClick={() =>
                    onChange(
                      part,
                      question[part]!.filter((x) => x.id !== m.id),
                    )
                  }
                >
                  Убрать материал
                </button>
              </li>
            ))}
          </ul>
          <button
            disabled={(question[part]?.length || 0) >= 3}
            onClick={() => setPicker(part)}
          >
            Добавить {part === "materials" ? "к заданию" : "к разбору"}
          </button>
        </div>
      ))}
      {picker && (
        <section aria-label="Выбор материала">
          <p>
            До трёх материалов. Разбор откроется ученикам только по команде
            учителя. Подготовить новые материалы можно во вкладке «Библиотека
            материалов».
          </p>
          <button onClick={() => setPicker(null)}>
            Закрыть выбор материалов
          </button>
          <TeachingMaterialsPanel
            workspace={workspace}
            onChoose={(m) => {
              const items = question[picker] || [];
              onChange(
                picker,
                [...items.filter((x) => x.id !== m.id), m].slice(0, 3),
              );
              setPicker(null);
            }}
          />
        </section>
      )}
    </div>
  );
}

export function LiveMaterials({
  items = [],
  workspace,
  lesson,
  index,
  part = "materials",
  projection = false,
}: {
  items?: MaterialSnapshot[];
  workspace: string;
  lesson: string;
  index: number;
  part?: MaterialPart;
  projection?: boolean;
}) {
  return (
    <div className="live-materials">
      {items.map((m) => (
        <section key={`${m.id}:${m.version}`}>
          <h4>{m.title}</h4>
          {m.text && <p className="live-media-text">{m.text}</p>}
          {m.files.map((f) => {
            const url = `/api/live-lessons/${encodeURIComponent(lesson)}/files/${encodeURIComponent(f.id)}?${new URLSearchParams({ workspace, questionIndex: String(index), part, projection: String(projection) })}`;
            return (
              <div key={f.id}>
                <TeachingMediaPlayer
                  key={url}
                  filename={f.filename}
                  url={url}
                />
                {/\.(png|jpe?g)$/i.test(f.filename) && (
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    <img src={url} alt={f.filename} loading="lazy" />
                  </a>
                )}
                <a
                  href={url + "&download=true"}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Скачать {f.filename} · {Math.ceil(f.size / 1024)} КБ
                </a>
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}

export function LiveSolution({ question: q }: { question: any }) {
  return (
    <div className="live-media-text">
      <p>
        {q.kind === "essay"
          ? "Развёрнутый ответ проверяет учитель."
          : q.kind === "number"
            ? `${q.number} (допуск ${q.tolerance})`
            : q.kind === "short"
              ? q.accepted.join(" / ")
              : q.options
                  .filter((o: any) => q.correctIds.includes(o.id))
                  .map((o: any) => o.text)
                  .join("; ")}
      </p>
      <p>{q.explanation}</p>
    </div>
  );
}
