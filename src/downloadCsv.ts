import { csvCell } from "./utils";

// Saves a table as a CSV file (semicolon-separated, with a byte order mark so a spreadsheet reads Russian text).
export function downloadCsv(rows: unknown[][], filename: string) {
  const blob = new Blob(
    [
      "\uFEFF" +
        rows
          .map((r) => r.map((v) => csvCell(String(v ?? ""))).join(";"))
          .join("\r\n"),
    ],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
