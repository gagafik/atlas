// Shapes of the final-grades API shared by the panel and its tables, matrix and report cards.
export type Named = { id: string; name: string };
export type Scale = Named & {
  version: number;
  kind: "thresholds" | "labels";
  rounding: string;
  minCount: number;
  active: boolean;
  // Weight of a band in the pupil's average; a scale has points on all its bands or on none.
  bands: {
    label: string;
    minPercent: string | number;
    points?: string | number | null;
  }[];
};
export type Period = Named & {
  version: number;
  status: string;
  start: string;
  end: string;
};
export type Book = {
  id: string;
  period: Period;
  closureVersion: number;
  current: boolean;
};
export type Calculation = {
  rawPercent: string | null;
  roundedPercent: string | null;
  recommended: string | null;
  // Present for books prepared with category shares; older books carry only the three values above.
  method?: "average" | "points";
  rule?: string;
  categories?: {
    id: string;
    name: string;
    color: string;
    share: string;
    numeric: number;
    percent: string | null;
  }[];
  missing?: string[];
  excluded?: string[];
  blocked?: boolean;
  // A year recommendation derived from the published finals of the periods inside the year.
  basis?: "periods";
  points?: string;
  periods?: {
    id: string;
    name: string;
    label: string;
    points: string | null;
    current: boolean;
  }[];
};
export type Proposal = {
  scale: Scale;
  outcome: string;
  label: string;
  comment: string;
  explanation: string;
  calculation: Calculation;
  author: string;
  approval?: { actor: string; created: string };
  carriedFrom?: { bookId: string };
};
export type Published = {
  student: Named & { className: string };
  subjectId: string;
  subject: string;
  label: string;
  outcome: string;
  comment: string;
  period: string;
  scale: Scale;
  closureVersion: number;
  publishedAt: string;
  current: boolean;
};
export type Row = {
  id: string;
  bookId: string;
  studentId: string;
  subjectId: string;
  classId: string;
  assigned: string;
  version: number;
  status: string;
  proposal: Proposal | null;
  published: Published | null;
  source: {
    student: Named & { className: string };
    subject: string;
    expected: number;
    published: number;
    numeric: number;
    text: number;
    missing: number;
    zeroWeight: number;
    incompleteRoster: number;
    weight: string;
    weightedSum: string;
    policy?: { ruleName: string; method: string };
    // The course teachers of the closure, most lessons first.
    teachers?: { id: string; name: string; lessons: number }[];
  };
  // Only when the sheet was asked for a scale.
  advice?: Calculation;
};
export type Options = {
  admin: boolean;
  staff: boolean;
  classTeacher?: boolean;
  scales: Scale[];
  books: Book[];
  periods: Period[];
  students: Named[];
  teachers: Named[];
  classes: Named[];
  phrases: string[];
  yearRule: "periods" | "works";
  reportVersion: number;
};
export type Sheet = {
  book: Book;
  items: Row[];
  total: number;
  nextOffset: number | null;
  classes: Named[];
  subjects: Named[];
};
export type Evidence = {
  id: number;
  date: string;
  start: string;
  assessment: string;
  category?: string;
  score: number | null;
  label: string;
  maxScore: number;
  weight: number;
};
export type History = {
  id: number;
  action: string;
  actor: string;
  created: string;
  reason: string;
  row: Row;
};
export type Gpa = { value: string; counted: number; skipped: number };
export type Measure = {
  kind: string;
  kindTitle: string;
  category: string;
  day: string;
  letterNumber: string;
  levelTitle: string;
};
export type CardItem = Published & { preview?: boolean };
export type Card = {
  id: string;
  name: string;
  items: CardItem[];
  gpa: Gpa | null;
  measures: Measure[];
};
export type ClassReport = {
  school: string;
  period: Period;
  classId: string;
  className: string;
  subjects: Named[];
  pupils: Card[];
  preview: boolean;
};
