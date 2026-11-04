// What the Pages demo serves is the committed snapshot with one rule applied when it is built: a part of the journal that
// `journal/options` offers but the snapshot has no answer for is not offered, so neither the menu nor the launcher leads a
// visitor to a screen of misses. The files in tools/demo/snapshot stay as they were captured (the administrator's file is at the
// size limit of the repository, so the absence notes of the school are not in it).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The route each part of the journal opens with.
export const PART_ROUTES = {
  absences: "/api/journal/absences",
  coverage: "/api/journal/coverage",
  attendance: "/api/journal/attendance",
  attendanceGrid: "/api/journal/attendance",
  finalGrades: "/api/final-grades",
  periods: "/api/journal/periods",
  courseTable: "/api/journal/course-table",
};
const OPTIONS = "GET /api/journal/options";

/** The snapshot as the demo serves it: the same object when every offered part has answers, else a copy without the others. */
export function servedSnapshot(snapshot) {
  const options = snapshot.entries[OPTIONS];
  const panels = options?.b?.panels;
  if (!panels) return snapshot;
  const routes = Object.keys(snapshot.entries).map((key) =>
    key.slice(key.indexOf(" ") + 1),
  );
  const unanswered = Object.entries(PART_ROUTES).filter(
    ([part, route]) =>
      panels[part] && !routes.some((path) => path.startsWith(route)),
  );
  if (!unanswered.length) return snapshot;
  const kept = { ...panels };
  for (const [part] of unanswered) kept[part] = false;
  return {
    ...snapshot,
    entries: {
      ...snapshot.entries,
      [OPTIONS]: { ...options, b: { ...options.b, panels: kept } },
    },
  };
}

/** The text of a snapshot file as served: the file itself unless a part of the journal is withdrawn. */
export function servedText(text) {
  const snapshot = JSON.parse(text);
  const served = servedSnapshot(snapshot);
  return served === snapshot ? text : JSON.stringify(served);
}

// `node tools/demo/served.mjs <role>` prints the served snapshot of a role.
const ROLES = ["admin", "teacher", "parent", "student"];
if (
  resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url) &&
  ROLES.includes(process.argv[2])
) {
  const file = new URL(`./snapshot/${process.argv[2]}.json`, import.meta.url);
  process.stdout.write(servedText(readFileSync(file, "utf8")));
}
