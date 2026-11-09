// Client types and labels of the safety reports: login journal, fire drill and safety briefings.

export interface LoginRow {
  seq: number;
  created: string;
  outcome: "success" | "failed";
  accountId: string | null;
  accountLogin: string | null;
  accountName: string | null;
  role: string | null;
  address: string;
  browser: string;
}
export interface LoginPage {
  items: LoginRow[];
  nextBefore: number | null;
  retentionDays: number;
}
export const BROWSER_NAMES: Record<string, string> = {
  yandex: "Яндекс Браузер",
  edge: "Edge",
  opera: "Opera",
  firefox: "Firefox",
  chrome: "Chrome",
  safari: "Safari",
  other: "Другой",
};

export interface DrillPupil {
  id: string;
  name: string;
}
export interface DrillPlace {
  kind: "lesson" | "between";
  roomIds: string[];
  rooms: string;
  teacherIds: string[];
  responsible: string;
  pupils: DrillPupil[];
}
export interface DrillClass {
  classId: string;
  className: string;
  classTeacher: string;
  expected: number;
  places: DrillPlace[];
  away: DrillPupil[];
}
export interface DrillStaff {
  teacherId: string;
  name: string;
  kind: "lesson" | "duty";
  place: string;
  classes: string;
}
export interface Drill {
  date: string;
  time: string;
  teachingDay: boolean;
  classes: DrillClass[];
  staff: DrillStaff[];
  totals: { expected: number; away: number; staff: number };
}

export interface SafetyOptions {
  today: string;
  now: string;
  yearStart: string;
  yearEnd: string;
  kinds: { id: string; label: string }[];
  classes: { id: string; name: string }[];
  teachers: { id: string; name: string }[];
}
export interface BriefingItem {
  id: string;
  version: number;
  kind: string;
  heldOn: string;
  topic: string;
  audience: "class" | "staff";
  classId: string | null;
  audienceLabel: string;
  conductor: string;
  present: number;
  absent: number;
}
export interface BriefingDetail extends BriefingItem {
  status: "active" | "withdrawn";
  note: string;
  conductorTeacherId: string | null;
  people: {
    id: string;
    kind: string;
    name: string;
    className: string;
    present: boolean;
  }[];
  history: { action: string; actor: string; created: string; reason: string }[];
}
export interface OverduePerson {
  id: string;
  name: string;
  classId: string;
  className: string;
  last: string | null;
}
export const HISTORY_NAMES: Record<string, string> = {
  created: "Записан",
  updated: "Изменён",
  withdrawn: "Отозван",
};

export const dayLabel = (iso: string) =>
  `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
export const stampLabel = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("ru-RU", {
        timeZone: "Asia/Qyzylorda",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
};
