import type { GradingEvidence } from "./HomeworkGradeForm";

export interface QuizFile {
  id: string;
  filename: string;
  size: number;
  created: string;
}
export interface Question {
  kind: "single" | "multiple" | "short" | "number" | "essay";
  text: string;
  points: number;
  options: { id: string; text: string }[];
  correctIds: string[];
  accepted: string[];
  caseSensitive: boolean;
  number: number | null;
  tolerance: number;
  explanation: string;
  fileIds?: string[];
  solutionFileIds?: string[];
  attachments?: QuizFile[];
  solutionAttachments?: QuizFile[];
}
export interface BankItem {
  id: string;
  version: number;
  subject: string;
  tags: string[];
  question: Question;
  archived: boolean;
}
export interface QuestionSnapshot {
  id: string;
  version: number;
  question: Question;
}
export interface QuizDefinition {
  title: string;
  instructions: string;
  variants: { id: string; name: string; questions: QuestionSnapshot[] }[];
  opensAt: string;
  closesAt: string;
  timeLimitMinutes: number;
  maxAttempts: number;
  shuffleQuestions: boolean;
  shuffleOptions: boolean;
}
export interface QuizDocument {
  lesson: {
    date: string;
    subject: string;
    group: string;
    students: { id: string; name: string; enrolled: boolean }[];
  };
  definition: QuizDefinition;
  studentIds: string[];
  variantsByStudent: Record<string, string>;
}
export interface Attempt {
  id: string;
  quizId: string;
  studentId: string;
  number: number;
  version: number;
  status: "active" | "submitted" | "reviewed";
  startedAt: string;
  deadline: string;
  submittedAt: string | null;
  questions?: (Pick<
    Question,
    "kind" | "text" | "points" | "options" | "attachments"
  > & {
    id: string;
  })[];
  answers?: Record<string, { selected: string[]; text: string }>;
  variant?: { id: string; name: string };
  maxScore?: number;
  score?: number | null;
  marks?: Record<
    string,
    { score: number; comment: string; automatic: boolean }
  >;
  solutions?: QuestionSnapshot[];
  comment?: string;
  correctionReason?: string;
  resultsVisible?: boolean;
  finishReason?: string;
  serverNow?: string;
  canAnswer?: boolean;
  canReview?: boolean;
  expired?: boolean;
  grading?: GradingEvidence & { sourceScore: number; stale: boolean };
}
export interface Quiz {
  id: string;
  version: number;
  status: string;
  released: boolean;
  staff: boolean;
  canManage: boolean;
  lessonKey: string;
  lessonDate: string;
  subject: string;
  group: string;
  title: string;
  instructions: string;
  opensAt: string;
  closesAt: string;
  timeLimitMinutes: number;
  maxAttempts: number;
  serverNow: string;
  document?: QuizDocument;
  revision?: number;
  studentId?: string;
  canStart?: boolean;
  attempts?: Attempt[];
  students?: {
    id: string;
    name: string;
    variant: string;
    enrolled: boolean;
    attempts: Attempt[];
  }[];
}
export interface Activity {
  dirty: boolean;
  busy: boolean;
}
export const questionKinds = {
  single: "Один правильный ответ",
  multiple: "Несколько правильных ответов",
  short: "Короткий ответ",
  number: "Число",
  essay: "Развёрнутый ответ",
};
export const quizStatuses: Record<string, string> = {
  draft: "Черновик",
  published: "Опубликован",
  closed: "Приём завершён",
  withdrawn: "Отозван",
  active: "Выполняется",
  submitted: "Нужна проверка",
  reviewed: "Проверено",
};
export const schoolDate = (value: string) =>
  new Date(value).toLocaleString("ru-RU", { timeZone: "Asia/Qyzylorda" });
export const blankQuestion = (): Question => ({
  kind: "single",
  text: "",
  points: 1,
  options: [
    { id: crypto.randomUUID(), text: "" },
    { id: crypto.randomUUID(), text: "" },
  ],
  correctIds: [],
  accepted: [],
  caseSensitive: false,
  number: null,
  tolerance: 0,
  explanation: "",
});
