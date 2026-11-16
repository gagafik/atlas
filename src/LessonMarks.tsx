import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./LessonMarks.css";

type Category = {
  id: string;
  kind: "praise" | "remark";
  title: string;
  quick: boolean;
  approval: boolean;
};
type Mark = {
  id: string;
  studentId: string;
  kind: "praise" | "remark";
  category: string;
  status: "pending" | "published";
  author: string;
};
type LessonInfo = {
  label: string;
  canMark: boolean;
  categories: Category[];
  notes: Mark[];
};
export type LessonMarks = {
  info: LessonInfo | null;
  busy: string;
  error: { studentId: string; text: string } | null;
  mark: (studentId: string, category: Category, text: string) => void;
};

// Praise and remarks for the pupils of one journal lesson; the server checks who teaches it.
export function useLessonMarks(
  workspace: string,
  lessonKey?: string,
  day?: string,
): LessonMarks {
  const [info, setInfo] = useState<LessonInfo | null>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState<LessonMarks["error"]>(null),
    [generation, setGeneration] = useState(0);
  const requests = useRef<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    setInfo(null);
    setError(null);
    if (!lessonKey || !day) return;
    api(
      `behaviour/lesson?lessonKey=${encodeURIComponent(lessonKey)}&day=${encodeURIComponent(day)}`,
      workspace,
    )
      .then((value) => {
        if (alive) setInfo(value);
      })
      // A cancelled lesson or a teacher without rights simply has no marks bar.
      .catch(() => {
        if (alive) setInfo(null);
      });
    return () => {
      alive = false;
    };
  }, [workspace, lessonKey, day, generation]);
  function mark(studentId: string, category: Category, text: string) {
    if (!lessonKey || !day || busy) return;
    const key = JSON.stringify([lessonKey, studentId, category.id, text]);
    requests.current[key] ||= crypto.randomUUID();
    setBusy(studentId);
    setError(null);
    api("behaviour/notes", workspace, "POST", {
      requestId: requests.current[key],
      studentIds: [studentId],
      categoryId: category.id,
      day,
      text,
      lessonKey,
    })
      .then(() => {
        delete requests.current[key];
        setGeneration((n) => n + 1);
      })
      .catch((e: any) => setError({ studentId, text: e.message }))
      .finally(() => setBusy(""));
  }
  return { info, busy, error, mark };
}

// The marks already given to one pupil for this lesson.
export function LessonMarkChips({
  marks,
  studentId,
}: {
  marks: LessonMarks;
  studentId: string;
}) {
  const { info } = marks;
  if (!info) return null;
  const own = info.notes.filter((n) => n.studentId === studentId);
  return (
    <>
      {own.map((n) => (
        <span key={n.id} className={`lesson-mark lesson-mark-${n.kind}`}>
          {n.kind === "praise" ? "＋ " : "− "}
          {n.category}
          {n.status === "pending" ? " · на согласовании" : ""}
        </span>
      ))}
    </>
  );
}

// The refusal of the server to the last mark given to this pupil.
export function LessonMarkError({
  marks,
  studentId,
}: {
  marks: LessonMarks;
  studentId: string;
}) {
  if (marks.error?.studentId !== studentId) return null;
  return (
    <span role="alert" className="lesson-mark-error">
      {marks.error.text}
    </span>
  );
}

// What a teacher may give: the quick praise and remarks, any other category with a prompt for the family's note, and the
// error of the last attempt. Nothing shows to a user who may not mark.
export function LessonMarkChoices({
  marks,
  studentId,
  disabled,
}: {
  marks: LessonMarks;
  studentId: string;
  disabled: boolean;
}) {
  const { info } = marks;
  if (!info?.canMark) return null;
  const quick = info.categories.filter((c) => c.quick);
  return (
    <>
      {quick.map((c) => (
        <button
          key={c.id}
          type="button"
          className={`lesson-mark-button lesson-mark-${c.kind}`}
          disabled={disabled}
          onClick={() => marks.mark(studentId, c, "")}
        >
          {c.kind === "praise" ? "＋ " : "− "}
          {c.title}
        </button>
      ))}
      <select
        aria-label="Другая похвала или замечание"
        value=""
        disabled={disabled}
        onChange={(e) => {
          const category = info.categories.find((c) => c.id === e.target.value);
          if (!category) return;
          const text = window.prompt(
            `${category.title}: комментарий для семьи (можно оставить пустым)`,
            "",
          );
          if (text !== null) marks.mark(studentId, category, text);
        }}
      >
        <option value="">Другая отметка…</option>
        {info.categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.kind === "praise" ? "Похвала" : "Замечание"}: {c.title}
            {c.approval ? " · согласование" : ""}
          </option>
        ))}
      </select>
      <LessonMarkError marks={marks} studentId={studentId} />
    </>
  );
}
