import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentType,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
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
  ChevronLeft,
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
import type { JournalPanelName, JournalView } from "./journal";
import { unreadLabel } from "./messages";
import {
  accountMenu,
  activeItem,
  buildMenu,
  groupOf,
  isGroup,
  menuItems,
  menuTitle,
  recallRail,
  rememberRail,
  stepIndex,
  type AccountId,
  type MenuContext,
  type ModalId,
  type NavGroup,
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

// Above this width the menu stays beside the page: groups open as flyouts and the menu can fold to icons.
// At and below it the menu is the drawer and groups unfold in place (the same width as the media queries in styles.css).
const DESKTOP = "(min-width: 901px)";
function useDesktop() {
  const [desktop, setDesktop] = useState(
    () => window.matchMedia?.(DESKTOP).matches ?? true,
  );
  useEffect(() => {
    const query = window.matchMedia?.(DESKTOP);
    if (!query) return;
    const change = () => setDesktop(query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return desktop;
}
// A flyout keeps this far from the edge of the screen and holds up to this many tiles in a row.
const FLYOUT_GAP = 8;
const FLYOUT_COLUMNS = 3;

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
  journalView?: JournalView;
  onView: (view: string, journal?: JournalView) => void;
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
  const desktop = useDesktop();
  const [drawer, setDrawer] = useState(false),
    [accountOpen, setAccountOpen] = useState(false),
    [openGroups, setOpenGroups] = useState<string[]>([]),
    // Desktop only: the group whose flyout is open.
    [flyout, setFlyout] = useState<string | null>(null),
    [folded, setFolded] = useState(recallRail);
  const rail = desktop && folded;
  const accountBox = useRef<HTMLDivElement>(null),
    accountToggle = useRef<HTMLButtonElement>(null),
    sidebar = useRef<HTMLElement>(null),
    menuToggle = useRef<HTMLButtonElement>(null),
    // The group of the open flyout: its button comes first, the panel after it.
    flyoutBox = useRef<HTMLDivElement>(null),
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
  // The drawer and the flyouts are two forms of one menu: a width that changes the form puts the other one away.
  useEffect(() => {
    if (desktop) setDrawer(false);
    else setFlyout(null);
  }, [desktop]);
  // The focus goes to the active tile of a flyout, else to its first.
  function focusFlyout() {
    const panel = flyoutBox.current?.querySelector(".nav-flyout");
    (
      panel?.querySelector<HTMLElement>('[aria-current="page"]') ??
      panel?.querySelector<HTMLElement>("button")
    )?.focus();
  }
  // Closing from the keyboard returns the focus to the group button.
  function closeFlyout(back = false) {
    if (back) (flyoutBox.current?.firstElementChild as HTMLElement)?.focus();
    setFlyout(null);
  }
  // The panel is fixed to the screen: it stands level with its group, higher when the screen is too short for it.
  function placeFlyout() {
    const group = flyoutBox.current?.firstElementChild;
    const panel = flyoutBox.current?.querySelector<HTMLElement>(".nav-flyout");
    if (!group || !panel) return;
    const room = window.innerHeight - panel.offsetHeight - FLYOUT_GAP;
    panel.style.top = `${Math.max(FLYOUT_GAP, Math.min(group.getBoundingClientRect().top, room))}px`;
  }
  useLayoutEffect(placeFlyout, [flyout]);
  useEffect(() => {
    if (!flyout) return;
    focusFlyout();
    const nav = sidebar.current?.querySelector("nav");
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeFlyout(true);
    };
    const outside = (event: MouseEvent) => {
      if (!flyoutBox.current?.contains(event.target as Node)) closeFlyout();
    };
    // Tab out of the panel leaves the flyout.
    const focused = (event: FocusEvent) => {
      if (!flyoutBox.current?.contains(event.target as Node)) closeFlyout();
    };
    document.addEventListener("keydown", keys);
    document.addEventListener("mousedown", outside);
    document.addEventListener("focusin", focused);
    // A menu that scrolls or a window that changes moves the group away from the panel.
    nav?.addEventListener("scroll", placeFlyout);
    window.addEventListener("resize", placeFlyout);
    return () => {
      document.removeEventListener("keydown", keys);
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("focusin", focused);
      nav?.removeEventListener("scroll", placeFlyout);
      window.removeEventListener("resize", placeFlyout);
    };
  }, [flyout]);
  function toggleRail() {
    setFlyout(null);
    setFolded(!folded);
    rememberRail(!folded);
  }
  function select(item: NavItem) {
    closeDrawer();
    setAccountOpen(false);
    setFlyout(null);
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
  const badgeOf = (item: NavItem) =>
    badges?.[item.id] ?? (item.id === "notices" ? unreadText : "");
  function entry(item: NavItem) {
    const Icon = iconOf(item.icon),
      badge = badgeOf(item);
    return (
      <button
        key={item.id}
        className={item === active ? "active" : ""}
        title={rail ? item.label : undefined}
        aria-current={item === active ? "page" : undefined}
        onClick={() => select(item)}
      >
        <Icon size={18} />
        <span>{item.label}</span>
        {!!badge && <span className="nav-count">{badge}</span>}
      </button>
    );
  }
  // An entry of a flyout: the same choice as the entry of the menu, and the focus goes back to the group.
  function tile(item: NavItem) {
    const Icon = iconOf(item.icon),
      badge = badgeOf(item);
    return (
      <button
        key={item.id}
        className={item === active ? "active" : ""}
        aria-current={item === active ? "page" : undefined}
        onClick={() => {
          const group = flyoutBox.current?.firstElementChild as HTMLElement;
          select(item);
          group?.focus();
        }}
      >
        <Icon size={26} />
        <span>{item.label}</span>
        {!!badge && <span className="nav-count">{badge}</span>}
      </button>
    );
  }
  function tileKeys(event: ReactKeyboardEvent<HTMLElement>) {
    const tiles = [...event.currentTarget.querySelectorAll("button")];
    const to = stepIndex(
      tiles.indexOf(document.activeElement as HTMLButtonElement),
      tiles.length,
      event.key,
    );
    if (to === null) return;
    event.preventDefault();
    tiles[to].focus();
  }
  function groupEntry(item: NavGroup) {
    const Icon = iconOf(item.icon),
      id = `nav-group-${item.id}`,
      open = desktop ? flyout === item.id : openGroups.includes(item.id),
      holds = item.items.some((child) => child === active),
      // A folded group still tells that something inside it waits; a flyout leaves the group folded.
      waiting =
        !desktop && open
          ? 0
          : item.items.reduce(
              (sum, child) => sum + (badges?.[child.id] ?? 0),
              0,
            );
    return (
      <div
        className="nav-group"
        key={item.id}
        ref={desktop && open ? flyoutBox : undefined}
      >
        <button
          className={holds && (desktop || !open) ? "active" : ""}
          title={rail ? item.label : undefined}
          aria-expanded={open}
          aria-controls={open || !desktop ? id : undefined}
          onClick={() => {
            if (!desktop)
              setOpenGroups(
                open
                  ? openGroups.filter((group) => group !== item.id)
                  : [...openGroups, item.id],
              );
            else if (open) closeFlyout();
            else setFlyout(item.id);
          }}
          onKeyDown={(event) => {
            if (!desktop || event.key !== "ArrowRight") return;
            event.preventDefault();
            if (open) focusFlyout();
            else setFlyout(item.id);
          }}
        >
          <Icon size={18} />
          <span>{item.label}</span>
          {!!waiting && <span className="nav-count">{waiting}</span>}
          <ChevronRight className="nav-arrow" size={14} />
        </button>
        {!desktop ? (
          <div className="nav-group-items" id={id} hidden={!open}>
            {item.items.map(entry)}
          </div>
        ) : (
          open && (
            <div
              className="nav-flyout"
              id={id}
              role="group"
              aria-label={item.label}
              tabIndex={-1}
              onKeyDown={tileKeys}
            >
              <div className="nav-flyout-title" aria-hidden="true">
                {item.label}
              </div>
              <div
                className="nav-flyout-tiles"
                style={
                  {
                    "--tiles": Math.min(item.items.length, FLYOUT_COLUMNS),
                  } as CSSProperties
                }
              >
                {item.items.map(tile)}
              </div>
            </div>
          )
        )}
      </div>
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
    <div className={`app-shell ${rail ? "rail" : ""}`}>
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
              {block.map((item) =>
                isGroup(item) ? groupEntry(item) : entry(item),
              )}
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-foot">
            {year && <div className="school-year">{year} учебный год</div>}
            {desktop && (
              <button
                className="rail-toggle"
                title={rail ? "Развернуть меню" : "Свернуть меню в значки"}
                aria-label={rail ? "Развернуть меню" : "Свернуть меню в значки"}
                onClick={toggleRail}
              >
                {rail ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
              </button>
            )}
          </div>
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
