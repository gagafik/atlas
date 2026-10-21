import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { classCapabilities, type ClassCapability } from "./classStaff";
import AbsencePanel from "./AbsencePanel";
import AttendancePanel from "./AttendancePanel";
import JournalCoveragePanel from "./JournalCoveragePanel";
import { canLeaveHomework } from "./homeworkNavigation";
import "./classStaff.css";

type Space = {
  id: string;
  name: string;
  classes: { id: string; name: string; capabilities: ClassCapability[] }[];
};
export default function ClassStaffWorkspace({
  controls,
}: {
  controls?: ReactNode;
}) {
  const [spaces, setSpaces] = useState<Space[]>([]),
    [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(true),
    [error, setError] = useState("");
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    let alive = true;
    setBusy(true);
    setError("");
    api("journal/class-staff/options", "school")
      .then((value) => {
        if (alive) {
          setSpaces(value.workspaces);
          setWorkspace((old) =>
            value.workspaces.some((s: Space) => s.id === old)
              ? old
              : value.workspaces[0]?.id || "",
          );
        }
      })
      .catch((e) => {
        if (alive) {
          setError(e.message);
          setSpaces([]);
        }
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [generation]);
  const current = spaces.find((s) => s.id === workspace);
  const has = (cap: ClassCapability) =>
    current?.classes.some((c) => c.capabilities.includes(cap));
  const content = (
    <>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      <button
        disabled={busy}
        onClick={() => {
          if (canLeaveHomework()) setGeneration((n) => n + 1);
        }}
      >
        Обновить доступ к классам
      </button>
      {busy ? (
        <p>Проверяю назначения…</p>
      ) : !current ? (
        <p>
          Сейчас вам не назначены полномочия по классам. Обратитесь к
          администратору школы.
        </p>
      ) : (
        <>
          <label className="field">
            <span>Школа для работы с классами</span>
            <select
              value={workspace}
              onChange={(e) => {
                if (canLeaveHomework()) setWorkspace(e.target.value);
              }}
            >
              {spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <details>
            <summary>Мои полномочия по классам</summary>
            {current.classes.map((c) => (
              <p key={c.id}>
                <strong>{c.name}</strong>:{" "}
                {c.capabilities.map((cap) => classCapabilities[cap]).join(", ")}
              </p>
            ))}
          </details>
          <section key={workspace + ":" + generation}>
            {has("absence_review") && <AbsencePanel workspace={workspace} />}
            {has("attendance_read") && (
              <AttendancePanel workspace={workspace} />
            )}
            {has("journal_coverage") && (
              <JournalCoveragePanel workspace={workspace} />
            )}
            {has("behaviour_notes") && (
              <button
                onClick={() =>
                  window.dispatchEvent(
                    new CustomEvent("atlas-open-behaviour", {
                      detail: { workspace },
                    }),
                  )
                }
              >
                Похвалы и замечания классу
              </button>
            )}
          </section>
        </>
      )}
    </>
  );
  return controls ? (
    <main className="portal class-staff-workspace">
      <header>
        <div>
          <strong>атлас•</strong>
          <h1>Работа с классами</h1>
          <p>Назначенные функции сотрудника</p>
        </div>
        {controls}
      </header>
      {content}
    </main>
  ) : (
    <section className="class-staff-workspace">{content}</section>
  );
}
