import { useEffect, useState } from "react";
import { Megaphone } from "lucide-react";
import { api } from "./api";

interface Status {
  revision: number;
  announcedRevision: number | null;
  announcedAt: string | null;
  pending: boolean;
}
const path = "notifications/timetable/announce";
// Saving the timetable never notifies pupils and parents; this button tells the families whose timetable changed since the last announcement.
export default function TimetableAnnounce({
  workspace,
  revision,
  notify,
  fail,
}: {
  workspace: string;
  revision: number;
  notify: (text: string) => void;
  fail: (text: string) => void;
}) {
  const [status, setStatus] = useState<Status | null>(null),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    api(path, workspace)
      .then((r: Status) => {
        if (live) setStatus(r);
      })
      .catch(() => {
        // Only an administrator or an editor of this workspace may announce.
        if (live) setStatus(null);
      });
    return () => {
      live = false;
    };
  }, [workspace, revision]);
  async function announce() {
    if (
      !window.confirm(
        "Семьи получат уведомление, только если расписание их детей изменилось после прошлого сообщения. Продолжить?",
      )
    )
      return;
    setBusy(true);
    try {
      const sent = await api(path, workspace, "POST", { revision });
      notify(`Уведомлено аккаунтов: ${sent.notified}`);
      setStatus(await api(path, workspace));
    } catch (e: any) {
      fail(e.message);
    } finally {
      setBusy(false);
    }
  }
  if (!status) return null;
  const title = "Сообщить семьям об изменениях расписания";
  return (
    <button
      className="icon-button announce-button"
      title={title}
      aria-label={title}
      disabled={busy}
      onClick={() => void announce()}
    >
      <Megaphone size={17} />
      {status.pending && <i className="announce-dot" aria-hidden="true" />}
    </button>
  );
}
