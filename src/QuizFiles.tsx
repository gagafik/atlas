import type { QuizFile } from "./quiz";
import "./QuizFiles.css";

export default function QuizFiles({
  files = [],
  workspace,
  quizId,
  attemptId,
  questionId,
  solution = false,
  onRemove,
  disabled = false,
}: {
  files?: QuizFile[];
  workspace: string;
  quizId?: string;
  attemptId?: string;
  questionId?: string;
  solution?: boolean;
  onRemove?: (id: string) => void;
  disabled?: boolean;
}) {
  if (!files.length) return null;
  return (
    <ul
      className="quiz-files"
      aria-label={solution ? "Файлы разбора" : "Файлы вопроса"}
    >
      {files.map((file) => {
        const params = new URLSearchParams({
          workspace,
          part: solution ? "solutionAttachments" : "attachments",
        });
        if (quizId) params.set("quizId", quizId);
        if (attemptId) params.set("attemptId", attemptId);
        if (questionId) params.set("questionId", questionId);
        const url = `/api/journal/quiz-files/${encodeURIComponent(file.id)}?${params}`;
        const ext = file.filename.split(".").pop()?.toLowerCase();
        return (
          <li key={file.id}>
            <a href={`${url}&download=true`}>{file.filename}</a>{" "}
            <small>{(file.size / 1024).toFixed(1)} КБ</small>
            {onRemove && (
              <button
                type="button"
                disabled={disabled}
                onClick={() => onRemove(file.id)}
                aria-label={`Убрать файл ${file.filename}`}
              >
                Убрать
              </button>
            )}
            {["png", "jpg", "jpeg"].includes(ext || "") && (
              <img src={url} alt={file.filename} loading="lazy" />
            )}
            {["mp3", "wav", "m4a", "ogg"].includes(ext || "") && (
              <audio
                src={url}
                controls
                preload="none"
                aria-label={file.filename}
              />
            )}
            {["mp4", "webm"].includes(ext || "") && (
              <video
                src={url}
                controls
                preload="none"
                aria-label={file.filename}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}
