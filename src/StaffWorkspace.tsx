import { useState, type ReactNode } from "react";
import type { AuthStatus } from "./auth";
import ClassStaffWorkspace from "./ClassStaffWorkspace";
import MessagesPanel from "./MessagesPanel";

/** Workplace of a member of staff: the class duties assigned to them and messages with colleagues. */
export default function StaffWorkspace({
  session,
  controls,
}: {
  session: AuthStatus;
  controls: ReactNode;
}) {
  const [section, setSection] = useState("class-staff"),
    spaces = session.user?.workspaces ?? [],
    [workspace, setWorkspace] = useState(spaces[0] ?? "school");
  return (
    <main className="portal">
      <header>
        <div>
          <strong>атлас•</strong>
          <h1>{section === "messages" ? "Сообщения" : "Работа с классами"}</h1>
          <p>Назначенные функции сотрудника</p>
        </div>
        {controls}
      </header>
      <div className="dated-toolbar portal-tabs">
        <button
          aria-pressed={section === "class-staff"}
          onClick={() => setSection("class-staff")}
        >
          Работа с классами
        </button>
        <button
          aria-pressed={section === "messages"}
          onClick={() => setSection("messages")}
        >
          Сообщения
        </button>
      </div>
      {section === "messages" ? (
        <>
          <label className="field">
            <span>Пространство сообщений</span>
            <select
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
            >
              {spaces.map((id) => (
                <option key={id} value={id}>
                  {id === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <MessagesPanel key={workspace} workspace={workspace} />
        </>
      ) : (
        <ClassStaffWorkspace />
      )}
    </main>
  );
}
