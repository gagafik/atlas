// The sheet that window.print() shows instead of the panel: a title, a line of context and one plain table.
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
  return (
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
    </section>
  );
}
