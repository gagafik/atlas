import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import {
  Activity,
  Archive,
  Award,
  Backpack,
  Bell,
  BookMarked,
  BookOpen,
  CalendarCheck,
  CalendarClock,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  ClipboardList,
  Clock3,
  CreditCard,
  FileText,
  FlaskConical,
  FolderOpen,
  Gauge,
  Globe,
  GraduationCap,
  Grid2X2,
  Handshake,
  House,
  KeyRound,
  Layers3,
  LayoutGrid,
  Library,
  ListChecks,
  LockKeyhole,
  LogOut,
  Mail,
  MessageSquare,
  MessagesSquare,
  Monitor,
  MonitorPlay,
  Newspaper,
  NotebookPen,
  NotebookText,
  PackageSearch,
  PanelLeftClose,
  Printer,
  Replace,
  Ruler,
  ScrollText,
  Send,
  Settings,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Trophy,
  UserCheck,
  UserRound,
  UserPlus,
  Users,
  UsersRound,
  Utensils,
  Wrench,
} from "lucide-react";
import NotificationCenter from "./NotificationCenter";
import { roleNames, type Account } from "./auth";
import type { JournalPanelName } from "./journal";
import { unreadLabel } from "./messages";
import {
  accountMenu,
  activeItem,
  buildMenu,
  groupOf,
  isGroup,
  menuItems,
  menuTitle,
  type AccountId,
  type MenuContext,
  type ModalId,
  type NavItem,
  type NavMenu,
} from "./navigation";
import type { Unread } from "./shellData";

type Icon = ComponentType<{ size?: number; className?: string }>;
// The icon of an entry is named in navigation.ts; this is the one place that knows the drawings.
const ICONS: Record<string, Icon> = {
  home: House,
  notices: MessageSquare,
  schedule: CalendarDays,
  planner: Grid2X2,
  rules: Ruler,
  workloads: ClipboardList,
  courses: BookMarked,
  assistant: Sparkles,
  calendar: CalendarDays,
  exchange: Printer,
  publications: Send,
  journal: BookOpen,
  dated: Replace,
  commitments: CalendarClock,
  grades: Award,
  reports: ScrollText,
  attendance: UserCheck,
  curriculum: ClipboardList,
  materials: FolderOpen,
  homework: NotebookPen,
  quizzes: ListChecks,
  groups: Users,
  teachers: UserRound,
  rooms: LayoutGrid,
  subjects: Layers3,
  access: LockKeyhole,
  settings: Settings,
  classStaff: UsersRound,
  duties: ShieldCheck,
  forms: ClipboardCheck,
  live: MonitorPlay,
  library: Library,
  lostFound: PackageSearch,
  behaviour: Mail,
  assessments: CalendarCheck,
  clubs: Trophy,
  canteen: Utensils,
  handover: Backpack,
  payments: CreditCard,
  admissions: UserPlus,
  site: Globe,
  documents: FileText,
  staffTime: Clock3,
  workload: Gauge,
  registers: NotebookText,
  meetings: Handshake,
  consultations: MessagesSquare,
  hall: Monitor,
  sections: LayoutGrid,
  operations: Activity,
  learning: GraduationCap,
  documentation: Archive,
  communication: Send,
  agenda: Newspaper,
  control: Wrench,
  family: Users,
  invitation: UserPlus,
  password: KeyRound,
  recovery: ShieldCheck,
  app: Smartphone,
  signOut: LogOut,
};
export const iconOf = (name: string): Icon => ICONS[name] ?? CircleHelp;

/** What AuthShell hands to the screen of a role: the facts the menu depends on and how to act on a click. */
export interface ShellFrame {
  context: Omit<MenuContext, "journalPanels">;
  identity: string;
  user: Account | null;
  busy: boolean;
  onModal(id: ModalId): void;
  onAccount(id: AccountId): void;
}
export interface ShellApi {
  title: string;
  menu: NavMenu;
  select(item: NavItem): void;
}

/** The frame of every role: the menu on the left, the top bar, and the screen of the role as `children`. */
export default function Shell({
  frame,
  journalPanels,
  view,
  journalView,
  onView,
  school,
  year,
  unread,
  badges,
  extra,
  accountExtra,
  footer,
  children,
}: {
  frame: ShellFrame;
  journalPanels: JournalPanelName[] | null;
  view: string;
  journalView?: JournalPanelName;
  onView: (view: string, journal?: JournalPanelName) => void;
  school?: string;
  year?: string;
  unread?: Unread;
  badges?: Record<string, number | undefined>;
  extra?: ReactNode;
  accountExtra?: { label: string; onSelect: () => void; disabled?: boolean }[];
  footer?: ReactNode;
  children: (shell: ShellApi) => ReactNode;
}) {
  const context = { ...frame.context, journalPanels };
  const menu = buildMenu(context);
  const accounts = accountMenu(context);
  const active = activeItem(menu, view, journalView);
  const title = menuTitle(menu, accounts, view, journalView);
  const [drawer, setDrawer] = useState(false),
    [accountOpen, setAccountOpen] = useState(false),
    [openGroups, setOpenGroups] = useState<string[]>([]);
  const accountBox = useRef<HTMLDivElement>(null),
    accountToggle = useRef<HTMLButtonElement>(null),
    sidebar = useRef<HTMLElement>(null),
    menuToggle = useRef<HTMLButtonElement>(null),
    shown = useRef(false);
  // The group of the screen on show is open, wherever the screen was reached from.
  const activeGroup = active ? groupOf(menu, active.id)?.id : undefined;
  useEffect(() => {
    if (activeGroup)
      setOpenGroups((open) =>
        open.includes(activeGroup) ? open : [...open, activeGroup],
      );
  }, [active?.id, activeGroup]);
  // A new screen opens at its top, whatever the scroll position was when the fixed menu was used.
  useLayoutEffect(() => {
    if (shown.current) window.scrollTo(0, 0);
    shown.current = true;
  }, [view, journalView]);
  // The drawer works as a dialog: the focus goes into the menu when it opens and back to its button when it closes.
  function closeDrawer() {
    if (!drawer) return;
    setDrawer(false);
    menuToggle.current?.focus();
  }
  useEffect(() => {
    if (!drawer) return;
    const nav = sidebar.current?.querySelector("nav");
    (
      nav?.querySelector<HTMLElement>('[aria-current="page"]') ??
      nav?.querySelector<HTMLElement>("button")
    )?.focus();
  }, [drawer]);
  useEffect(() => {
    if (!drawer && !accountOpen) return;
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (accountOpen) accountToggle.current?.focus();
        setAccountOpen(false);
        closeDrawer();
      } else if (event.key === "Tab" && drawer && sidebar.current) {
        // Tab stays inside the open drawer: the rest of the page is behind its backdrop.
        const stops = [
          ...sidebar.current.querySelectorAll<HTMLElement>("a[href], button"),
        ].filter((stop) => !stop.closest("[hidden]"));
        const first = stops[0],
          last = stops[stops.length - 1],
          at = document.activeElement;
        if (!sidebar.current.contains(at) || (event.shiftKey && at === first)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (!event.shiftKey && at === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    const outside = (event: MouseEvent) => {
      if (!accountBox.current?.contains(event.target as Node))
        setAccountOpen(false);
    };
    document.addEventListener("keydown", keys);
    document.addEventListener("mousedown", outside);
    return () => {
      document.removeEventListener("keydown", keys);
      document.removeEventListener("mousedown", outside);
    };
  }, [drawer, accountOpen]);
  function select(item: NavItem) {
    closeDrawer();
    setAccountOpen(false);
    const { target } = item;
    if (target.kind === "view") onView(target.view, target.journal);
    else if (target.kind === "modal") frame.onModal(target.modal);
    else frame.onAccount(target.account);
  }
  const home = menuItems(menu).find((item) => item.id === "home");
  const unreadText =
    unread && (unread.count || unread.more)
      ? unreadLabel(unread.count, unread.more)
      : "";
  function entry(item: NavItem) {
    const Icon = iconOf(item.icon),
      badge = badges?.[item.id] ?? (item.id === "notices" ? unreadText : "");
    return (
      <button
        key={item.id}
        className={item === active ? "active" : ""}
        aria-current={item === active ? "page" : undefined}
        onClick={() => select(item)}
      >
        <Icon size={18} />
        <span>{item.label}</span>
        {!!badge && <span className="nav-count">{badge}</span>}
      </button>
    );
  }
  const signOut = accounts.find((item) => item.id === "signOut");
  const accountItem = (item: (typeof accounts)[number]) => {
    const Icon = iconOf(item.icon);
    return (
      <button
        key={item.id}
        disabled={item.id === "signOut" && frame.busy}
        onClick={() => select(item)}
      >
        <Icon size={16} />
        <span>{item.label}</span>
      </button>
    );
  };
  return (
    <div className="app-shell">
      <aside
        id="shell-sidebar"
        ref={sidebar}
        className={`sidebar ${drawer ? "open" : ""}`}
      >
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            if (home) select(home);
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
        <nav aria-label="Главное меню">
          {menu.map((block, index) => (
            <Fragment key={index}>
              {index > 0 && <div className="nav-divider" />}
              {block.map((item) => {
                if (!isGroup(item)) return entry(item);
                const open = openGroups.includes(item.id),
                  Icon = iconOf(item.icon),
                  holds = item.items.some((child) => child === active),
                  // A folded group still tells that something inside it waits.
                  waiting = open
                    ? 0
                    : item.items.reduce(
                        (sum, child) => sum + (badges?.[child.id] ?? 0),
                        0,
                      );
                return (
                  <div className="nav-group" key={item.id}>
                    <button
                      className={holds && !open ? "active" : ""}
                      aria-expanded={open}
                      aria-controls={`nav-group-${item.id}`}
                      onClick={() =>
                        setOpenGroups(
                          open
                            ? openGroups.filter((id) => id !== item.id)
                            : [...openGroups, item.id],
                        )
                      }
                    >
                      <Icon size={18} />
                      <span>{item.label}</span>
                      {!!waiting && (
                        <span className="nav-count">{waiting}</span>
                      )}
                      <ChevronRight className="nav-arrow" size={14} />
                    </button>
                    <div
                      className="nav-group-items"
                      id={`nav-group-${item.id}`}
                      hidden={!open}
                    >
                      {item.items.map(entry)}
                    </div>
                  </div>
                );
              })}
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {year && <div className="school-year">{year} учебный год</div>}
          {footer}
        </div>
      </aside>
      {drawer && <div className="sidebar-backdrop" onClick={closeDrawer} />}
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              ref={menuToggle}
              className="icon-button menu-toggle"
              aria-label="Меню"
              aria-controls="shell-sidebar"
              aria-expanded={drawer}
              onClick={() => setDrawer(!drawer)}
            >
              <PanelLeftClose size={19} />
            </button>
            <strong>{school || "Атлас"}</strong>
            {year && <small>{year}</small>}
          </div>
          <div className="topbar-right">
            {extra}
            <NotificationCenter
              key={frame.identity}
              identity={frame.identity}
            />
            <button
              className="icon-button messages-button"
              title="Сообщения"
              aria-label={`Сообщения${unreadText ? `, непрочитанных: ${unreadText}` : ""}`}
              onClick={() => onView("messages")}
            >
              <MessageSquare size={18} />
              {unreadText && <b>{unreadText}</b>}
            </button>
            <div className="account" ref={accountBox}>
              <button
                ref={accountToggle}
                className="account-toggle"
                aria-expanded={accountOpen}
                onClick={() => setAccountOpen(!accountOpen)}
              >
                <UserRound size={16} />
                <span>{frame.user?.name || "Локальный пользователь"}</span>
                <ChevronDown size={14} />
              </button>
              {accountOpen && (
                <div className="account-menu">
                  <div className="account-head">
                    <strong>
                      {frame.user?.name || "Локальный пользователь"}
                    </strong>
                    <small>
                      {frame.user
                        ? roleNames[frame.user.role]
                        : "Вход не включён"}
                    </small>
                  </div>
                  {accounts.filter((item) => item !== signOut).map(accountItem)}
                  {accountExtra?.map((item) => (
                    <button
                      key={item.label}
                      disabled={item.disabled}
                      onClick={() => {
                        setAccountOpen(false);
                        item.onSelect();
                      }}
                    >
                      <FlaskConical size={16} />
                      <span>{item.label}</span>
                    </button>
                  ))}
                  {signOut && accountItem(signOut)}
                </div>
              )}
            </div>
          </div>
        </header>
        {children({ title, menu, select })}
      </div>
    </div>
  );
}
