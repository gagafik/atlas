import { createPortal } from "react-dom";

// The sheet that window.print() shows instead of the panel: a title, a line of context and one plain table.
// It sits in document.body, not in the modal dialog: a dialog is fixed in the top layer, so anything inside it is cut to one page.
export default function MeetingPrint({
  title,
  subtitle,
  head,
  rows,
}: {
  title: string;
  subtitle: string;
  head: string[];
  rows: string[][];
}) {
  return createPortal(
    <section className="meeting-print" aria-label="Печать">
      <h2>{title}</h2>
      <p>{subtitle}</p>
      <table>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>,
    document.body,
  );
}
