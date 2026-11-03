import { useEffect, useState } from "react";
import type { AuthStatus } from "./auth";
import ClassStaffWorkspace from "./ClassStaffWorkspace";
import MessagesPanel from "./MessagesPanel";
import Shell, { type ShellFrame } from "./Shell";
import { eventViews } from "./navigation";
import { useMessageUnread } from "./shellData";
import { canLeaveHomework } from "./homeworkNavigation";
import { focusRequest, spendFocus, type FocusRequest } from "./focusRequest";

/** Workplace of a member of staff: the class duties assigned to them and messages with colleagues. */
export default function StaffWorkspace({
  session,
  frame,
}: {
  session: AuthStatus;
  frame: ShellFrame;
}) {
  const [section, setSection] = useState("class-staff"),
    spaces = session.user?.workspaces ?? [],
    [workspace, setWorkspace] = useState(spaces[0] ?? "school"),
    [messageFocus, setMessageFocus] = useState<FocusRequest | undefined>();
  const unread = useMessageUnread(workspace);
  function go(value: string) {
    if (!canLeaveHomework()) return;
    setSection(value);
    setMessageFocus(undefined);
  }
  // Opened from a notification after the server re-checked access to the record.
  useEffect(() => {
    const sections = eventViews("staff", "staff");
    const open = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      if (!canLeaveHomework()) return;
      if (detail.workspace && spaces.includes(detail.workspace))
        setWorkspace(detail.workspace);
      const messages = event.type === "atlas-open-messages";
      setSection(sections[event.type]);
      setMessageFocus(focusRequest(messages ? detail.threadId : undefined));
    };
    for (const name of Object.keys(sections))
      window.addEventListener(name, open);
    return () => {
      for (const name of Object.keys(sections))
        window.removeEventListener(name, open);
    };
  }, [spaces.join()]);
  return (
    <Shell
      frame={frame}
      journalPanels={null}
      view={section}
      onView={go}
      unread={unread}
    >
      {({ title }) => (
        <main className="portal">
          <div className="page-heading">
            <h1>{title}</h1>
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
      )}
    </Shell>
  );
}
