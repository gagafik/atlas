export const ROLES = ["admin", "teacher", "parent", "student"] as const;
export type DemoRole = (typeof ROLES)[number];

const LABELS: Record<DemoRole, string> = {
  admin: "Администратор",
  teacher: "Учитель",
  parent: "Родитель",
  student: "Ученик",
};

const STYLE = `
/* White with a dark-blue line: it reads as a note above the app, not as a second top bar (the tokens come from styles.css). */
.demo-bar{position:fixed;top:0;left:0;right:0;z-index:400;box-sizing:border-box;display:flex;flex-wrap:wrap;
  align-items:center;gap:2px 10px;padding:5px 12px;background:var(--surface,#fefefe);color:var(--ink,#231f20);
  border-bottom:2px solid var(--brand-navy,#083a81);
  font:12px/1.35 var(--font,"Segoe UI",system-ui,sans-serif)}
.demo-bar-text{flex:1 1 auto}
.demo-bar nav{display:flex;flex-wrap:wrap;align-items:center;gap:2px 4px}
.demo-bar a{color:var(--accent-strong,#1a56ae);padding:3px 8px;border-radius:2px;text-decoration:underline;white-space:nowrap}
.demo-bar a[aria-current="page"]{background:var(--brand-navy,#083a81);color:var(--brand-white,#fefefe);text-decoration:none;font-weight:600}
body{padding-top:var(--demo-bar,0px)}
.sidebar,.sidebar-backdrop,.modal-backdrop{top:var(--demo-bar,0px)!important}
.modal{max-height:calc(100dvh - var(--demo-bar,0px) - 50px)}
/* Dialogs live in the top layer and would cover the bar: start them below it. */
dialog:modal{margin-top:calc(var(--demo-bar,0px) + 8px)!important;max-height:calc(100dvh - var(--demo-bar,0px) - 16px)!important}
[data-demo-hidden]{display:none!important}
`;

/**
 * Controls that cannot work on a static page (installing the app, invitations,
 * passwords, recovery codes, public links, hall screens, server state, the
 * switch to the built-in example school) are hidden: a viewer should not meet a
 * button that only ends in an error. They sit in the menu and the account menu.
 */
const UNAVAILABLE =
  /^(Приложение|Принять приглашение|Резервные коды|Пароль|Публикация|Информационные экраны|Состояние сервера|Попробовать на примере|Вернуться в свою школу|Новый учебный год)$/;

function tidy(): void {
  for (const element of document.querySelectorAll<HTMLElement>(
    ".sidebar nav button, .account-menu button, .start-tiles button",
  )) {
    const text = (element.textContent ?? "").replace(/\s+/g, " ").trim();
    if (UNAVAILABLE.test(text) && !element.hasAttribute("data-demo-hidden"))
      element.setAttribute("data-demo-hidden", "");
  }
  // The built-in example school is not part of the demo.
  for (const option of document.querySelectorAll<HTMLOptionElement>(
    "select option",
  ))
    if (option.textContent?.trim() === "Демонстрация" && !option.hidden) {
      option.hidden = true;
      option.disabled = true;
    }
}

export function mountBar(role: DemoRole): void {
  const style = document.createElement("style");
  style.textContent = STYLE;
  document.head.append(style);
  const bar = document.createElement("div");
  bar.className = "demo-bar";
  const text = document.createElement("span");
  text.className = "demo-bar-text";
  text.textContent =
    "Демо на вымышленных данных · изменения не сохраняются · Смотреть как:";
  const nav = document.createElement("nav");
  nav.setAttribute("aria-label", "Роль в демо");
  for (const candidate of ROLES) {
    const link = document.createElement("a");
    link.href = `${import.meta.env.BASE_URL}?as=${candidate}`;
    link.textContent = LABELS[candidate];
    if (candidate === role) link.setAttribute("aria-current", "page");
    nav.append(link);
  }
  bar.append(text, nav);
  document.body.prepend(bar);
  const measure = () =>
    document.documentElement.style.setProperty(
      "--demo-bar",
      `${bar.offsetHeight}px`,
    );
  let scheduled = false;
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
      scheduled = false;
      tidy();
    });
  };
  new MutationObserver(schedule).observe(document.body, {
    childList: true,
    subtree: true,
  });
  schedule();
  measure();
  if (typeof ResizeObserver !== "undefined")
    new ResizeObserver(measure).observe(bar);
  else window.addEventListener("resize", measure);
}
