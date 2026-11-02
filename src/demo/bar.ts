export const ROLES = ["admin", "teacher", "parent", "student"] as const;
export type DemoRole = (typeof ROLES)[number];

const LABELS: Record<DemoRole, string> = {
  admin: "Администратор",
  teacher: "Учитель",
  parent: "Родитель",
  student: "Ученик",
};

const STYLE = `
.demo-bar{position:fixed;top:0;left:0;right:0;z-index:400;box-sizing:border-box;display:flex;flex-wrap:wrap;
  align-items:center;gap:2px 10px;padding:5px 12px;background:#1d332a;color:#f2f7f3;
  font:12px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif}
.demo-bar-text{flex:1 1 auto}
.demo-bar nav{display:flex;flex-wrap:wrap;align-items:center;gap:2px 4px}
.demo-bar a{color:#d3ecdd;padding:3px 8px;border-radius:6px;text-decoration:underline;white-space:nowrap}
.demo-bar a[aria-current="page"]{background:#f2f7f3;color:#1d332a;text-decoration:none;font-weight:600}
body{padding-top:var(--demo-bar,0px)}
.sidebar,.modal-backdrop{top:var(--demo-bar,0px)!important}
.modal{max-height:calc(100dvh - var(--demo-bar,0px) - 50px)}
`;

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
  measure();
  if (typeof ResizeObserver !== "undefined")
    new ResizeObserver(measure).observe(bar);
  else window.addEventListener("resize", measure);
}
