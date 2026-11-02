import { isApiUrl } from "./runtime";

export const BLOCKED_LINK_TEXT =
  "В демо файлы и внешние страницы не открываются.";

let toastTimer: ReturnType<typeof setTimeout> | undefined;

export function showDemoToast(text: string): void {
  let toast = document.getElementById("demo-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "demo-toast";
    toast.setAttribute("role", "status");
    toast.style.cssText =
      "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:500;" +
      "max-width:min(92vw,420px);padding:10px 16px;border-radius:10px;background:#1d332a;" +
      "color:#fff;font:14px/1.4 system-ui,sans-serif;box-shadow:0 8px 30px #0003;text-align:center";
    document.body.append(toast);
  }
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    if (toast) toast.hidden = true;
  }, 3500);
}

function leavesDemo(url: URL): boolean {
  if (url.origin !== window.location.origin) return false;
  return (
    isApiUrl(url, window.location.origin) ||
    !url.pathname.startsWith(import.meta.env.BASE_URL)
  );
}

/** Links to server files and to pages outside the demo show a note instead of a 404. */
export function installLinkGuard(): void {
  document.addEventListener(
    "click",
    (event) => {
      const link = (event.target as Element | null)?.closest?.("a[href]");
      if (!link) return;
      let url: URL;
      try {
        url = new URL((link as HTMLAnchorElement).href, window.location.href);
      } catch {
        return;
      }
      if (!leavesDemo(url)) return;
      event.preventDefault();
      event.stopPropagation();
      showDemoToast(BLOCKED_LINK_TEXT);
    },
    true,
  );
  const open = window.open.bind(window);
  window.open = (target?: string | URL, ...rest: unknown[]) => {
    if (target !== undefined && String(target) !== "") {
      try {
        if (leavesDemo(new URL(String(target), window.location.href))) {
          showDemoToast(BLOCKED_LINK_TEXT);
          return null;
        }
      } catch {
        // An unparsable address is left to the browser.
      }
    }
    return open(target, ...(rest as [string?, string?]));
  };
}
