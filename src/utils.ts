import type { State, Workload, Placement, Group, ScheduleIssue } from "./types";
export function rosterComplete(group: Group) {
  return (
    group.kind === "whole" ||
    group.kind === "staff" ||
    group.rosterStatus === "complete" ||
    (group.studentIds.length > 0 && !group.rosterStatus)
  );
}
export const uid = () => crypto.randomUUID();
export const teacherIds = (w: Workload) => [
  ...(w.teacherId ? [w.teacherId] : []),
  ...(w.additionalTeacherIds || []),
];
export const roomIds = (w: Workload, p: Placement | null) => [
  ...new Set([
    ...(p ? (p.roomId ? [p.roomId] : []) : w.roomIds),
    ...(w.requiredRoomIds || []),
  ]),
];
export const teacherNames = (s: State, w: Workload) =>
  teacherIds(w)
    .map((id) => named(s.teachers, id))
    .join(", ") ||
  (w.teacherRequired === false ? "Без учителя" : "Учитель не указан");
export const roomNames = (s: State, w: Workload, p: Placement | null) =>
  roomIds(w, p)
    .map((id) => named(s.rooms, id))
    .join(", ") ||
  (w.roomRequired === false ? "Без кабинета" : "Кабинет не указан");
export const named = (
  items: { id: string; name: string }[],
  id: string | null | undefined,
) => items.find((i) => i.id === id)?.name || "Не указан";
export const gradeOf = (name: string) => Number(name.match(/^\d+/)?.[0] || 0);
export function placementMatches(
  s: State,
  w: Workload,
  p: Placement | null,
  mode: string,
  id: string,
) {
  if (!id) return mode !== "student";
  if (mode === "student") {
    const student = s.students.find((st) => st.id === id);
    const group = s.groups.find((g) => g.id === w.groupId);
    if (!student || !group) return false;
    if (group.kind !== "whole") return group.studentIds.includes(id);
    return (
      group.classIds.includes(student.classId) &&
      (!p?.classIds.length || p.classIds.includes(student.classId))
    );
  }
  if (mode === "class")
    return (
      p?.classIds.length
        ? p.classIds
        : s.groups.find((g) => g.id === w.groupId)?.classIds || []
    ).includes(id);
  if (mode === "teacher") return teacherIds(w).includes(id);
  if (mode === "room") return roomIds(w, p).includes(id);
  return w.groupId === id;
}
export function lessonTime(s: State, p: Placement) {
  const workload = s.workloads.find((w) => w.id === p.workloadId)!;
  const profile = s.profiles.find(
    (profile) => profile.id === workload.profileId,
  )!;
  return (profile.dayPeriods?.[String(p.day)] || profile.periods)[p.period];
}
export function chronologicalPlacements(s: State, placements: Placement[]) {
  return [...placements].sort(
    (a, b) =>
      a.day - b.day ||
      lessonTime(s, a).start.localeCompare(lessonTime(s, b).start) ||
      lessonTime(s, a).end.localeCompare(lessonTime(s, b).end) ||
      a.id.localeCompare(b.id),
  );
}
export function issuesForPlacements(
  issues: ScheduleIssue[],
  placements: Placement[],
) {
  const ids = new Set(placements.flatMap((p) => [p.id, p.workloadId]));
  return issues.filter((issue) => issue.ids.some((id) => ids.has(id)));
}
export function issuesForStudent(
  issues: ScheduleIssue[],
  placements: Placement[],
  studentId: string,
) {
  return issues.filter((issue) => {
    if (issue.affectedStudentIds?.length)
      return issue.affectedStudentIds.includes(studentId);
    if (!issuesForPlacements([issue], placements).length) return false;
    const c = issue.collision;
    // A pupil overlap concerns the shared pupils, not every pupil taking
    // either course. A teacher/room collision affects both entire lessons.
    return (
      !c ||
      c.studentIds.includes(studentId) ||
      c.teacherIds.length > 0 ||
      c.roomIds.length > 0
    );
  });
}
export function csvCell(value: string) {
  return (
    '"' +
    (/^[=+@\-\t\r]/.test(value) ? "'" + value : value).replaceAll('"', '""') +
    '"'
  );
}
export function download(
  filename: string,
  value: string,
  type = "application/json",
) {
  const url = URL.createObjectURL(new Blob([value], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function weekdays(start: string, end: string) {
  let n = 0;
  const day = new Date(start + "T12:00:00");
  const last = new Date(end + "T12:00:00");
  while (day <= last) {
    if (day.getDay() > 0 && day.getDay() < 6) n++;
    day.setDate(day.getDate() + 1);
  }
  return n;
}
