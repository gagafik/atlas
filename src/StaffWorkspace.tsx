import { useEffect, useState, type ReactNode } from "react";
import type { AuthStatus } from "./auth";
import ClassStaffWorkspace from "./ClassStaffWorkspace";
import MessagesPanel from "./MessagesPanel";
import { canLeaveHomework } from "./homeworkNavigation";
import { focusRequest, spendFocus, type FocusRequest } from "./focusRequest";

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
    [workspace, setWorkspace] = useState(spaces[0] ?? "school"),
    [messageFocus, setMessageFocus] = useState<FocusRequest | undefined>();
  function go(value: string) {
    setSection(value);
    setMessageFocus(undefined);
  }
  // Opened from a notification after the server re-checked access to the record.
  useEffect(() => {
    const open = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      if (!canLeaveHomework()) return;
      if (detail.workspace && spaces.includes(detail.workspace))
        setWorkspace(detail.workspace);
      const messages = event.type === "atlas-open-messages";
      setSection(messages ? "messages" : "class-staff");
      setMessageFocus(focusRequest(messages ? detail.threadId : undefined));
    };
    for (const name of ["atlas-open-messages", "atlas-open-absences"])
      window.addEventListener(name, open);
    return () => {
      for (const name of ["atlas-open-messages", "atlas-open-absences"])
        window.removeEventListener(name, open);
    };
  }, [spaces.join()]);
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
          onClick={() => go("class-staff")}
        >
          Работа с классами
        </button>
        <button
          aria-pressed={section === "messages"}
          onClick={() => go("messages")}
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
          <MessagesPanel
            key={workspace}
            workspace={workspace}
            focusThread={messageFocus}
            onFocusUsed={(r) =>
              setMessageFocus((current) => spendFocus(current, r))
            }
          />
        </>
      ) : (
        <ClassStaffWorkspace />
      )}
    </main>
  );
}
