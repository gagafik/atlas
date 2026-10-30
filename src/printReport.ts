// Prints a copy of a page element in a window of its own; false when the browser did not allow the window.
export function printNode(
  node: HTMLElement | null,
  title: string,
  css: string,
): boolean {
  const win = window.open("", "_blank");
  if (!win) return false;
  win.opener = null;
  win.document.title = title;
  const copy = node?.cloneNode(true);
  if (copy) win.document.body.appendChild(copy);
  const style = win.document.createElement("style");
  style.textContent = css;
  win.document.head.appendChild(style);
  win.print();
  return true;
}

export const PERSONAL_PRINT =
  "body{font:14px sans-serif;padding:24px;color:#111}article{break-inside:avoid;border-top:1px solid #ccc;padding:8px}h4,p{margin:8px 0}.error{font-weight:bold}";

// One card per page; the signature lines are drawn with borders so they print without any form controls.
export const CLASS_PRINT =
  "body{font:14px sans-serif;padding:24px;color:#111}article.final-card{break-after:page;padding:8px 0}article.final-card:last-child{break-after:auto}h2,h3,h4,p{margin:8px 0}.final-subject{display:flex;justify-content:space-between;gap:16px;border-top:1px solid #ccc;padding:6px 0}.final-subject p{margin:0}.final-preview{font-weight:bold}.final-signatures{display:flex;gap:32px;margin-top:48px}.final-signatures span{flex:1;border-top:1px solid #111;padding-top:4px}";
