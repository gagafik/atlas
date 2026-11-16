import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useAssessmentCategories } from "./assessmentCategories";
import FamilyFinals from "./FamilyFinals";
import FinalGradesHome from "./FinalGradesHome";
import type { FinalsShared, Options } from "./finalGradesTypes";
import TeacherFinals from "./TeacherFinals";
import { useUnsavedGuard } from "./useUnsavedGuard";

// «Табели»: loads what the person may see and shows the screen of the role. The screens do the work.
export default function FinalGradesPanel({
  workspace,
  focus,
  studentId,
  year,
}: {
  workspace: string;
  // A past school year (archived only): its periods and books are listed. The current year sends nothing.
  year?: string;
  // Set by an opened notification: show the report card of this pupil for this period.
  focus?: { studentId: string; finalsPeriodId?: string };
  // The pupil of the report card chooser, when the page already knows the child.
  studentId?: string;
}) {
  const [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    // Raised after every change, so the tables never show numbers from before it.
    [epoch, setEpoch] = useState(0),
    [categoryEpoch, setCategoryEpoch] = useState(0);
  const categories = useAssessmentCategories(workspace, categoryEpoch);
  const guard = useUnsavedGuard();
  const lock = useRef(false),
    lastScale = useRef("");

  async function run(work: () => Promise<void>) {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      return true;
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие.",
      );
      return false;
    } finally {
      setBusy(false);
      lock.current = false;
    }
  }
  async function loadOptions() {
    const r: Options = await api(
      `final-grades/options${year ? `?year=${year}` : ""}`,
      workspace,
    );
    setOptions(r);
    return r;
  }
  // Reading the lists again outside the busy lock: a failed read is shown and leaves what is on screen as it was.
  async function reloadOptions() {
    try {
      return await loadOptions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось обновить итоги.");
      return undefined;
    }
  }
  const changed = () => setEpoch((n) => n + 1);
  useEffect(() => {
    setOptions(null);
    void run(async () => void (await loadOptions()));
  }, [workspace, year]);

  const shared: FinalsShared | null = options && {
    workspace,
    year,
    options,
    categories,
    onCategoriesSaved: () => setCategoryEpoch((n) => n + 1),
    lastScale,
    epoch,
    changed,
    leave: guard.leave,
    dirty: guard.dirty,
    onDirty: guard.markDirty,
    reloadOptions,
    refresh: () => {
      if (guard.leave())
        void run(async () => {
          await loadOptions();
          changed();
        });
    },
    busy,
    run,
    setNotice,
  };
  return (
    <section className="absence-panel final-grades-panel">
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      {shared &&
        (shared.options.admin ? (
          <FinalGradesHome {...shared} focus={focus} />
        ) : shared.options.staff ? (
          <TeacherFinals {...shared} focus={focus} />
        ) : (
          <FamilyFinals
            workspace={workspace}
            options={shared.options}
            studentId={studentId}
            focus={focus}
          />
        ))}
    </section>
  );
}
