import { useEffect, useMemo, useRef, useState, Fragment } from "react";
import type { FormEvent, ReactNode } from "react";
import StudentSchedule from "./StudentSchedule";
import ConflictList from "./ConflictList";
import Planner from "./Planner";
import GenerationPanel from "./GenerationPanel";
import BellsEditor from "./BellsEditor";
import RulesPanel from "./RulesPanel";
import CampusEditor from "./CampusEditor";
import CyclesEditor from "./CyclesEditor";
import CoursesPanel from "./CoursesPanel";
import ExchangePanel from "./ExchangePanel";
import DatedSchedule from "./DatedSchedule";
import CommitmentsPanel from "./CommitmentsPanel";
import PublicationsPanel from "./PublicationsPanel";
import AccessPanel from "./AccessPanel";
import JournalPanel from "./JournalPanel";
import HomeworkQueuePanel from "./HomeworkQueuePanel";
import QuizzesPanel from "./QuizzesLoader";
import TeachingMaterialsPanel from "./TeachingMaterialsLoader";
import CurriculumPanel from "./CurriculumLoader";
import { canLeaveHomework } from "./homeworkNavigation";
import MessagesPanel from "./MessagesPanel";
import AssistantPanel from "./AssistantPanel";
import { roleNames, type AuthStatus } from "./auth";
import { patterns, weekState, activeIn, cycleHours, patternOn } from "./cycles";
import ChangePreview from "./ChangePreview";
import { api } from "./api";
import { withEffectiveFrom } from "./timetableVersions";
import { startPolling } from "./polling";
import {
  CalendarDays,
  BookOpen,
  Users,
  GraduationCap,
  DoorOpen,
  Settings,
  Sparkles,
  Plus,
  Search,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  Check,
  AlertTriangle,
  ArrowUpRight,
  Download,
  Upload,
  Undo2,
  Redo2,
  X,
  LockKeyhole,
  Clock3,
  Grid2X2,
  ListChecks,
  FlaskConical,
  Trash2,
  Pencil,
  LoaderCircle,
  Printer,
  PanelLeftClose,
  CircleHelp,
  School,
  Layers3,
  ArrowRight,
} from "lucide-react";
import type {
  State,
  Report,
  Page,
  Workload,
  Placement,
  Group,
  CalendarEvent,
  Named,
} from "./types";
import {
  named,
  teacherIds,
  teacherNames,
  roomIds,
  roomNames,
  gradeOf,
  uid,
  placementMatches,
  download,
  csvCell,
  weekdays,
  rosterComplete,
  chronologicalPlacements,
  issuesForStudent,
  lessonTime,
} from "./utils";

const NAV: { id: Page; label: string; icon: typeof CalendarDays }[] = [
  { id: "schedule", label: "Расписание", icon: CalendarDays },
  { id: "planner", label: "Рабочее место", icon: Grid2X2 },
  { id: "rules", label: "Ограничения", icon: ListChecks },
  { id: "assistant", label: "AI-помощник", icon: Sparkles },
  { id: "courses", label: "Выбор курсов", icon: GraduationCap },
  { id: "workloads", label: "Учебная нагрузка", icon: BookOpen },
  { id: "groups", label: "Классы и группы", icon: Users },
  { id: "teachers", label: "Учителя", icon: GraduationCap },
  { id: "rooms", label: "Кабинеты", icon: DoorOpen },
  { id: "subjects", label: "Предметы", icon: Layers3 },
  { id: "calendar", label: "Учебный календарь", icon: CalendarDays },
  { id: "dated", label: "Замены и даты", icon: Clock3 },
  { id: "journal", label: "Учебный журнал", icon: BookOpen },
  { id: "materials", label: "Учебные материалы", icon: BookOpen },
  { id: "curriculum", label: "Учебные планы", icon: BookOpen },
  { id: "homework", label: "Домашние задания", icon: ListChecks },
  { id: "quizzes", label: "Контрольные и тесты", icon: ListChecks },
  { id: "messages", label: "Сообщения", icon: BookOpen },
  { id: "commitments", label: "Бронирования и дежурства", icon: DoorOpen },
  { id: "exchange", label: "Обмен и печать", icon: Printer },
  { id: "publications", label: "Публикация", icon: ArrowUpRight },
  { id: "access", label: "Пользователи и доступ", icon: LockKeyhole },
  { id: "settings", label: "Настройки", icon: Settings },
];
const KINDS: Record<string, string> = {
  whole: "Весь класс",
  core: "Core",
  math: "Математика",
  elective: "Elective",
  imported: "Группа из файла",
  staff: "Без класса · собрание / мероприятие",
};
const EVENT_NAMES: Record<string, string> = {
  holiday: "Праздник",
  winter: "Winter Break",
  reading: "Reading Week",
  extra: "Каникулы 1 классов",
  exam: "Экзамены",
};
type Modal = {
  type: string;
  item?: any;
  day?: number;
  period?: number;
  returnToCheck?: { studentId?: string };
};
const EMPTY_REPORT: Report = {
  issues: [],
  issueCount: 0,
  unknownGroups: 0,
  missingTeachers: 0,
  missingRooms: 0,
  placed: 0,
  required: 0,
  completeChecks: false,
};

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Empty({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <CalendarDays size={26} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export default function App({
  session,
  onAuthChange,
  sessionControls,
}: {
  session: AuthStatus;
  onAuthChange: () => Promise<void>;
  sessionControls: ReactNode;
}) {
  // Only an administrator changes a class teacher; the server refuses everyone else, undo and redo included.
  const canAssignClassTeacher =
    !session.enabled || session.user?.role === "admin";
  const [activeCycle, setActiveCycle] = useState("weekly");
  const [cycleDate, setCycleDate] = useState("");
  const [workspace, setWorkspaceValue] = useState(
    session.user?.workspaces[0] || "school",
  );
  const [state, setState] = useState<State | null>(null);
  const [report, setReport] = useState<Report>(EMPTY_REPORT);
  const [page, setPageValue] = useState<Page>("schedule");
  function setPage(value: Page) {
    if (canLeaveHomework()) setPageValue(value);
  }
  function setWorkspace(value: string) {
    if (canLeaveHomework()) setWorkspaceValue(value);
  }
  const [commitmentId, setCommitmentId] = useState<string | undefined>();
  const [datedDate, setDatedDate] = useState<string | undefined>();
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [toast, setToast] = useState("");
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState<Modal | null>(null);
  const [placementLoad, setPlacementLoad] = useState("");
  const [placementDay, setPlacementDay] = useState(0);
  const [mode, setMode] = useState("class"),
    [selected, setSelected] = useState("");
  const [studentQuery, setStudentQuery] = useState("");
  const [studentClassFilter, setStudentClassFilter] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [showArchivedStudents, setShowArchivedStudents] = useState(false);
  const [grade, setGrade] = useState<number | null>(null),
    [tab, setTab] = useState("classes");
  const [tablePage, setTablePage] = useState(0),
    [sidebar, setSidebar] = useState(false);
  const [groupClasses, setGroupClasses] = useState<string[]>([]),
    [groupStudents, setGroupStudents] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const requestLock = useRef(false);
  const presenceId = useRef(uid());
  const [collaboration, setCollaboration] = useState<{
    revision: number | null;
    peers: { clientId: string; name: string; page: string }[];
  }>({ revision: null, peers: [] });
  const [syncError, setSyncError] = useState("");
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  useEffect(() => {
    let live = true;
    const update = () => {
      return api("collaboration/presence", workspace, "POST", {
        clientId: presenceId.current,
        page,
      })
        .then((r) => {
          if (live) {
            setCollaboration(r);
            setSyncError("");
          }
          return true;
        })
        .catch((e) => {
          if (live) setSyncError(e.message);
          return false;
        });
    };
    const polling = startPolling(update, { interval: 25000 });
    return () => {
      live = false;
      polling.stop();
    };
  }, [workspace, page, state?.revision]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setModal(null);
    setSelected("");
    Promise.all([api("state", workspace), api("check", workspace)])
      .then(([s, r]) => {
        if (alive) {
          setState(s);
          setReport(r);
          setSelected(
            s.classes.find((c: any) => c.name === "1A")?.id ||
              s.classes[0]?.id ||
              "",
          );
          setMode("class");
          setGrade(null);
          setStudentQuery("");
          setStudentClassFilter("");
          setShowArchivedStudents(false);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [workspace]);
  useEffect(() => {
    if (!state || loading) return;
    if (!patterns(state).some((p) => p.id === activeCycle)) {
      setActiveCycle(patterns(state)[0].id);
      return;
    }
    let alive = true;
    api(`check?cycleId=${encodeURIComponent(activeCycle)}`, workspace)
      .then((r) => {
        if (alive) setReport(r);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [state?.revision, activeCycle, workspace, loading]);
  useEffect(() => {
    setSearch("");
    setTablePage(0);
    setSidebar(false);
  }, [page]);
  useEffect(() => {
    setTablePage(0);
  }, [search, tab]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 4500);
      return () => clearTimeout(t);
    }
  }, [toast]);
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement as HTMLElement;
    const listener = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) setModal(null);
      if (e.key === "Tab") {
        const nodes = Array.from(
          document.querySelectorAll<HTMLElement>(
            ".modal button:not(:disabled),.modal input:not(:disabled),.modal select:not(:disabled),.modal textarea:not(:disabled),.modal summary",
          ),
        ).filter((node) => node.getClientRects().length > 0);
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", listener);
    const t = setTimeout(
      () =>
        document
          .querySelector<HTMLElement>(
            ".modal input, .modal select, .modal textarea, .modal button",
          )
          ?.focus(),
      0,
    );
    return () => {
      document.removeEventListener("keydown", listener);
      clearTimeout(t);
      previous?.focus();
    };
  }, [modal, busy]);

  const maps = useMemo(
    () =>
      state
        ? Object.fromEntries(
            [
              "workloads",
              "groups",
              "teachers",
              "subjects",
              "rooms",
              "profiles",
            ].map((key) => [
              key,
              Object.fromEntries(
                (state[key as keyof State] as any[]).map((x) => [x.id, x]),
              ),
            ]),
          )
        : {},
    [state],
  );
  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    state?.placements
      .filter((p) => (p.cycleId || "weekly") === activeCycle)
      .forEach((p) => (m[p.workloadId] = (m[p.workloadId] || 0) + 1));
    return m;
  }, [state, activeCycle]);
  const filteredPlacements = useMemo(
    () =>
      state?.placements.filter(
        (p) =>
          (p.cycleId || "weekly") === activeCycle &&
          placementMatches(
            state,
            maps.workloads[p.workloadId],
            p,
            mode,
            selected,
          ),
      ) || [],
    [state, maps, mode, selected, activeCycle],
  );
  const pending = useMemo(
    () =>
      state?.workloads
        .filter(
          (w) =>
            activeIn(w, activeCycle) &&
            (counts[w.id] || 0) < cycleHours(w, activeCycle) &&
            placementMatches(state, w, null, mode, selected),
        )
        .map((w) => ({ ...w, hours: cycleHours(w, activeCycle) })) || [],
    [state, counts, mode, selected, activeCycle],
  );
  const studentOptions = useMemo(
    () =>
      state?.students
        .filter(
          (st) =>
            (showArchivedStudents || st.active !== false) &&
            (!studentClassFilter || st.classId === studentClassFilter) &&
            `${st.name} ${named(state.classes, st.classId)}`
              .toLocaleLowerCase("ru")
              .includes(studentQuery.trim().toLocaleLowerCase("ru")),
        )
        .sort(
          (a, b) =>
            a.name.localeCompare(b.name, "ru") ||
            a.classId.localeCompare(b.classId),
        ) || [],
    [state, studentQuery, studentClassFilter, showArchivedStudents],
  );
  useEffect(() => {
    if (modal?.type === "placement") {
      setPlacementLoad(modal.item?.workloadId || pending[0]?.id || "");
      setPlacementDay(modal.item?.day ?? modal.day ?? 0);
    }
  }, [modal]);
  useEffect(() => {
    if (
      !loading &&
      page === "schedule" &&
      mode === "student" &&
      !studentOptions.some((st) => st.id === selected)
    ) {
      setSelected(studentOptions[0]?.id || "");
    }
  }, [loading, page, mode, studentOptions, selected]);

  async function commit(next: State, message = "Изменения сохранены") {
    if (requestLock.current) return false;
    requestLock.current = true;
    setBusy(true);
    setError("");
    try {
      const saved = await api(
        withEffectiveFrom("state", effectiveFrom),
        workspace,
        "PUT",
        next,
      );
      setState(saved);
      const id = patterns(saved).some((p) => p.id === activeCycle)
        ? activeCycle
        : patterns(saved)[0].id;
      setActiveCycle(id);
      // The save is already committed: a failed check must not invite a retry
      // that would store new records twice.
      try {
        setReport(
          await api(`check?cycleId=${encodeURIComponent(id)}`, workspace),
        );
      } catch (e: any) {
        setError(`Изменения сохранены, но проверка не выполнена: ${e.message}`);
      }
      setToast(message);
      return true;
    } catch (e: any) {
      setError(e.message);
      return false;
    } finally {
      requestLock.current = false;
      setBusy(false);
    }
  }
  async function receiveState(saved: State) {
    setState(saved);
    const id = patterns(saved).some((p) => p.id === activeCycle)
      ? activeCycle
      : patterns(saved)[0].id;
    setActiveCycle(id);
    setReport(await api(`check?cycleId=${encodeURIComponent(id)}`, workspace));
  }
  async function previewEdit(body: Record<string, unknown>) {
    if (requestLock.current) return;
    requestLock.current = true;
    setBusy(true);
    setError("");
    try {
      const preview = await api("editor/preview", workspace, "POST", {
        revision: state!.revision,
        ...body,
      });
      setModal({ type: "preview", item: preview });
    } catch (e: any) {
      setError(e.message);
    } finally {
      requestLock.current = false;
      setBusy(false);
    }
  }
  async function previewEvent(value?: CalendarEvent, removeId?: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api("calendar/preview", workspace, "POST", {
        revision: s.revision,
        table: "events",
        value: value || null,
        removeId: removeId || null,
      });
      setModal({ type: "preview", item: result });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function applyPreview(preview: { token: string; revision: number }) {
    if (requestLock.current) return;
    requestLock.current = true;
    setBusy(true);
    setError("");
    try {
      await receiveState(
        await api(
          withEffectiveFrom("editor/apply", effectiveFrom),
          workspace,
          "POST",
          {
            revision: state!.revision,
            token: preview.token,
          },
        ),
      );
      setModal(null);
      setToast("Изменения применены. Доступна отмена.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      requestLock.current = false;
      setBusy(false);
    }
  }
  function open(type: string, item?: any, day?: number, period?: number) {
    setError("");
    if (type === "placement") {
      if (item?.cycleId) setActiveCycle(item.cycleId);
      setPlacementLoad(item?.workloadId || pending[0]?.id || "");
      setPlacementDay(item?.day ?? day ?? 0);
    }
    setModal({ type, item, day, period });
    setGroupClasses(item?.classIds || []);
    setGroupStudents(item?.studentIds || []);
  }
  function toggle(value: string, values: string[], set: (v: string[]) => void) {
    set(
      values.includes(value)
        ? values.filter((x) => x !== value)
        : [...values, value],
    );
  }

  if (loading || !state)
    return (
      <div className="loading-screen">
        <div className="brand-symbol">
          <Grid2X2 />
        </div>
        <h2>Атлас</h2>
        {error ? (
          <>
            <p role="alert">{error}</p>
            <button onClick={() => location.reload()}>Повторить</button>
          </>
        ) : (
          <>
            <LoaderCircle className="spin" />
            <p>Открываем расписание школы…</p>
          </>
        )}
      </div>
    );
  const s = state;
  const classTeacherId =
    mode === "class"
      ? s.classes.find((c) => c.id === selected)?.classTeacherId
      : null;
  const currentWeek = weekState(s, activeCycle);
  const title = NAV.find((n) => n.id === page)?.label;
  const selectedItems: Named[] =
    mode === "student"
      ? studentOptions.map((st) => ({
          id: st.id,
          name: `${st.name} · ${named(s.classes, st.classId)}${st.active === false ? " · выбыл" : ""}`,
        }))
      : mode === "class"
        ? s.classes
        : mode === "teacher"
          ? s.teachers
          : mode === "room"
            ? s.rooms
            : s.groups;
  const selectedClass = s.classes.find((c) => c.id === selected);
  const selectedStudent = s.students.find((st) => st.id === selected);
  const checkStudent = s.students.find(
    (st) => st.id === modal?.item?.studentId,
  );
  const checkIssues = checkStudent
    ? issuesForStudent(
        report.issues,
        currentWeek.placements.filter((p) =>
          placementMatches(
            s,
            maps.workloads[p.workloadId],
            p,
            "student",
            checkStudent.id,
          ),
        ),
        checkStudent.id,
      )
    : report.issues;
  const checkWarnings = checkStudent
    ? issuesForStudent(
        report.warnings || [],
        currentWeek.placements.filter((p) =>
          placementMatches(
            s,
            maps.workloads[p.workloadId],
            p,
            "student",
            checkStudent.id,
          ),
        ),
        checkStudent.id,
      )
    : report.warnings || [];
  const profile =
    maps.profiles[selectedClass?.profileId || "secondary"] || s.profiles[0];
  const totalPending = currentWeek.workloads.reduce(
    (n, w) => n + Math.max(0, w.hours - (counts[w.id] || 0)),
    0,
  );
  const item = modal?.item;
  const yearLabel = `${s.settings.yearStart.slice(0, 4)} / ${s.settings.yearEnd.slice(0, 4)}`;
  const firstMonth = new Date(s.settings.yearStart + "T12:00:00");
  const lastMonth = new Date(s.settings.yearEnd + "T12:00:00");
  const monthCount =
    (lastMonth.getFullYear() - firstMonth.getFullYear()) * 12 +
    lastMonth.getMonth() -
    firstMonth.getMonth() +
    1;

  function openStudent(id: string) {
    setStudentQuery("");
    setStudentClassFilter("");
    setShowArchivedStudents(
      s.students.find((st) => st.id === id)?.active === false,
    );
    setSelected(id);
    setMode("student");
    setPage("schedule");
    setModal(null);
  }
  function openFromCheck(type: string, value: Placement | Group) {
    const context = checkStudent ? { studentId: checkStudent.id } : {};
    open(type, value);
    setModal((current) => current && { ...current, returnToCheck: context });
  }

  async function saveForm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!modal) return;
    const form = new FormData(e.currentTarget);
    const get = (key: string) => String(form.get(key) || "").trim();
    const number = (key: string) => Number(get(key));
    const next = structuredClone(s);
    const id = item?.id || uid();
    let key = "";
    let value: any;
    if (modal.type === "teacher") {
      key = "teachers";
      value = {
        id,
        name: get("name"),
        unavailable: get("unavailable")
          .split(/[\s,]+/)
          .filter(Boolean),
      };
      if (
        value.unavailable.some((x: string) => !/^([0-6]):([0-9]|10)$/.test(x))
      ) {
        setError("Недоступность задаётся как день:урок, например 0:1, 0:2.");
        return;
      }
    }
    if (modal.type === "room") {
      key = "rooms";
      value = {
        id,
        name: get("name"),
        capacity: get("capacity") ? number("capacity") : null,
        buildingId: get("buildingId") || null,
      };
    }
    if (modal.type === "subject") {
      key = "subjects";
      value = { id, name: get("name"), color: get("color") };
    }
    if (modal.type === "class") {
      key = "classes";
      value = {
        id,
        name: get("name"),
        maxDaily: number("maxDaily"),
        studentCount: get("studentCount") ? number("studentCount") : null,
        profileId: get("profileId"),
        // A disabled field is not in FormData: keep the stored value instead of clearing it.
        classTeacherId: canAssignClassTeacher
          ? get("classTeacherId") || null
          : (item?.classTeacherId ?? null),
      };
      if (!item)
        next.groups.push({
          id: uid(),
          name: value.name,
          classIds: [id],
          studentIds: [],
          kind: "whole",
        });
      else {
        const wholeGroups = next.groups.filter(
          (g) =>
            g.kind === "whole" &&
            g.classIds.length === 1 &&
            g.classIds[0] === id,
        );
        wholeGroups.forEach((g) => {
          g.name = value.name;
        });
        next.workloads
          .filter((w) => wholeGroups.some((g) => g.id === w.groupId))
          .forEach((w) => {
            w.profileId = value.profileId;
          });
      }
    }
    if (modal.type === "group") {
      key = "groups";
      value = {
        ...modal.item,
        id,
        name: get("name"),
        kind: get("kind"),
        rosterStatus: get("rosterComplete") === "on" ? "complete" : "partial",
        classIds: get("kind") === "staff" ? [] : groupClasses,
        studentIds:
          get("kind") === "staff"
            ? []
            : groupStudents.filter((id) =>
                s.students.some(
                  (st) => st.id === id && groupClasses.includes(st.classId),
                ),
              ),
      };
      if (!groupClasses.length && get("kind") !== "staff") {
        setError("Выберите хотя бы один класс.");
        return;
      }
    }
    if (modal.type === "workload") {
      key = "workloads";
      value = {
        id,
        groupId: get("groupId"),
        subjectId: get("subjectId"),
        teacherId:
          get("withoutTeacher") === "on" ? null : get("teacherId") || null,
        additionalTeacherIds:
          get("withoutTeacher") === "on"
            ? []
            : form.getAll("additionalTeacherIds").map(String),
        teacherRequired: get("withoutTeacher") !== "on",
        studentCount: get("studentCount") ? number("studentCount") : null,
        hours: number("hours"),
        maxPerDay: number("maxPerDay"),
        profileId: get("profileId"),
        roomIds:
          get("withoutRoom") === "on" ? [] : form.getAll("roomIds").map(String),
        requiredRoomIds:
          get("withoutRoom") === "on"
            ? []
            : form.getAll("requiredRoomIds").map(String),
        roomRequired: get("withoutRoom") !== "on",
        block: get("block"),
        allowedPeriods: get("allowedPeriods")
          .split(",")
          .map((x) => Number(x.trim()))
          .filter(Number.isFinite),
        lessonLengths: get("lessonLengths")
          .split(/[+,\s]+/)
          .filter(Boolean)
          .map(Number),
        maxLessonBreak: number("maxLessonBreak"),
        cycleIds: form.getAll("cycleIds").map(String),
        cycleHours: Object.fromEntries(
          patterns(s)
            .filter((p) => get(`cycleHours:${p.id}`))
            .map((p) => [p.id, number(`cycleHours:${p.id}`)]),
        ),
        cycleLessonLengths: Object.fromEntries(
          patterns(s)
            .filter(
              (p) =>
                get(`cycleHours:${p.id}`) ||
                get(`cycleLengths:${p.id}`) ||
                form.has(`overrideLengths:${p.id}`),
            )
            .map((p) => [
              p.id,
              get(`cycleLengths:${p.id}`)
                .split(/[+,\s]+/)
                .filter(Boolean)
                .map(Number),
            ]),
        ),
      };
      if (!get("allowedPeriods")) value.allowedPeriods = [];
      if (!value.roomRequired) {
        if (
          next.placements.some(
            (p) => p.workloadId === id && p.roomId && p.locked,
          )
        ) {
          setError(
            "Сначала снимите закрепление занятий: отмена кабинета изменит их размещение.",
          );
          return;
        }
        next.placements
          .filter((p) => p.workloadId === id)
          .forEach((p) => {
            p.roomId = null;
          });
      }
    }
    if (modal.type === "placement") {
      key = "placements";
      const w = s.workloads.find((w) => w.id === get("workloadId"))!;
      if (!w) {
        setError("Выберите нагрузку.");
        return;
      }
      value = {
        id,
        workloadId: w.id,
        cycleId: get("cycleId") || activeCycle,
        day: number("day"),
        period: number("period"),
        roomId: get("roomId") || null,
        locked: form.has("locked"),
        classIds: item?.workloadId === w.id ? item.classIds : [],
        sourceCells: item?.sourceCells || [],
      };
      if (
        item?.locked &&
        (value.cycleId !== (item.cycleId || "weekly") ||
          value.day !== item.day ||
          value.period !== item.period ||
          value.roomId !== item.roomId) &&
        value.locked
      ) {
        setError("Снимите закрепление, прежде чем переносить занятие.");
        return;
      }
    }
    if (modal.type === "event") {
      key = "events";
      value = {
        id,
        name: get("name"),
        start: get("start"),
        end: get("end"),
        kind: get("kind"),
        grades: get("grades") || "Все классы",
        effect: get("effect") || "note",
        classIds: form.getAll("eventClassIds").map(String),
      };
    }
    if (modal.type === "students") {
      const lines = get("students")
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean);
      let added = 0;
      for (const line of lines) {
        const parts = line.split(";").map((x) => x.trim());
        const c = s.classes.find(
          (c) => c.name.toLocaleLowerCase() === parts[1]?.toLocaleLowerCase(),
        );
        if (parts.length !== 2 || !c || !parts[0]) {
          setError(`Не распознана строка «${line}». Формат: имя;класс.`);
          return;
        }
        if (
          !next.students.some(
            (st) => st.name === parts[0] && st.classId === c.id,
          )
        ) {
          next.students.push({ id: uid(), name: parts[0], classId: c.id });
          added++;
        }
      }
      if (!added) {
        setError("Новых учеников нет. Проверьте строки и повторения.");
        return;
      }
    }
    if (modal.type === "settings")
      next.settings = {
        ...s.settings,
        name: get("name"),
        yearStart: get("yearStart"),
        yearEnd: get("yearEnd"),
      };
    if (key) {
      const items = (next as any)[key] as any[];
      const index = items.findIndex((x) => x.id === id);
      if (index >= 0) items[index] = value;
      else items.push(value);
    }
    if (modal.type === "placement") {
      await previewEdit({ action: "placement", placement: value });
      return;
    }
    if (modal.type === "event") {
      await previewEvent(value);
      return;
    }
    if (await commit(next)) setModal(null);
  }

  async function remove(type: string, id: string) {
    if (type === "event") {
      await previewEvent(undefined, id);
      return;
    }
    const next = structuredClone(s);
    const key: Record<string, string> = {
      teacher: "teachers",
      room: "rooms",
      subject: "subjects",
      class: "classes",
      group: "groups",
      workload: "workloads",
      placement: "placements",
      event: "events",
    };
    if (type === "workload" && s.placements.some((p) => p.workloadId === id)) {
      setError("Сначала снимите с сетки занятия этой нагрузки.");
      return;
    }
    if (type === "teacher") {
      const led = s.classes.filter((c) => c.classTeacherId === id);
      if (led.length) {
        setError(
          `Учитель — классный руководитель: ${led.map((c) => c.name).join(", ")}. Сначала назначьте другого классного руководителя.`,
        );
        return;
      }
    }
    if (type === "class") {
      next.groups = next.groups.filter(
        (g) =>
          !(
            g.kind === "whole" &&
            g.classIds.length === 1 &&
            g.classIds[0] === id &&
            !next.workloads.some((w) => w.groupId === g.id)
          ),
      );
    }
    (next as any)[key[type]] = (next as any)[key[type]].filter(
      (x: any) => x.id !== id,
    );
    if (await commit(next, "Запись удалена")) setModal(null);
  }

  async function drop(event: React.DragEvent, day: number, period: number) {
    event.preventDefault();
    const id = event.dataTransfer.getData("text/plain");
    if (!id || busy) return;
    if (id.startsWith("new:")) {
      open("placement", undefined, day, period);
      setModal({
        type: "placement",
        item: { workloadId: id.slice(4) },
        day,
        period,
      });
      return;
    }
    const p = s.placements.find((p) => p.id === id);
    if (!p) return;
    if (p.locked) {
      setToast("Занятие закреплено. Снимите закрепление в карточке.");
      return;
    }
    await previewEdit({ action: "move", ids: [id], day, period, linked: true });
  }

  function exportCsv() {
    const headers = [
      "Неделя",
      "День",
      "Урок",
      "Время",
      "Класс / группа",
      "Предмет",
      "Учитель",
      "Кабинет",
    ];
    const rows = chronologicalPlacements(s, filteredPlacements).map((p) => {
      const w = maps.workloads[p.workloadId];
      const t = lessonTime(s, p);
      return [
        named(patterns(s), p.cycleId || "weekly"),
        s.settings.days[p.day],
        String(p.period),
        `${t.start}–${t.end}`,
        maps.groups[w.groupId].name,
        maps.subjects[w.subjectId].name,
        teacherNames(s, w),
        roomNames(s, w, p),
      ];
    });
    download(
      mode === "student" && selectedStudent
        ? `Расписание — ${selectedStudent.name}.csv`
        : "Расписание.csv",
      "\uFEFF" +
        [headers, ...rows].map((r) => r.map(csvCell).join(";")).join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }

  function options(items: Named[], empty?: string) {
    return (
      <>
        {empty && <option value="">{empty}</option>}
        {items.map((x) => (
          <option key={x.id} value={x.id}>
            {x.name}
            {"kind" in x && (x as Group).kind !== "whole"
              ? ` · ${[...new Set((x as Group).classIds.map((id) => gradeOf(named(s.classes, id))))].sort((a, b) => a - b).join(", ")} кл.`
              : ""}
          </option>
        ))}
      </>
    );
  }
  function rowActions(type: string, row: any) {
    return (
      <button
        className="icon-button"
        title="Редактировать"
        aria-label={`Редактировать ${row.name || "нагрузку"}`}
        onClick={() => open(type, row)}
      >
        <Pencil size={15} />
      </button>
    );
  }
  function loadLabel(w: Workload) {
    return `${maps.groups[w.groupId]?.name} · ${maps.subjects[w.subjectId]?.name}`;
  }
  function dataTable(
    rows: any[],
    columns: { label: string; render: (r: any) => ReactNode }[],
    type: string,
  ) {
    const filtered = rows.filter(
      (r) =>
        JSON.stringify(r).toLowerCase().includes(search.toLowerCase()) ||
        (r.groupId &&
          loadLabel(r).toLowerCase().includes(search.toLowerCase())) ||
        (r.groupId &&
          teacherNames(s, r).toLowerCase().includes(search.toLowerCase())),
    );
    const safePage = Math.min(
      tablePage,
      Math.max(0, Math.ceil(filtered.length / 40) - 1),
    );
    const visible = filtered.slice(safePage * 40, safePage * 40 + 40);
    return (
      <div className="table-card">
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.label}>{c.label}</th>
                ))}
                <th>
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.id}>
                  {columns.map((c) => (
                    <td key={c.label}>{c.render(row)}</td>
                  ))}
                  <td>{rowActions(type, row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!visible.length && (
          <Empty
            title="Здесь пока пусто"
            text={
              search
                ? "По этому запросу ничего не найдено."
                : "Добавьте первую запись, чтобы начать работу."
            }
          />
        )}
        <div className="table-footer">
          <span>{filtered.length} записей</span>
          <div>
            <button
              className="icon-button"
              aria-label="Предыдущая страница"
              disabled={!safePage}
              onClick={() => setTablePage(safePage - 1)}
            >
              <ChevronLeft size={16} />
            </button>
            <span>
              {safePage + 1} / {Math.max(1, Math.ceil(filtered.length / 40))}
            </span>
            <button
              className="icon-button"
              aria-label="Следующая страница"
              disabled={(safePage + 1) * 40 >= filtered.length}
              onClick={() => setTablePage(safePage + 1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  function lessonCard(p: Placement) {
    const w = maps.workloads[p.workloadId] as Workload;
    const subject = maps.subjects[w.subjectId];
    const g = maps.groups[w.groupId] as Group;
    const times = lessonTime(s, p);
    return (
      <button
        key={p.id}
        className={`lesson ${p.locked ? "pinned" : ""}`}
        style={{ backgroundColor: subject.color }}
        draggable={!p.locked && !busy}
        onDragStart={(e) => e.dataTransfer.setData("text/plain", p.id)}
        onClick={() => open("placement", p)}
        title={`${subject.name} · ${g.name}\n${teacherNames(s, w)}\n${times.start}–${times.end}`}
      >
        <div className="lesson-top">
          <strong>{subject.name}</strong>
          {p.locked && <LockKeyhole size={11} />}
        </div>
        {g.kind !== "whole" && <span className="lesson-group">{g.name}</span>}
        <span className="lesson-teacher">{teacherNames(s, w)}</span>
        <div className="lesson-bottom">
          <span>
            <DoorOpen size={10} />
            {roomNames(s, w, p)}
          </span>
          {mode === "class" ? (
            <span>{times.start}</span>
          ) : (
            <span>{g.kind === "whole" ? g.name : times.start}</span>
          )}
        </div>
      </button>
    );
  }

  const entityButtons =
    page === "groups" ? (
      <>
        <button onClick={() => open("students")}>
          <Upload size={16} />
          Списки учеников
        </button>
        <button
          className="primary"
          onClick={() => open(tab === "classes" ? "class" : "group")}
        >
          <Plus size={17} />
          {tab === "classes" ? "Добавить класс" : "Добавить группу"}
        </button>
      </>
    ) : (
      <button
        className="primary"
        onClick={() =>
          open(
            page === "workloads"
              ? "workload"
              : page === "teachers"
                ? "teacher"
                : page === "rooms"
                  ? "room"
                  : page === "subjects"
                    ? "subject"
                    : "event",
          )
        }
      >
        <Plus size={17} />
        Добавить{" "}
        {page === "workloads"
          ? "нагрузку"
          : page === "teachers"
            ? "учителя"
            : page === "rooms"
              ? "кабинет"
              : page === "subjects"
                ? "предмет"
                : "период"}
      </button>
    );

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebar ? "open" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("schedule");
          }}
        >
          <div className="brand-symbol">
            <Grid2X2 size={23} />
          </div>
          <div>
            <strong>
              атлас<span>•</span>
            </strong>
            <small>школьное расписание</small>
          </div>
        </a>
        <button className="school-switch" onClick={() => setPage("settings")}>
          <div className="school-avatar">
            <School size={19} />
          </div>
          <span>
            <strong>{s.settings.name}</strong>
            <small>{yearLabel} учебный год</small>
          </span>
          <ChevronDown size={14} />
        </button>
        <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav>
          {NAV.filter(
            (n) =>
              ![
                "access",
                "journal",
                "homework",
                "quizzes",
                "materials",
                "curriculum",
              ].includes(n.id) ||
              !session.enabled ||
              session.user?.role === "admin",
          ).map(({ id, label, icon: Icon }, i) => (
            <Fragment key={id}>
              {i === 6 && <div className="nav-divider" />}
              <button
                className={page === id ? "active" : ""}
                onClick={() => setPage(id)}
              >
                <Icon size={18} />
                <span>{label}</span>
                {id === "workloads" && totalPending > 0 && (
                  <span className="nav-count">{totalPending}</span>
                )}
              </button>
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button
            className="demo-button"
            disabled={
              busy || (!!session.user && session.user.workspaces.length < 2)
            }
            onClick={() =>
              setWorkspace(workspace === "school" ? "demo" : "school")
            }
          >
            <FlaskConical size={17} />
            <span>
              {workspace === "school"
                ? "Попробовать на примере"
                : "Вернуться в свою школу"}
            </span>
            <ArrowUpRight size={15} />
          </button>
          <div className="local-status">
            <span className="status-dot" />
            Данные на этом компьютере
          </div>
          <div className="profile">
            <div className="user-avatar">А</div>
            <div>
              <strong>{session.user?.name || "Локальный пользователь"}</strong>
              <small>
                {session.user
                  ? roleNames[session.user.role]
                  : "Вход не включён"}
              </small>
            </div>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button menu-toggle"
              aria-label="Меню"
              onClick={() => setSidebar(!sidebar)}
            >
              <PanelLeftClose size={19} />
            </button>
            <span>Школа</span>
            <ChevronRight size={13} />
            <strong>{title}</strong>
          </div>
          <div className="topbar-right">
            {sessionControls}
            {workspace === "demo" && (
              <Badge tone="amber">Демонстрационные данные</Badge>
            )}
            <span className="saved">
              {busy ? (
                <LoaderCircle className="spin" size={13} />
              ) : (
                <Check size={13} />
              )}{" "}
              {busy ? "Сохраняем…" : "Сохранено"}
            </span>
            <button
              className="icon-button"
              title="О приложении"
              onClick={() => open("help")}
            >
              <CircleHelp size={18} />
            </button>
          </div>
        </header>
        {!!collaboration.peers.length && (
          <p className="collaboration-note">
            Сейчас работают:{" "}
            {collaboration.peers
              .map(
                (p) =>
                  `${p.name} (${NAV.find((n) => n.id === p.page)?.label || "расписание"})`,
              )
              .join(", ")}
          </p>
        )}
        {syncError && (
          <p className="collaboration-note" role="status">
            Не удалось проверить общую версию: {syncError}
          </p>
        )}
        {collaboration.revision !== null &&
          collaboration.revision > s.revision && (
            <div className="collaboration-note changed" role="status">
              <strong>
                Другой пользователь сохранил версию {collaboration.revision}.
              </strong>
              <span>
                У вас открыта версия {s.revision}. При загрузке открытые
                несохранённые формы будут закрыты.
              </span>
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const fresh = await api("state", workspace);
                    setModal(null);
                    await receiveState(fresh);
                    setRefreshEpoch((n) => n + 1);
                    setToast("Загружена общая версия.");
                  } catch (e: any) {
                    setError(e.message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Загрузить новую версию
              </button>
            </div>
          )}
        <main key={refreshEpoch}>
          <div className="page-heading">
            <div>
              <div className="eyebrow">ПЛАНИРОВАНИЕ УЧЕБНОГО ГОДА</div>
              <h1>{title}</h1>
              <p>
                {page === "assistant"
                  ? "Вопросы к модели и проверяемые предложения для расписания."
                  : page === "schedule"
                    ? "Вся школьная неделя. Каждый урок на своём месте."
                    : page === "planner"
                      ? "Сводная сетка, безопасные правки и сравнение вариантов."
                      : page === "rules"
                        ? "Обязательные условия и пожелания для школьной недели."
                        : page === "courses"
                          ? "Предпочтения учеников, секции курсов и проверенное распределение."
                          : page === "exchange"
                            ? "Файлы для обмена и документы для всей школы."
                            : page === "workloads"
                              ? "Предметы, преподаватели и часы для каждого класса и группы."
                              : page === "groups"
                                ? "Общие классы и смешанные группы для core, математики и electives."
                                : page === "calendar"
                                  ? `${new Date(s.settings.yearStart + "T12:00").toLocaleDateString("ru")} — ${new Date(s.settings.yearEnd + "T12:00").toLocaleDateString("ru")} · одна учебная смена`
                                  : page === "dated"
                                    ? "Фактические занятия, отсутствия и замены на конкретную дату."
                                    : page === "commitments"
                                      ? "События, занятость помещений и распределение дежурных."
                                      : page === "publications"
                                        ? "Проверенные снимки расписания для получателей."
                                        : page === "messages"
                                          ? "Личные диалоги и объявления школы."
                                          : page === "quizzes"
                                            ? "Банк вопросов, назначение тестов и проверка результатов"
                                            : page === "homework"
                                              ? "Задания по срокам, отправленные ответы и проверка работ."
                                              : page === "curriculum"
                                                ? "Темы, плановые часы и прогресс по опубликованным урокам."
                                                : page === "materials"
                                                  ? "Библиотека заготовок, общие материалы и версии для уроков."
                                                  : page === "journal"
                                                    ? "Посещаемость, оценки, темы и домашние задания по датам."
                                                    : page === "access"
                                                      ? "Учётные записи, разрешённые расписания и журнал действий."
                                                      : page === "settings"
                                                        ? "Параметры школы, звонки и резервные копии."
                                                        : "Справочник вашей школы. Изменения доступны в редакторе расписания."}
              </p>
            </div>
            <div className="heading-actions">
              {page === "schedule" || page === "planner" || page === "rules" ? (
                <>
                  <button
                    onClick={() => {
                      open("check");
                      api(
                        `check?cycleId=${encodeURIComponent(activeCycle)}`,
                        workspace,
                      )
                        .then(setReport)
                        .catch((e) => setError(e.message));
                    }}
                  >
                    <ListChecks size={17} />
                    Проверить
                  </button>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => open("generate")}
                  >
                    <Sparkles size={17} />
                    Составить расписание
                  </button>
                </>
              ) : [
                  "courses",
                  "exchange",
                  "dated",
                  "commitments",
                  "publications",
                  "access",
                  "assistant",
                  "journal",
                  "homework",
                  "quizzes",
                  "materials",
                  "curriculum",
                  "messages",
                ].includes(page) ? null : page !== "settings" ? (
                entityButtons
              ) : (
                <button className="primary" onClick={() => open("settings")}>
                  <Pencil size={16} />
                  Изменить параметры
                </button>
              )}
            </div>
          </div>
          {![
            "dated",
            "commitments",
            "publications",
            "access",
            "assistant",
            "journal",
            "homework",
            "quizzes",
            "materials",
            "curriculum",
            "messages",
          ].includes(page) && (
            <div className="cycle-switcher">
              <label className="field">
                <span>Неделя расписания</span>
                <select
                  value={activeCycle}
                  onChange={(e) => {
                    setActiveCycle(e.target.value);
                    setCycleDate("");
                    setModal(null);
                  }}
                >
                  {patterns(s).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Выбрать неделю по дате</span>
                <input
                  type="date"
                  value={cycleDate}
                  onChange={(e) => {
                    setCycleDate(e.target.value);
                    const id = patternOn(s, e.target.value);
                    if (id) setActiveCycle(id);
                  }}
                />
              </label>
              {cycleDate && !patternOn(s, cycleDate) && (
                <span className="muted">Дата вне учебных периодов</span>
              )}
            </div>
          )}
          {page === "planner" && (
            <Planner
              key={`${workspace}:${activeCycle}`}
              state={currentWeek}
              workspace={workspace}
              onState={receiveState}
              onOpen={(p) => open("placement", p)}
            />
          )}
          {page === "rules" && (
            <RulesPanel
              state={s}
              report={report}
              workspace={workspace}
              onState={receiveState}
            />
          )}
          {page === "courses" && (
            <CoursesPanel
              key={workspace}
              state={s}
              workspace={workspace}
              onState={receiveState}
              onGroup={(id) =>
                open(
                  "group",
                  s.groups.find((g) => g.id === id),
                )
              }
            />
          )}
          {page === "exchange" && (
            <ExchangePanel
              key={workspace}
              state={s}
              workspace={workspace}
              defaultCycle={activeCycle}
              onState={receiveState}
            />
          )}
          {page === "dated" && (
            <DatedSchedule
              key={workspace}
              state={s}
              workspace={workspace}
              onState={receiveState}
              initialDate={datedDate}
              onCommitment={(id) => {
                setCommitmentId(id);
                setPage("commitments");
              }}
            />
          )}
          {page === "commitments" && (
            <CommitmentsPanel
              key={workspace}
              state={s}
              workspace={workspace}
              onState={receiveState}
              initialId={commitmentId}
              onDate={(day) => {
                setDatedDate(day);
                setPage("dated");
              }}
            />
          )}
          {page === "publications" && (
            <PublicationsPanel
              key={workspace}
              state={s}
              workspace={workspace}
              defaultCycle={activeCycle}
            />
          )}
          {page === "materials" && (
            <TeachingMaterialsPanel key={workspace} workspace={workspace} />
          )}
          {page === "curriculum" && (
            <CurriculumPanel key={workspace} workspace={workspace} />
          )}
          {page === "journal" && (
            <JournalPanel key={workspace} workspace={workspace} />
          )}
          {page === "homework" && (
            <HomeworkQueuePanel key={workspace} workspace={workspace} />
          )}
          {page === "quizzes" && (
            <QuizzesPanel key={workspace} workspace={workspace} />
          )}
          {page === "messages" && (
            <MessagesPanel key={workspace} workspace={workspace} />
          )}
          {page === "assistant" && (
            <AssistantPanel
              key={workspace}
              state={s}
              workspace={workspace}
              onApply={applyPreview}
            />
          )}
          {page === "access" && (
            <AccessPanel
              key={workspace}
              session={session}
              state={s}
              workspace={workspace}
              onAuthChange={onAuthChange}
            />
          )}
          {error && !modal && (
            <div className="error-banner" role="alert">
              <AlertTriangle size={18} />
              <span>{error}</span>
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="Закрыть ошибку"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {page === "schedule" && (
            <>
              <div className="stats-row">
                <div className="stat">
                  <span>Классы</span>
                  <strong>{s.classes.length}</strong>
                  <Users size={20} />
                  <small>в учебном плане</small>
                </div>
                <div className="stat">
                  <span>Учителя</span>
                  <strong>{s.teachers.length}</strong>
                  <GraduationCap size={20} />
                  <small>в справочнике</small>
                </div>
                <div className="stat">
                  <span>Занятия в сетке</span>
                  <strong>
                    {currentWeek.placements.length.toLocaleString("ru")}
                  </strong>
                  <CalendarDays size={20} />
                  <small>
                    из{" "}
                    {currentWeek.workloads
                      .reduce((n, w) => n + w.hours, 0)
                      .toLocaleString("ru")}{" "}
                    по нагрузке
                  </small>
                </div>
                <button
                  className="stat stat-check"
                  onClick={() => open("check")}
                >
                  <span>Проверка данных</span>
                  <strong
                    className={
                      report.issueCount || !report.completeChecks
                        ? "amber-text"
                        : "green-text"
                    }
                  >
                    {report.issueCount || (!report.completeChecks ? "—" : "0")}
                    <ArrowUpRight size={20} />
                  </strong>
                  <small>
                    {report.issueCount
                      ? "пересечений для проверки"
                      : report.completeChecks
                        ? "пересечений не найдено"
                        : "есть неполные данные"}
                  </small>
                </button>
              </div>
              {s.importInfo.filename && (
                <div className="import-banner">
                  <div className="import-icon">
                    <Check size={18} />
                  </div>
                  <div>
                    <strong>Ваше расписание уже здесь</strong>
                    <span>
                      {s.importInfo.filename} · сохранены все{" "}
                      {s.importInfo.sourceCells.toLocaleString("ru")}{" "}
                      заполненные ячейки.{" "}
                      {report.unknownGroups
                        ? `Требуют уточнения составы ${report.unknownGroups} групп.`
                        : "Составы учебных групп заполнены."}
                    </span>
                  </div>
                  <button
                    className="text-button"
                    onClick={() => open("importInfo")}
                  >
                    Об импорте <ArrowRight size={15} />
                  </button>
                </div>
              )}
              <div className="schedule-layout">
                <section className="schedule-card">
                  <div className="schedule-toolbar">
                    <div className="segmented">
                      {[
                        ["class", "Класс"],
                        ["teacher", "Учитель"],
                        ["room", "Кабинет"],
                        ["group", "Группа"],
                        ["student", "Ученик"],
                      ].map(([value, label]) => (
                        <button
                          key={value}
                          className={mode === value ? "selected" : ""}
                          onClick={() => {
                            setMode(value);
                            setSelected(
                              value === "class"
                                ? s.classes[0]?.id || ""
                                : value === "teacher"
                                  ? s.teachers[0]?.id || ""
                                  : value === "room"
                                    ? s.rooms[0]?.id || ""
                                    : value === "student"
                                      ? studentOptions[0]?.id || ""
                                      : s.groups[0]?.id || "",
                            );
                          }}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <div className="toolbar-buttons">
                      <label
                        className="effective-from"
                        title="Пусто — изменение относится ко всему текущему периоду расписания. Дата — прошлые дни до неё сохраняют прежнее расписание и составы."
                      >
                        <span>Действует с</span>
                        <input
                          type="date"
                          value={effectiveFrom}
                          max={new Date().toLocaleDateString("sv-SE")}
                          disabled={busy}
                          onChange={(e) => setEffectiveFrom(e.target.value)}
                        />
                      </label>
                      <button
                        className="icon-button"
                        title="Отменить последнее изменение"
                        disabled={busy || s.revision === 0}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            setState(
                              await api("undo", workspace, "POST", {
                                revision: s.revision,
                              }),
                            );
                            setReport(
                              await api(
                                `check?cycleId=${encodeURIComponent(activeCycle)}`,
                                workspace,
                              ),
                            );
                            setToast("Восстановлена предыдущая версия");
                          } catch (e: any) {
                            setError(e.message);
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        <Undo2 size={17} />
                      </button>
                      <button
                        className="icon-button"
                        title="Повторить отменённое изменение"
                        disabled={busy}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            await receiveState(
                              await api("redo", workspace, "POST", {
                                revision: s.revision,
                              }),
                            );
                            setToast("Отменённое изменение повторено");
                          } catch (e: any) {
                            setError(e.message);
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        <Redo2 size={17} />
                      </button>
                      <button
                        className="icon-button"
                        title="Скачать выбранное расписание в CSV"
                        disabled={mode === "student" && !selectedStudent}
                        onClick={exportCsv}
                      >
                        <Download size={17} />
                      </button>
                      <button
                        className="icon-button"
                        title="Печать расписания"
                        disabled={mode === "student" && !selectedStudent}
                        onClick={() => window.print()}
                      >
                        <Printer size={17} />
                      </button>
                    </div>
                  </div>
                  {mode === "student" && (
                    <div className="student-filters">
                      <label>
                        <span>Поиск ученика</span>
                        <input
                          type="search"
                          value={studentQuery}
                          placeholder="Имя, фамилия или класс"
                          onChange={(e) => setStudentQuery(e.target.value)}
                        />
                      </label>
                      <label>
                        <span>Класс ученика</span>
                        <select
                          value={studentClassFilter}
                          onChange={(e) =>
                            setStudentClassFilter(e.target.value)
                          }
                        >
                          {options(s.classes, "Все классы")}
                        </select>
                      </label>
                      <label className="student-archive-toggle">
                        <input
                          type="checkbox"
                          checked={showArchivedStudents}
                          onChange={(e) =>
                            setShowArchivedStudents(e.target.checked)
                          }
                        />
                        Показать выбывших
                      </label>
                    </div>
                  )}
                  <div className="schedule-filters">
                    <div className="selection">
                      <span>
                        {mode === "class"
                          ? "Класс"
                          : mode === "teacher"
                            ? "Учитель"
                            : mode === "room"
                              ? "Кабинет"
                              : mode === "student"
                                ? "Ученик"
                                : "Учебная группа"}
                      </span>
                      <select
                        aria-label="Выбрать расписание"
                        value={selected}
                        onChange={(e) => setSelected(e.target.value)}
                      >
                        {mode === "student" && !selectedItems.length && (
                          <option value="">Ученики не найдены</option>
                        )}
                        {selectedItems
                          .filter(
                            (i) =>
                              mode !== "class" ||
                              grade === null ||
                              gradeOf(i.name) === grade,
                          )
                          .map((i) => (
                            <option key={i.id} value={i.id}>
                              {i.name}
                            </option>
                          ))}
                      </select>
                    </div>
                    <div className="schedule-subtitle">
                      <span className="status-dot" /> Недельное расписание{" "}
                      <span>·</span> {filteredPlacements.length} занятий
                      {mode === "class" && classTeacherId && (
                        <>
                          {" "}
                          <span>·</span> Классный руководитель:{" "}
                          {named(s.teachers, classTeacherId)}
                        </>
                      )}
                    </div>
                  </div>
                  {mode === "class" && (
                    <div className="grade-tabs">
                      <button
                        className={grade === null ? "chosen" : ""}
                        onClick={() => setGrade(null)}
                      >
                        Все классы
                      </button>
                      {Array.from(
                        new Set(s.classes.map((c) => gradeOf(c.name))),
                      )
                        .sort((a, b) => a - b)
                        .map((g) => (
                          <button
                            key={g}
                            className={grade === g ? "chosen" : ""}
                            onClick={() => {
                              setGrade(g);
                              setSelected(
                                s.classes.find((c) => gradeOf(c.name) === g)
                                  ?.id || "",
                              );
                            }}
                          >
                            {g === 0 ? "Подготовка" : g}
                          </button>
                        ))}
                    </div>
                  )}
                  {mode === "student" ? (
                    <StudentSchedule
                      state={currentWeek}
                      studentId={selected}
                      placements={filteredPlacements}
                      report={report}
                      onLesson={(p) => open("placement", p)}
                      onCheck={() => open("check", { studentId: selected })}
                      onGroup={(g) => open("group", g)}
                    />
                  ) : (
                    <div className="timetable-scroll">
                      <table className="timetable">
                        <thead>
                          <tr>
                            <th className="period-heading">
                              <Clock3 size={15} />
                            </th>
                            {s.settings.days.map((d, i) => (
                              <th key={d}>
                                <span>{String(i + 1).padStart(2, "0")}</span>
                                {d}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {profile?.periods.map((slot: any, p: number) => (
                            <tr key={p}>
                              <th className="period-label">
                                <strong>{slot.label}</strong>
                                {mode === "class" && (
                                  <small>
                                    {Object.keys(profile.dayPeriods || {})
                                      .length ? (
                                      "По дням"
                                    ) : (
                                      <>
                                        {slot.start}
                                        <br />
                                        {slot.end}
                                      </>
                                    )}
                                  </small>
                                )}
                              </th>
                              {s.settings.days.map((d, day) => {
                                const lessons = filteredPlacements.filter(
                                  (x) => x.day === day && x.period === p,
                                );
                                return (
                                  <td
                                    key={d}
                                    onDragOver={(e) => e.preventDefault()}
                                    onDrop={(e) => drop(e, day, p)}
                                  >
                                    <div className="cell-stack">
                                      {lessons.map(lessonCard)}
                                      <button
                                        className={`cell-add ${lessons.length ? "has-lessons" : ""}`}
                                        aria-label={`Добавить урок: ${d}, ${p}`}
                                        onClick={() =>
                                          open("placement", undefined, day, p)
                                        }
                                      >
                                        <Plus size={15} />
                                        {!lessons.length && (
                                          <span>Добавить</span>
                                        )}
                                      </button>
                                    </div>
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <div className="schedule-foot">
                    <span>
                      <span className="status-dot" />{" "}
                      {mode === "class"
                        ? profile?.name
                        : "Время указано по звонкам каждой группы"}
                    </span>
                    <span>
                      {mode === "student"
                        ? "Общие уроки класса и личные группы"
                        : "Перетащите урок, чтобы изменить время"}
                    </span>
                  </div>
                </section>
                <aside className="schedule-aside">
                  <div className="pending-panel">
                    <div className="panel-heading">
                      <h3>Нераспределённые</h3>
                      <Badge>
                        {pending.reduce(
                          (n, w) => n + w.hours - (counts[w.id] || 0),
                          0,
                        )}
                      </Badge>
                    </div>
                    <p>Нагрузка выбранного расписания</p>
                    {pending.length ? (
                      pending.map((w) => (
                        <button
                          className="pending-item"
                          key={w.id}
                          draggable={mode !== "student"}
                          onDragStart={(e) =>
                            e.dataTransfer.setData("text/plain", "new:" + w.id)
                          }
                          onClick={() =>
                            setModal({
                              type: "placement",
                              item: { workloadId: w.id },
                            })
                          }
                        >
                          <span
                            className="subject-dot"
                            style={{
                              background: maps.subjects[w.subjectId].color,
                            }}
                          />
                          <span>
                            <strong>{maps.subjects[w.subjectId].name}</strong>
                            <small>{maps.groups[w.groupId].name}</small>
                          </span>
                          <b>{w.hours - (counts[w.id] || 0)}</b>
                        </button>
                      ))
                    ) : (
                      <div className="pending-empty">
                        <div>
                          <Check size={21} />
                        </div>
                        <strong>
                          {filteredPlacements.length
                            ? "Все уроки на месте"
                            : "Нет заданной нагрузки"}
                        </strong>
                        <span>
                          {filteredPlacements.length
                            ? "Для выбранного расписания всё распределено."
                            : "Добавьте предметы и часы, чтобы начать."}
                        </span>
                      </div>
                    )}
                    <button
                      className="full-width"
                      onClick={() => {
                        setPage("workloads");
                        open("workload");
                      }}
                    >
                      <Plus size={15} />
                      Добавить нагрузку
                    </button>
                  </div>
                  <div className="note-panel">
                    <div className="note-icon">
                      <Layers3 size={18} />
                    </div>
                    <h3>Смешанные группы</h3>
                    <p>
                      Core, математика и electives могут объединять учеников из
                      разных классов.
                    </p>
                    <button
                      className="text-button"
                      onClick={() => {
                        setPage("groups");
                        setTab("groups");
                      }}
                    >
                      Открыть группы <ArrowRight size={15} />
                    </button>
                  </div>
                  <div className="micro-note">
                    <LockKeyhole size={14} />
                    <span>
                      Закреплённые уроки сохраняют своё место при генерации.
                    </span>
                  </div>
                </aside>
              </div>
            </>
          )}
          {![
            "schedule",
            "planner",
            "rules",
            "courses",
            "exchange",
            "dated",
            "commitments",
            "publications",
            "access",
            "assistant",
            "journal",
            "homework",
            "quizzes",
            "calendar",
            "materials",
            "curriculum",
            "messages",
            "settings",
          ].includes(page) && (
            <>
              <div className="list-toolbar">
                {page === "groups" ? (
                  <div className="segmented">
                    <button
                      className={tab === "classes" ? "selected" : ""}
                      onClick={() => setTab("classes")}
                    >
                      Классы <span>{s.classes.length}</span>
                    </button>
                    <button
                      className={tab === "groups" ? "selected" : ""}
                      onClick={() => setTab("groups")}
                    >
                      Группы{" "}
                      <span>
                        {s.groups.filter((g) => g.kind !== "whole").length}
                      </span>
                    </button>
                    <button
                      className={tab === "students" ? "selected" : ""}
                      onClick={() => setTab("students")}
                    >
                      Ученики <span>{s.students.length}</span>
                    </button>
                  </div>
                ) : (
                  <span className="muted">
                    {page === "workloads"
                      ? `${s.workloads.length} назначений · ${report.required} часов в неделю`
                      : ""}
                  </span>
                )}
                <label className="search-field">
                  <Search size={17} />
                  <input
                    placeholder="Поиск…"
                    aria-label="Поиск в справочнике"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
              </div>
              {page === "teachers" &&
                dataTable(
                  s.teachers,
                  [
                    {
                      label: "Учитель",
                      render: (r) => (
                        <div className="name-cell">
                          <span className="initials">{r.name.slice(0, 1)}</span>
                          <strong>{r.name}</strong>
                        </div>
                      ),
                    },
                    {
                      label: "Часов в неделю",
                      render: (r) =>
                        currentWeek.workloads
                          .filter((w) => teacherIds(w).includes(r.id))
                          .reduce((n, w) => n + w.hours, 0),
                    },
                    {
                      label: "Доступность",
                      render: (r) =>
                        r.unavailable.length ? (
                          <Badge tone="amber">
                            {r.unavailable.length} ограничений
                          </Badge>
                        ) : (
                          <Badge>Без ограничений</Badge>
                        ),
                    },
                  ],
                  "teacher",
                )}
              {page === "rooms" && (
                <CampusEditor
                  state={s}
                  workspace={workspace}
                  onState={receiveState}
                />
              )}
              {page === "rooms" &&
                dataTable(
                  s.rooms,
                  [
                    {
                      label: "Кабинет",
                      render: (r) => <strong>{r.name}</strong>,
                    },
                    {
                      label: "Вместимость",
                      render: (r) => r.capacity ?? "Не указана",
                    },
                    {
                      label: "Здание",
                      render: (r) =>
                        named(s.campus?.buildings || [], r.buildingId),
                    },
                    {
                      label: "Занятий в сетке",
                      render: (r) =>
                        currentWeek.placements.filter((p) =>
                          roomIds(maps.workloads[p.workloadId], p).includes(
                            r.id,
                          ),
                        ).length,
                    },
                  ],
                  "room",
                )}
              {page === "subjects" &&
                dataTable(
                  s.subjects,
                  [
                    {
                      label: "Предмет",
                      render: (r) => (
                        <div className="name-cell">
                          <span
                            className="subject-swatch"
                            style={{ background: r.color }}
                          />
                          <strong>{r.name}</strong>
                        </div>
                      ),
                    },
                    {
                      label: "Назначений",
                      render: (r) =>
                        currentWeek.workloads.filter(
                          (w) => w.subjectId === r.id,
                        ).length,
                    },
                    {
                      label: "Часов в неделю",
                      render: (r) =>
                        currentWeek.workloads
                          .filter((w) => w.subjectId === r.id)
                          .reduce((n, w) => n + w.hours, 0),
                    },
                  ],
                  "subject",
                )}
              {page === "workloads" &&
                dataTable(
                  s.workloads,
                  [
                    {
                      label: "Класс / группа",
                      render: (r) => (
                        <>
                          <strong>{maps.groups[r.groupId].name}</strong>
                          <small className="cell-secondary">
                            {maps.groups[r.groupId].classIds
                              .map((id: string) => named(s.classes, id))
                              .join(", ")}
                          </small>
                        </>
                      ),
                    },
                    {
                      label: "Предмет",
                      render: (r) => (
                        <Badge>{maps.subjects[r.subjectId].name}</Badge>
                      ),
                    },
                    {
                      label: "Учитель",
                      render: (r) => (
                        <span
                          className={
                            r.teacherRequired !== false && !teacherIds(r).length
                              ? "amber-text"
                              : ""
                          }
                        >
                          {teacherNames(s, r)}
                        </span>
                      ),
                    },
                    {
                      label: "Часов / нед.",
                      render: (r) => (
                        <strong>
                          {activeIn(r, activeCycle)
                            ? cycleHours(r, activeCycle)
                            : "Не ведётся"}
                        </strong>
                      ),
                    },
                    {
                      label: "В сетке",
                      render: (r) => (
                        <Badge
                          tone={
                            (counts[r.id] || 0) ===
                            (activeIn(r, activeCycle)
                              ? cycleHours(r, activeCycle)
                              : 0)
                              ? "green"
                              : "amber"
                          }
                        >
                          {counts[r.id] || 0} /{" "}
                          {activeIn(r, activeCycle)
                            ? cycleHours(r, activeCycle)
                            : 0}
                        </Badge>
                      ),
                    },
                    { label: "Синхронный блок", render: (r) => r.block || "—" },
                  ],
                  "workload",
                )}
              {page === "groups" &&
                tab === "classes" &&
                dataTable(
                  s.classes,
                  [
                    {
                      label: "Класс",
                      render: (r) => (
                        <strong className="class-name">{r.name}</strong>
                      ),
                    },
                    {
                      label: "Классный руководитель",
                      render: (r) =>
                        r.classTeacherId
                          ? named(s.teachers, r.classTeacherId)
                          : "—",
                    },
                    {
                      label: "Профиль звонков",
                      render: (r) => maps.profiles[r.profileId].name,
                    },
                    {
                      label: "Групп",
                      render: (r) =>
                        s.groups.filter(
                          (g) =>
                            g.kind !== "whole" && g.classIds.includes(r.id),
                        ).length,
                    },
                    {
                      label: "Занятий в сетке",
                      render: (r) =>
                        currentWeek.placements.filter((p) =>
                          placementMatches(
                            s,
                            maps.workloads[p.workloadId],
                            p,
                            "class",
                            r.id,
                          ),
                        ).length,
                    },
                  ],
                  "class",
                )}
              {page === "groups" && tab === "groups" && (
                <>
                  <div className="info-line">
                    <CircleHelp size={17} />
                    {s.students.length
                      ? `${s.students.filter((st) => st.active !== false).length} действующих учеников · ${s.students.filter((st) => st.active === false).length} отмечены выбывшими в источнике. Составы ${report.unknownGroups} групп требуют уточнения.`
                      : "Для точной проверки пересечений внесите учеников и выберите состав каждой группы."}
                  </div>
                  {s.groups.some((g) => g.rosterSources?.length) && (
                    <details className="roster-review">
                      <summary>
                        Что ещё проверить в составах · {report.unknownGroups}
                      </summary>
                      {s.groups
                        .filter((g) => g.kind !== "whole" && !rosterComplete(g))
                        .map((g) => (
                          <div key={g.id}>
                            <button
                              className="text-button"
                              onClick={() => open("group", g)}
                            >
                              {g.name}
                            </button>
                            <span>
                              {g.rosterNote ||
                                "В переданных файлах нет состава этой группы."}
                            </span>
                          </div>
                        ))}
                    </details>
                  )}
                  {dataTable(
                    s.groups.filter((g) => g.kind !== "whole"),
                    [
                      {
                        label: "Учебная группа",
                        render: (r) => <strong>{r.name}</strong>,
                      },
                      {
                        label: "Тип",
                        render: (r) => <Badge>{KINDS[r.kind]}</Badge>,
                      },
                      {
                        label: "Классы",
                        render: (r) => (
                          <span className="wrap">
                            {r.classIds
                              .map((id: string) => named(s.classes, id))
                              .join(", ")}
                          </span>
                        ),
                      },
                      {
                        label: "Состав",
                        render: (r) =>
                          r.studentIds.length ? (
                            <Badge tone={rosterComplete(r) ? "green" : "amber"}>
                              {r.studentIds.length} учеников
                              {!rosterComplete(r) ? " · уточнить" : ""}
                            </Badge>
                          ) : (
                            <Badge tone="amber">Не указан</Badge>
                          ),
                      },
                      {
                        label: "Источник состава",
                        render: (r) => (
                          <span className="wrap">
                            {r.rosterSources?.join("; ") ||
                              "Вручную / не заполнен"}
                          </span>
                        ),
                      },
                    ],
                    "group",
                  )}
                </>
              )}
              {page === "groups" && tab === "students" && (
                <div className="table-card">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Ученик</th>
                        <th>Класс</th>
                        <th>Группы</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.students
                        .filter((st) =>
                          st.name.toLowerCase().includes(search.toLowerCase()),
                        )
                        .map((st) => (
                          <tr key={st.id}>
                            <td>
                              <button
                                className="text-button student-name-link"
                                onClick={() => openStudent(st.id)}
                                aria-label={`Расписание ученика: ${st.name}, ${named(s.classes, st.classId)}`}
                              >
                                {st.name}
                                <ArrowUpRight size={13} />
                              </button>{" "}
                              {st.active === false && (
                                <Badge tone="amber">Выбыл в источнике</Badge>
                              )}
                            </td>
                            <td>{named(s.classes, st.classId)}</td>
                            <td>
                              {s.groups
                                .filter((g) => g.studentIds.includes(st.id))
                                .map((g) => g.name)
                                .join(", ") || "—"}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                  {!s.students.length && (
                    <Empty
                      title="Добавьте составы классов"
                      text="В экспорте aSc нет списков учеников. Они нужны, чтобы проверять пересечения core и electives."
                      action={
                        <button
                          className="primary"
                          onClick={() => open("students")}
                        >
                          <Upload size={16} />
                          Внести список
                        </button>
                      }
                    />
                  )}
                </div>
              )}
            </>
          )}
          {page === "calendar" && (
            <>
              <div className="calendar-intro">
                <div>
                  <strong>
                    {weekdays(s.settings.yearStart, s.settings.yearEnd)}
                  </strong>
                  <span>
                    будних дней в периоде
                    <br />
                    <small>до вычета каникул и праздников</small>
                  </span>
                </div>
                <p>
                  <CircleHelp size={18} /> Периоды перенесены с вашего
                  изображения. Проверьте даты. Reading Week и экзамены отмечены
                  отдельно. Для отмены занятий включите влияние периода и
                  выберите классы. Результат виден в разделе «Замены и даты»;
                  недельный шаблон сохраняется.
                </p>
              </div>
              <div className="calendar-legend">
                {Object.entries(EVENT_NAMES).map(([kind, label]) => (
                  <span key={kind}>
                    <i className={`event-dot ${kind}`} />
                    {label}
                  </span>
                ))}
              </div>
              <div className="calendar-grid">
                {Array.from({ length: Math.min(monthCount, 24) }, (_, i) => {
                  const date = new Date(
                    firstMonth.getFullYear(),
                    firstMonth.getMonth() + i,
                    1,
                  );
                  const year = date.getFullYear(),
                    month = date.getMonth();
                  const pad = (n: number) => String(n).padStart(2, "0");
                  const offset = (date.getDay() + 6) % 7;
                  return (
                    <section className="month" key={i}>
                      <h3>
                        {date
                          .toLocaleDateString("ru", {
                            month: "long",
                            year: "numeric",
                          })
                          .replace(" г.", "")}
                      </h3>
                      <div className="month-days">
                        {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => (
                          <small key={d}>{d}</small>
                        ))}
                        {Array.from({ length: offset }, (_, j) => (
                          <span key={"empty" + j} />
                        ))}
                        {Array.from(
                          { length: new Date(year, month + 1, 0).getDate() },
                          (_, j) => {
                            const day = `${year}-${pad(month + 1)}-${pad(j + 1)}`;
                            const events = s.events.filter(
                              (e) => e.start <= day && e.end >= day,
                            );
                            const event =
                              events.find((e) => e.kind === "holiday") ||
                              events[0];
                            return (
                              <span
                                key={day}
                                className={`calendar-day ${event?.kind || ""} ${day < s.settings.yearStart || day > s.settings.yearEnd ? "outside" : ""} ${(offset + j) % 7 > 4 ? "weekend" : ""}`}
                                title={`${day}${events.map((e) => " · " + e.name + " (" + e.grades + ")").join("")}`}
                              >
                                {j + 1}
                              </span>
                            );
                          },
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
              <h2 className="section-title">Периоды учебного года</h2>
              {dataTable(
                s.events.slice().sort((a, b) => a.start.localeCompare(b.start)),
                [
                  { label: "Период", render: (r) => <strong>{r.name}</strong> },
                  {
                    label: "Даты",
                    render: (r) =>
                      `${new Date(r.start + "T12:00").toLocaleDateString("ru")} — ${new Date(r.end + "T12:00").toLocaleDateString("ru")}`,
                  },
                  { label: "Классы", render: (r) => r.grades },
                  {
                    label: "Влияние",
                    render: (r) =>
                      r.effect === "cancel"
                        ? "Отмена: " +
                          (r.classIds?.length
                            ? r.classIds
                                .map((id: string) => named(s.classes, id))
                                .join(", ")
                            : "все классы")
                        : "Информационная отметка",
                  },
                  {
                    label: "Тип",
                    render: (r) => <Badge>{EVENT_NAMES[r.kind]}</Badge>,
                  },
                ],
                "event",
              )}
            </>
          )}
          {page === "settings" && (
            <div className="settings-grid">
              <section className="settings-card">
                <h2>Параметры школы</h2>
                <dl>
                  <dt>Название</dt>
                  <dd>{s.settings.name}</dd>
                  <dt>Учебный год</dt>
                  <dd>
                    {s.settings.yearStart} — {s.settings.yearEnd}
                  </dd>
                  <dt>Учебная неделя</dt>
                  <dd>
                    {s.settings.days.length} дней · {s.settings.shifts} смена
                  </dd>
                  <dt>Хранение</dt>
                  <dd>Локальная база на вашем компьютере</dd>
                  <dt>Версия данных</dt>
                  <dd>{s.revision}</dd>
                </dl>
              </section>
              <section className="settings-card">
                <h2>Резервные копии</h2>
                <p>
                  JSON содержит все справочники, группы, составы и расписание.
                  Перед каждым изменением сохраняется предыдущая версия (до 30).
                </p>
                <div className="button-row">
                  <button
                    onClick={() =>
                      download(
                        `atlas-${workspace}-${new Date().toISOString().slice(0, 10)}.json`,
                        JSON.stringify(s, null, 2),
                      )
                    }
                  >
                    <Download size={17} />
                    Скачать JSON
                  </button>
                  <button onClick={() => fileRef.current?.click()}>
                    <Upload size={17} />
                    Восстановить
                  </button>
                </div>
                <input
                  type="file"
                  accept=".json"
                  hidden
                  ref={fileRef}
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    try {
                      const data = JSON.parse(await file.text());
                      if (
                        data.schemaVersion !== 1 ||
                        !Array.isArray(data.placements)
                      )
                        throw Error("Файл не похож на резервную копию Атласа.");
                      open("restore", data);
                    } catch (e: any) {
                      setError(e.message);
                    }
                    e.target.value = "";
                  }}
                />
              </section>
              <CyclesEditor
                state={s}
                workspace={workspace}
                onState={receiveState}
              />
              <BellsEditor
                state={s}
                workspace={workspace}
                onState={receiveState}
              />
              <section className="settings-card full-span">
                <h2>Возможности Атласа</h2>
                <p>
                  Редактирование недельного расписания, справочников и нагрузки,
                  фиксация занятий, проверка пересечений по фактическому
                  времени, составы смешанных групп, синхронные блоки и
                  автоматическая генерация по заданным ограничениям.
                </p>
                <p>
                  Двойные и тройные занятия задаются в учебной нагрузке,
                  дополнительные условия — в разделе «Ограничения», учебные
                  циклы — в настройках. Доступны замены по датам, публикация
                  снимков, учётные записи, личные расписания, уведомления и
                  учебный журнал. На других устройствах нужен доступ к серверу
                  школы с HTTPS; локальный адрес работает на этом компьютере.
                </p>
              </section>
            </div>
          )}
          <footer className="page-footer">
            <span>Атлас · школьное расписание</span>
            <span>{yearLabel}</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Check size={17} />
          {toast}
        </div>
      )}
      {modal && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget && !busy) setModal(null);
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
            className={`modal ${["check", "importInfo", "generate", "group", "help", "preview"].includes(modal.type) ? "wide" : ""}`}
          >
            <header className="modal-header">
              <h2 id="modal-title">
                {
                  (
                    {
                      teacher: "Учитель",
                      room: "Кабинет",
                      subject: "Предмет",
                      class: "Класс",
                      group: "Учебная группа",
                      workload: "Учебная нагрузка",
                      placement: "Занятие",
                      students: "Добавить учеников",
                      event: "Календарный период",
                      settings: "Параметры школы",
                      generate: "Составить расписание",
                      check: "Проверка расписания",
                      preview: "Проверка изменений",
                      importInfo: "Результат импорта",
                      restore: "Восстановить резервную копию",
                      help: "Первая версия Атласа",
                    } as Record<string, string>
                  )[modal.type]
                }
              </h2>
              <button
                className="icon-button"
                aria-label="Закрыть"
                disabled={busy}
                onClick={() => setModal(null)}
              >
                <X size={20} />
              </button>
            </header>
            <div className="modal-body">
              {modal.returnToCheck && (
                <button
                  className="text-button back-to-check"
                  onClick={() => open("check", modal.returnToCheck)}
                >
                  <ChevronLeft size={16} />
                  Назад к проверке
                </button>
              )}
              {error && (
                <div className="error-banner" role="alert">
                  <AlertTriangle size={18} />
                  <span>{error}</span>
                </div>
              )}
              {modal.type === "preview" ? (
                <ChangePreview
                  key={item.token}
                  preview={item}
                  busy={busy}
                  onApply={() => applyPreview(item)}
                  onCancel={() => setModal(null)}
                />
              ) : modal.type === "check" ? (
                <>
                  <div className="check-summary">
                    <Badge tone={checkIssues.length ? "amber" : "green"}>
                      {checkIssues.length} пересечений / нарушений
                    </Badge>
                    <Badge tone={report.completeChecks ? "green" : "amber"}>
                      {report.completeChecks
                        ? "Проверка полная"
                        : "Проверка неполная"}
                    </Badge>
                    {!!checkWarnings.length && (
                      <Badge tone="amber">
                        {checkWarnings.length} невыполненных пожеланий
                      </Badge>
                    )}
                  </div>
                  {checkStudent && (
                    <div className="check-scope">
                      <strong>
                        {checkStudent.name} ·{" "}
                        {named(s.classes, checkStudent.classId)}
                      </strong>
                      <button
                        className="text-button"
                        onClick={() => open("check")}
                      >
                        Показать всю школу
                      </button>
                    </div>
                  )}
                  {checkStudent && (
                    <p className="muted small">
                      Показаны нарушения, затрагивающие этого ученика. Счётчики
                      полноты данных ниже относятся ко всей школе.
                    </p>
                  )}
                  <p className="muted">
                    Проверяем фактическое время, учителей, кабинеты и известные
                    составы групп, а также заданные ограничения. Совместные
                    занятия, записанные отдельно в исходном файле, могут
                    требовать объединения.
                  </p>
                  <div className="check-metrics">
                    <span>
                      Составы для уточнения <b>{report.unknownGroups}</b>
                    </span>
                    <span>
                      Нагрузки без учителя <b>{report.missingTeachers}</b>
                    </span>
                    <span>
                      Занятия без кабинета <b>{report.missingRooms}</b>
                    </span>
                    <span>
                      Не проверена вместимость{" "}
                      <b>{report.unknownCapacity || 0}</b>
                    </span>
                    <span>
                      Не проверены переходы{" "}
                      <b>{report.unknownBuildings || 0}</b>
                    </span>
                  </div>
                  <ConflictList
                    state={s}
                    issues={checkIssues}
                    onLesson={(p) => openFromCheck("placement", p)}
                    onGroup={(g) => openFromCheck("group", g)}
                    onStudent={openStudent}
                  />
                  {!!checkWarnings.length && (
                    <>
                      <h3>Пожелания</h3>
                      <p className="muted">
                        Эти отклонения учитываются при улучшении расписания и не
                        запрещают его применение.
                      </p>
                      <ConflictList
                        state={s}
                        issues={checkWarnings}
                        onLesson={(p) => openFromCheck("placement", p)}
                        onGroup={(g) => openFromCheck("group", g)}
                        onStudent={openStudent}
                      />
                    </>
                  )}
                  {!checkIssues.length && (
                    <div className="success-box">
                      <Check size={18} />В пределах заполненных данных
                      пересечений нет.
                    </div>
                  )}
                </>
              ) : modal.type === "importInfo" ? (
                <>
                  <p>
                    Источник: <strong>{s.importInfo.filename}</strong>
                  </p>
                  <div className="check-metrics">
                    <span>
                      Заполненных ячеек <b>{s.importInfo.sourceCells}</b>
                    </span>
                    <span>
                      Занятий после объединения{" "}
                      <b>{s.importInfo.importedLessons}</b>
                    </span>
                  </div>
                  {s.importInfo.notes.map((n, i) => (
                    <p className="import-note" key={i}>
                      <span>{i + 1}</span>
                      {n}
                    </p>
                  ))}
                </>
              ) : modal.type === "help" ? (
                <>
                  <p>
                    Начните с просмотра своего расписания. Нажмите на урок,
                    чтобы поменять день, номер, кабинет или закрепление. Для
                    переноса можно использовать мышь.
                  </p>
                  <p>
                    В «Учебной нагрузке» задаются часы, учителя, допустимые
                    кабинеты и синхронные блоки. Для смешанных групп внесите
                    составы учеников.
                  </p>
                  <p>
                    Кнопка «Попробовать на примере» открывает отдельный набор
                    данных. В нём можно проверить генерацию, не меняя расписание
                    школы.
                  </p>
                  <p>
                    Все изменения сохраняются локально. Резервную копию можно
                    скачать в настройках.
                  </p>
                </>
              ) : modal.type === "restore" ? (
                <>
                  <p>
                    Будет восстановлено {item.classes?.length || 0} классов и{" "}
                    {item.placements.length} занятий. Текущее состояние
                    останется в истории.
                  </p>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={async () => {
                      if (
                        await commit(
                          { ...item, revision: s.revision },
                          "Резервная копия восстановлена",
                        )
                      )
                        setModal(null);
                    }}
                  >
                    Восстановить данные
                  </button>
                </>
              ) : modal.type === "generate" ? (
                <GenerationPanel
                  defaultCycle={activeCycle}
                  state={s}
                  workspace={workspace}
                  defaultScope={
                    workspace === "demo"
                      ? "all"
                      : mode === "student" && selectedStudent
                        ? selectedStudent.classId
                        : mode === "class"
                          ? selected
                          : "all"
                  }
                  onApply={applyPreview}
                />
              ) : (
                <form onSubmit={saveForm}>
                  {[
                    "teacher",
                    "room",
                    "subject",
                    "class",
                    "group",
                    "event",
                  ].includes(modal.type) && (
                    <Field
                      label={
                        modal.type === "teacher" ? "Имя учителя" : "Название"
                      }
                    >
                      <input
                        name="name"
                        required
                        maxLength={160}
                        defaultValue={item?.name || ""}
                        placeholder={
                          modal.type === "class" ? "Например, 10A" : ""
                        }
                      />
                    </Field>
                  )}
                  {modal.type === "teacher" && (
                    <Field
                      label="Недоступные уроки"
                      hint="День от 0 (пн) до 4 (пт), затем номер урока. Например: 0:1, 0:2, 4:10. Ограничение действует для всех профилей звонков."
                    >
                      <input
                        name="unavailable"
                        defaultValue={item?.unavailable?.join(", ") || ""}
                        placeholder="0:1, 0:2"
                      />
                    </Field>
                  )}
                  {modal.type === "room" && (
                    <>
                      <Field
                        label="Вместимость, человек"
                        hint="Пусто — неизвестна. Для проверки также нужна численность класса или полный состав группы."
                      >
                        <input
                          type="number"
                          min={1}
                          max={5000}
                          name="capacity"
                          defaultValue={item?.capacity ?? ""}
                        />
                      </Field>
                      <Field label="Здание">
                        <select
                          name="buildingId"
                          defaultValue={item?.buildingId || ""}
                        >
                          {options(s.campus?.buildings || [], "Не указано")}
                        </select>
                      </Field>
                    </>
                  )}
                  {modal.type === "subject" && (
                    <Field label="Цвет в расписании">
                      <input
                        type="color"
                        name="color"
                        defaultValue={item?.color || "#dceae2"}
                      />
                    </Field>
                  )}
                  {modal.type === "class" && (
                    <>
                      <Field
                        label="Численность класса"
                        hint="Для проверки вместимости. Частичный импорт списка учеников не считается полной численностью."
                      >
                        <input
                          type="number"
                          min={1}
                          max={500}
                          name="studentCount"
                          defaultValue={item?.studentCount ?? ""}
                        />
                      </Field>
                      <Field label="Профиль звонков">
                        <select
                          name="profileId"
                          defaultValue={item?.profileId || "primary"}
                        >
                          {options(s.profiles)}
                        </select>
                      </Field>
                      <Field
                        label="Классный руководитель"
                        hint="Получает по умолчанию объяснительные, сводную посещаемость, контроль журналов и похвалы/замечания своего класса. Назначает администратор; смены сохраняются в истории («Пользователи и доступ» → «Права работы с классами»)."
                      >
                        <select
                          name="classTeacherId"
                          defaultValue={item?.classTeacherId || ""}
                          disabled={!canAssignClassTeacher}
                        >
                          {options(s.teachers, "Не назначен")}
                        </select>
                      </Field>
                      <Field
                        label="Ориентир уроков в день"
                        hint="Справочный ориентир. Чтобы применять его при генерации, создайте дневной предел в разделе «Ограничения»."
                      >
                        <input
                          type="number"
                          min="1"
                          max="12"
                          name="maxDaily"
                          defaultValue={item?.maxDaily || 8}
                        />
                      </Field>
                    </>
                  )}
                  {modal.type === "group" && (
                    <>
                      {!!item?.rosterSources?.length && (
                        <div className="roster-source">
                          <strong>{item.rosterSources.join("; ")}</strong>
                          {item.rosterNote && <p>{item.rosterNote}</p>}
                        </div>
                      )}
                      <Field
                        label="Тип группы"
                        hint="Для варианта «Без класса» классы и ученики ниже не сохраняются. Подходит для совещаний сотрудников."
                      >
                        <select name="kind" defaultValue={item?.kind || "core"}>
                          {Object.entries(KINDS).map(([id, name]) => (
                            <option key={id} value={id}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <div className="field">
                        <span>Классы, из которых набирается группа</span>
                        <div className="checkbox-grid">
                          {s.classes.map((c) => (
                            <label key={c.id}>
                              <input
                                type="checkbox"
                                checked={groupClasses.includes(c.id)}
                                onChange={() =>
                                  toggle(c.id, groupClasses, setGroupClasses)
                                }
                              />
                              {c.name}
                            </label>
                          ))}
                        </div>
                      </div>
                      <div className="field">
                        <span>
                          Состав группы ·{" "}
                          {
                            groupStudents.filter((id) =>
                              s.students.some(
                                (st) =>
                                  st.id === id &&
                                  groupClasses.includes(st.classId),
                              ),
                            ).length
                          }{" "}
                          учеников
                        </span>
                        {!s.students.length ? (
                          <div className="info-line">
                            Сначала внесите списки учеников в разделе «Классы и
                            группы».
                          </div>
                        ) : (
                          <div className="student-options">
                            {s.students
                              .filter((st) => groupClasses.includes(st.classId))
                              .map((st) => (
                                <label key={st.id}>
                                  <input
                                    type="checkbox"
                                    checked={groupStudents.includes(st.id)}
                                    onChange={() =>
                                      toggle(
                                        st.id,
                                        groupStudents,
                                        setGroupStudents,
                                      )
                                    }
                                  />
                                  {st.name}
                                  {st.active === false && (
                                    <small>Выбыл в источнике</small>
                                  )}
                                  <small>{named(s.classes, st.classId)}</small>
                                </label>
                              ))}
                          </div>
                        )}
                      </div>
                      <label className="roster-confirm">
                        <input
                          type="checkbox"
                          name="rosterComplete"
                          defaultChecked={item ? rosterComplete(item) : true}
                        />
                        Состав внесён полностью и проверен
                      </label>
                    </>
                  )}
                  {modal.type === "workload" && (
                    <>
                      <fieldset className="rule-days">
                        <legend>
                          Активные недели — без отметок все недели
                        </legend>
                        {patterns(s).map((p) => (
                          <label className="inline-check" key={p.id}>
                            <input
                              type="checkbox"
                              name="cycleIds"
                              value={p.id}
                              defaultChecked={item?.cycleIds?.includes(p.id)}
                            />
                            {p.name}
                          </label>
                        ))}
                      </fieldset>
                      {patterns(s).length > 1 && (
                        <details>
                          <summary>
                            Отдельные часы и длины занятий по неделям
                          </summary>
                          <p className="muted">
                            Пустые часы используют общую нагрузку ниже. Если
                            часы переопределены, пустая длина означает одиночные
                            уроки.
                          </p>
                          {patterns(s).map((p) => (
                            <div className="form-grid" key={p.id}>
                              <Field label={`${p.name}: часы`}>
                                <input
                                  type="number"
                                  min={1}
                                  max={100}
                                  name={`cycleHours:${p.id}`}
                                  defaultValue={item?.cycleHours?.[p.id] ?? ""}
                                />
                              </Field>
                              <label className="inline-check">
                                <input
                                  type="checkbox"
                                  name={`overrideLengths:${p.id}`}
                                  defaultChecked={Object.hasOwn(
                                    item?.cycleLessonLengths || {},
                                    p.id,
                                  )}
                                />
                                Отдельные длины в {p.name}; пустое поле —
                                одиночные уроки
                              </label>
                              <Field label={`${p.name}: длины занятий`}>
                                <input
                                  name={`cycleLengths:${p.id}`}
                                  defaultValue={
                                    item?.cycleLessonLengths?.[p.id]?.join(
                                      " + ",
                                    ) || ""
                                  }
                                  placeholder="2 + 2 + 1"
                                />
                              </Field>
                            </div>
                          ))}
                        </details>
                      )}
                      <Field label="Класс / учебная группа">
                        <select
                          required
                          name="groupId"
                          defaultValue={item?.groupId || ""}
                        >
                          {options(s.groups, "Выберите группу")}
                        </select>
                      </Field>
                      <div className="form-grid">
                        <Field label="Предмет">
                          <select
                            required
                            name="subjectId"
                            defaultValue={item?.subjectId || ""}
                          >
                            {options(s.subjects, "Выберите предмет")}
                          </select>
                        </Field>
                        <Field label="Учитель">
                          <select
                            name="teacherId"
                            defaultValue={item?.teacherId || ""}
                          >
                            {options(s.teachers, "Не указан")}
                          </select>
                        </Field>
                        <Field label="Часов в неделю">
                          <input
                            required
                            name="hours"
                            type="number"
                            min="1"
                            max="100"
                            defaultValue={item?.hours || 3}
                          />
                        </Field>
                        <Field label="Максимум в день">
                          <input
                            required
                            name="maxPerDay"
                            type="number"
                            min="1"
                            max="12"
                            defaultValue={item?.maxPerDay || 2}
                          />
                        </Field>
                      </div>
                      <Field
                        label="Совместные преподаватели"
                        hint="Все выбранные учителя заняты на каждом уроке этой нагрузки. Основного учителя повторно выбирать не нужно."
                      >
                        <select
                          multiple
                          name="additionalTeacherIds"
                          size={4}
                          defaultValue={item?.additionalTeacherIds || []}
                        >
                          {options(s.teachers)}
                        </select>
                      </Field>
                      <label className="roster-confirm">
                        <input
                          type="checkbox"
                          name="withoutTeacher"
                          defaultChecked={item?.teacherRequired === false}
                        />
                        Занятие без учителя — очистить назначения
                      </label>
                      <Field
                        label="Численность учеников этой нагрузки"
                        hint="Необязательное уточнение для вместимости. Для смешанной группы используется полный состав; для общего урока — численность класса. Внесённое число не может уменьшить известный состав."
                      >
                        <input
                          type="number"
                          name="studentCount"
                          min={1}
                          max={5000}
                          defaultValue={item?.studentCount ?? ""}
                        />
                      </Field>
                      <Field
                        label="Длины занятий за неделю"
                        hint="Например, 2 + 2 + 1 — две пары и один одиночный урок. Сумма должна совпадать с недельными часами. Пусто — все уроки одиночные. Поддерживаются длины 1, 2, 3."
                      >
                        <input
                          name="lessonLengths"
                          defaultValue={item?.lessonLengths?.join(" + ") || ""}
                          placeholder="2 + 2 + 1"
                        />
                      </Field>
                      <Field
                        label="Максимальная перемена внутри пары/тройки, минут"
                        hint="Занятие занимает соседние периоды одного дня в одном кабинете. Длинный перерыв разрывает занятие."
                      >
                        <input
                          name="maxLessonBreak"
                          type="number"
                          min={0}
                          max={180}
                          required
                          defaultValue={item?.maxLessonBreak ?? 20}
                        />
                      </Field>
                      <Field
                        label="Основной кабинет: выбрать один из списка"
                        hint="Выберите варианты с Ctrl. Генератор назначит один из них. Можно оставить пусто, если ниже заданы постоянные кабинеты."
                      >
                        <select
                          multiple
                          name="roomIds"
                          size={4}
                          defaultValue={item?.roomIds || []}
                        >
                          {options(s.rooms)}
                        </select>
                      </Field>
                      <Field
                        label="Постоянные кабинеты: занимать все одновременно"
                        hint="Добавляются к основному кабинету на каждом уроке. Не повторяйте их в списке вариантов. Все помещения одного занятия должны находиться в одном здании; вместимость суммируется."
                      >
                        <select
                          multiple
                          name="requiredRoomIds"
                          size={4}
                          defaultValue={item?.requiredRoomIds || []}
                        >
                          {options(s.rooms)}
                        </select>
                      </Field>
                      <label className="roster-confirm">
                        <input
                          type="checkbox"
                          name="withoutRoom"
                          defaultChecked={item?.roomRequired === false}
                        />
                        Занятие без кабинета — очистить назначения
                      </label>
                      <Field label="Профиль звонков">
                        <select
                          name="profileId"
                          defaultValue={item?.profileId || "secondary"}
                        >
                          {options(s.profiles)}
                        </select>
                      </Field>
                      <Field
                        label="Допустимые номера уроков"
                        hint="Через запятую. Пустое поле разрешает все периоды, включая нулевой."
                      >
                        <input
                          name="allowedPeriods"
                          defaultValue={
                            item?.allowedPeriods?.join(", ") ||
                            "1, 2, 3, 4, 5, 6, 7, 8"
                          }
                        />
                      </Field>
                      <Field
                        label="Синхронный блок (необязательно)"
                        hint="Одинаковое имя блока заставляет занятия проходить одновременно. Часы и профиль звонков должны совпадать."
                      >
                        <input
                          name="block"
                          defaultValue={item?.block || ""}
                          placeholder="Например, core-10"
                        />
                      </Field>
                    </>
                  )}
                  {modal.type === "placement" && (
                    <>
                      <Field label="Неделя занятия">
                        <select
                          name="cycleId"
                          defaultValue={item?.cycleId || activeCycle}
                        >
                          {patterns(s).map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Занятие из учебной нагрузки">
                        <select
                          name="workloadId"
                          required
                          value={placementLoad}
                          onChange={(e) => setPlacementLoad(e.target.value)}
                        >
                          <option value="">Выберите занятие</option>
                          {s.workloads
                            .filter(
                              (w) =>
                                item?.workloadId === w.id ||
                                (activeIn(w, activeCycle) &&
                                  (counts[w.id] || 0) <
                                    cycleHours(w, activeCycle) &&
                                  placementMatches(s, w, null, mode, selected)),
                            )
                            .map((w) => (
                              <option key={w.id} value={w.id}>
                                {loadLabel(w)} · {teacherNames(s, w)}
                              </option>
                            ))}
                        </select>
                      </Field>
                      <div className="form-grid">
                        <Field label="День">
                          <select
                            name="day"
                            value={placementDay}
                            onChange={(e) =>
                              setPlacementDay(Number(e.target.value))
                            }
                          >
                            {s.settings.days.map((day, i) => (
                              <option key={day} value={i}>
                                {day}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Номер урока">
                          <select
                            name="period"
                            key={placementLoad}
                            defaultValue={item?.period ?? modal.period ?? 1}
                          >
                            {(
                              maps.profiles[
                                maps.workloads[placementLoad]?.profileId
                              ]?.dayPeriods?.[String(placementDay)] ||
                              maps.profiles[
                                maps.workloads[placementLoad]?.profileId
                              ]?.periods ||
                              []
                            ).map(
                              (
                                p: {
                                  label: string;
                                  start: string;
                                  end: string;
                                },
                                i: number,
                              ) => (
                                <option key={i} value={i}>
                                  {p.label} · {p.start}–{p.end}
                                </option>
                              ),
                            )}
                          </select>
                        </Field>
                      </div>
                      <Field
                        label="Основной кабинет"
                        hint={
                          maps.workloads[placementLoad]?.requiredRoomIds?.length
                            ? `Также заняты постоянно: ${maps.workloads[placementLoad].requiredRoomIds.map((id: string) => named(s.rooms, id)).join(", ")}.`
                            : undefined
                        }
                      >
                        <select
                          name="roomId"
                          defaultValue={item?.roomId || ""}
                          disabled={
                            maps.workloads[placementLoad]?.roomRequired ===
                            false
                          }
                        >
                          {options(s.rooms, "Не указан")}
                        </select>
                      </Field>
                      <label className="checkbox-label">
                        <input
                          name="locked"
                          type="checkbox"
                          defaultChecked={item?.locked || false}
                        />
                        <LockKeyhole size={15} />
                        Закрепить время и кабинет
                      </label>
                      <p className="muted small">
                        Перед сохранением откроется проверка изменений. Новые
                        нарушения блокируют применение. Исходные данные импорта
                        сохраняются в истории.
                      </p>
                    </>
                  )}
                  {modal.type === "students" && (
                    <Field
                      label="Одна строка — один ученик"
                      hint="Формат: имя или внутренний код;класс. Можно использовать обезличенные коды. Существующие ученики с тем же именем и классом не добавляются повторно."
                    >
                      <textarea
                        name="students"
                        required
                        rows={12}
                        placeholder={"Ученик 001;10A\nУченик 002;10D"}
                      />
                    </Field>
                  )}
                  {modal.type === "event" && (
                    <>
                      <div className="form-grid">
                        <Field label="С">
                          <input
                            required
                            type="date"
                            name="start"
                            defaultValue={item?.start || s.settings.yearStart}
                          />
                        </Field>
                        <Field label="По">
                          <input
                            required
                            type="date"
                            name="end"
                            defaultValue={item?.end || s.settings.yearStart}
                          />
                        </Field>
                      </div>
                      <Field label="Тип">
                        <select
                          name="kind"
                          defaultValue={item?.kind || "holiday"}
                        >
                          {Object.entries(EVENT_NAMES).map(([key, name]) => (
                            <option key={key} value={key}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Для каких классов">
                        <input
                          name="grades"
                          defaultValue={item?.grades || "Все классы"}
                        />
                      </Field>
                      <Field label="Влияние на занятия по датам">
                        <select
                          name="effect"
                          defaultValue={item?.effect || "note"}
                        >
                          <option value="note">
                            Только информационная отметка
                          </option>
                          <option value="cancel">
                            Отменить занятия для выбранных классов
                          </option>
                        </select>
                      </Field>
                      <fieldset className="dated-multi">
                        <legend>
                          Классы для отмены · пустой выбор означает все классы
                        </legend>
                        <div>
                          {s.classes.map((c) => (
                            <label className="check-line" key={c.id}>
                              <input
                                type="checkbox"
                                name="eventClassIds"
                                value={c.id}
                                defaultChecked={item?.classIds?.includes(c.id)}
                              />
                              {c.name}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      <p className="muted">
                        Текст «Для каких классов» — подпись. Отмену определяет
                        выбор выше. В смешанной группе исключаются ученики
                        выбранных классов; остальные продолжают занятие.
                      </p>
                    </>
                  )}
                  {modal.type === "settings" && (
                    <>
                      <Field label="Название школы">
                        <input
                          name="name"
                          required
                          defaultValue={s.settings.name}
                        />
                      </Field>
                      <div className="form-grid">
                        <Field label="Начало учебного года">
                          <input
                            name="yearStart"
                            type="date"
                            required
                            defaultValue={s.settings.yearStart}
                          />
                        </Field>
                        <Field label="Конец учебного года">
                          <input
                            name="yearEnd"
                            type="date"
                            required
                            defaultValue={s.settings.yearEnd}
                          />
                        </Field>
                      </div>
                    </>
                  )}
                  <div className="modal-actions">
                    {item?.id && (
                      <button
                        className="danger subtle"
                        type="button"
                        disabled={busy}
                        onClick={() => remove(modal.type, item.id)}
                      >
                        <Trash2 size={15} />
                        {modal.type === "placement"
                          ? "Снять с сетки"
                          : "Удалить"}
                      </button>
                    )}
                    <span className="spacer" />
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setModal(null)}
                    >
                      Отмена
                    </button>
                    <button className="primary" disabled={busy}>
                      {busy ? (
                        <LoaderCircle size={16} className="spin" />
                      ) : (
                        <Check size={16} />
                      )}
                      {modal.type === "event"
                        ? "Проверить период"
                        : "Сохранить"}
                    </button>
                  </div>
                </form>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
