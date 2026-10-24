export interface Named {
  id: string;
  name: string;
}
export interface SchoolClass extends Named {
  maxDaily: number;
  profileId: string;
  studentCount?: number | null;
}
export interface Room extends Named {
  capacity?: number | null;
  buildingId?: string | null;
}
export interface Campus {
  buildings: Named[];
  transfers: { fromId: string; toId: string; minutes: number }[];
  defaultTravelMinutes: number;
}
export interface Cycles {
  patterns: Named[];
  periods: (Named & {
    start: string;
    end: string;
    anchor: string;
    patternIds: string[];
  })[];
}
export interface Teacher extends Named {
  unavailable: string[];
}
export interface Subject extends Named {
  color: string;
}
export interface Student extends Named {
  classId: string;
  externalId?: string | null;
  active?: boolean;
}
export interface Group extends Named {
  classIds: string[];
  studentIds: string[];
  kind: "whole" | "core" | "math" | "elective" | "imported" | "staff";
  rosterStatus?: "unknown" | "partial" | "complete" | null;
  rosterSources?: string[];
  rosterNote?: string;
}
export interface Profile extends Named {
  periods: { label: string; start: string; end: string }[];
  dayPeriods?: Record<string, { label: string; start: string; end: string }[]>;
}
export interface Workload {
  id: string;
  groupId: string;
  teacherId: string | null;
  additionalTeacherIds?: string[];
  teacherRequired?: boolean;
  subjectId: string;
  hours: number;
  maxPerDay: number;
  roomIds: string[];
  requiredRoomIds?: string[];
  roomRequired?: boolean;
  studentCount?: number | null;
  profileId: string;
  block: string;
  allowedPeriods: number[];
  lessonLengths?: number[];
  maxLessonBreak?: number;
  cycleIds?: string[];
  cycleHours?: Record<string, number>;
  cycleLessonLengths?: Record<string, number[]>;
}
export interface TimetableRule extends Named {
  cycleIds?: string[];
  kind:
    "availability" | "daily_load" | "day_shape" | "distribution" | "relation";
  scope:
    "class" | "teacher" | "student" | "group" | "subject" | "room" | "workload";
  targetIds: string[];
  days: number[];
  enabled: boolean;
  mode: "hard" | "soft";
  weight: number;
  unavailable: { day: number; start: string; end: string }[];
  minLessons: number | null;
  maxLessons: number | null;
  includeEmptyDays: boolean;
  earliestStart: string | null;
  latestEnd: string | null;
  maxIdleMinutes: number | null;
  maxIdlePeriods?: number | null;
  maxIdleBlocks?: number | null;
  maxWeeklyIdlePeriods?: number | null;
  gapProfileId?: string | null;
  gapExcludedPeriods?: number[];
  relation?: "before" | "not_following" | "same_day" | "different_days";
  subjectIds?: string[];
  otherSubjectIds?: string[];
  relationGapMinutes?: number;
  maxConsecutive: number | null;
  shortBreakMinutes: number;
  lunchStart: string | null;
  lunchEnd: string | null;
  lunchMinutes: number | null;
  minDays: number | null;
  maxDays: number | null;
  minDayDistance: number;
}
export interface Placement {
  id: string;
  workloadId: string;
  day: number;
  period: number;
  roomId: string | null;
  locked: boolean;
  classIds: string[];
  sourceCells: string[];
  cycleId?: string;
}
export interface CalendarEvent extends Named {
  start: string;
  end: string;
  kind: "holiday" | "winter" | "reading" | "extra" | "exam";
  grades: string;
  effect?: "note" | "cancel";
  classIds?: string[];
}
export type ChangeType =
  "substitution" | "merge" | "move" | "self_study" | "cancel" | "extra";
export interface DatedLesson extends Named {
  kind: "change" | "cancel" | "add";
  onDate: string;
  sourceDate: string | null;
  placementId: string | null;
  groupId: string | null;
  profileId?: string | null;
  subjectId: string | null;
  startTime: string | null;
  endTime: string | null;
  teacherIds: string[] | null;
  roomIds: string[] | null;
  changeType?: ChangeType | null;
  mergeWith?: string | null;
}
export interface ScheduleDay extends Named {
  onDate: string;
  day: number;
  cycleId: string | null;
  classIds: string[];
}
export interface SubstitutionWeights {
  subject: number;
  group: number;
  presence: number;
  fairness: number;
  building: number;
}
export interface TeacherAbsence extends Named {
  teacherId: string;
  start: string;
  end: string;
  startTime: string;
  endTime: string;
}
export interface Commitment extends Named {
  kind: "booking" | "duty";
  onDate: string;
  startTime: string;
  endTime: string;
  roomIds: string[];
  teacherIds: string[];
  groupIds: string[];
  profileId: string | null;
  location: string;
  buildingId: string | null;
  studentCount: number | null;
  requiredTeachers: number;
  eligibleTeacherIds: string[];
  locked: boolean;
  note: string;
}
export interface Course extends Named {
  sections: { groupId: string; minSize: number; maxSize: number }[];
}
export interface CourseRequest {
  id: string;
  studentId: string;
  courseIds: string[];
  required: boolean;
  lockedGroupId: string | null;
}
export interface State {
  schemaVersion: 1;
  revision: number;
  cycles?: Cycles;
  settings: {
    name: string;
    yearStart: string;
    yearEnd: string;
    days: string[];
    shifts: 1;
    substitutionWeights?: SubstitutionWeights;
  };
  classes: SchoolClass[];
  teachers: Teacher[];
  rooms: Room[];
  campus?: Campus;
  subjects: Subject[];
  students: Student[];
  groups: Group[];
  profiles: Profile[];
  workloads: Workload[];
  placements: Placement[];
  events: CalendarEvent[];
  rules?: TimetableRule[];
  courses?: Course[];
  courseRequests?: CourseRequest[];
  datedLessons?: DatedLesson[];
  absences?: TeacherAbsence[];
  commitments?: Commitment[];
  scheduleDays?: ScheduleDay[];
  importInfo: {
    filename: string;
    sourceCells: number;
    importedLessons: number;
    notes: string[];
  };
}
export interface ScheduleIssue {
  date?: string;
  cycleId?: string;
  cycleName?: string;
  type: string;
  ids: string[];
  message: string;
  ruleId?: string;
  affectedStudentIds?: string[];
  amount?: number;
  penalty?: number;
  collision?: {
    day: number;
    start: string;
    end: string;
    studentIds: string[];
    classIds: string[];
    teacherIds: string[];
    roomIds: string[];
  };
}
export interface Report {
  datedWarnings?: ScheduleIssue[];
  datedPenalty?: number;
  issues: ScheduleIssue[];
  issueCount: number;
  unknownGroups: number;
  missingTeachers: number;
  missingRooms: number;
  unknownCapacity?: number;
  unknownBuildings?: number;
  placed: number;
  required: number;
  completeChecks: boolean;
  warnings?: ScheduleIssue[];
  warningCount?: number;
  qualityPenalty?: number;
}
export type Page =
  | "messages"
  | "assistant"
  | "journal"
  | "homework"
  | "quizzes"
  | "materials"
  | "curriculum"
  | "access"
  | "publications"
  | "commitments"
  | "dated"
  | "schedule"
  | "planner"
  | "rules"
  | "courses"
  | "exchange"
  | "workloads"
  | "groups"
  | "teachers"
  | "rooms"
  | "subjects"
  | "calendar"
  | "settings";
