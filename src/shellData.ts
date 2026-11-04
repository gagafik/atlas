// What the shell reads for every role: the unread counter and the journal parts on offer.
import { useEffect, useState } from "react";
import { api } from "./api";
import { journalPanels, type JournalPanelName } from "./journal";
import { startPolling } from "./polling";

export type Unread = { count: number; more: boolean };

// The counter on the messages entry: one cheap read a minute, and again whenever something was read. Without messaging access it stays zero.
export function useMessageUnread(workspace: string): Unread {
  const [unread, setUnread] = useState<Unread>({ count: 0, more: false });
  useEffect(() => {
    if (!workspace) return;
    let live = true;
    const poll = startPolling(
      async () => {
        try {
          const r = await api("messages/unread", workspace);
          if (live) setUnread({ count: r.count, more: r.more });
        } catch (e: any) {
          if (e.status !== 403) return false;
          if (live) setUnread({ count: 0, more: false });
        }
      },
      { interval: 60000 },
    );
    const refresh = () => poll.refresh();
    window.addEventListener("atlas-messages-read", refresh);
    return () => {
      live = false;
      poll.stop();
      window.removeEventListener("atlas-messages-read", refresh);
    };
  }, [workspace]);
  return unread;
}

// The parts of the journal the server has anything for. null while loading or where the account has no journal, [] when the request fails.
export function useJournalPanels(
  workspace: string,
  allowed: boolean,
): JournalPanelName[] | null {
  const [panels, setPanels] = useState<JournalPanelName[] | null>(null);
  useEffect(() => {
    setPanels(null);
    if (!allowed || !workspace) return;
    let live = true;
    api("journal/options", workspace)
      .then((r) => live && setPanels(journalPanels(r)))
      .catch(() => live && setPanels([]));
    return () => {
      live = false;
    };
  }, [workspace, allowed]);
  return panels;
}
