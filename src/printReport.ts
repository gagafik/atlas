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
  "body{font:14px sans-serif;padding:24px;color:#111}article.final-card{break-after:page;padding:8px 0}article.final-card:last-child{break-after:auto}h2,h3,h4,p{margin:8px 0}.final-subject{display:flex;justify-content:space-between;gap:16px;border-top:1px solid #ccc;padding:6px 0}.final-subject p{margin:0}.final-preview{font-weight:bold}.final-stale{display:block;font-weight:bold}.final-signatures{display:flex;gap:32px;margin-top:48px}.final-signatures span{flex:1;border-top:1px solid #111;padding-top:4px}";

// The evacuation checklist: black on white, one class never split across pages, empty boxes to tick by hand.
export const DRILL_PRINT =
  "body{font:14px sans-serif;padding:24px;color:#000;background:#fff}h2,h3,h4,p{margin:8px 0}h3 small{font-weight:normal}.sf-classes{display:block}.sf-class{break-inside:avoid;border-top:1px solid #000;padding:8px 0}.sf-list{list-style:none;margin:0;padding:0}.sf-check{display:flex;gap:8px;align-items:center;padding:2px 0}.sf-check input{width:14px;height:14px;margin:0}.sf-figures{font-weight:bold}table{border-collapse:collapse;width:100%}th,td{border:1px solid #000;padding:4px 8px;text-align:left}thead{display:table-header-group}tr{break-inside:avoid}button{display:none}";

// The register page of a briefing: the signature column stays empty and is drawn with borders.
export const BRIEFING_PRINT =
  "body{font:14px sans-serif;padding:24px;color:#000;background:#fff}h2,h3,p{margin:8px 0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #000;padding:6px 8px;text-align:left}.sf-sign{display:table-cell;width:30%;height:28px}.sf-printonly{display:block}thead{display:table-header-group}tr{break-inside:avoid}button{display:none}";
