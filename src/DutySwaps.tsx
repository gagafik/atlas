import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { named } from "./utils";
import { requestId, shortDate, type DutyProps } from "./dutySchedule";

export interface Swap {
  id: string;
  version: number;
  status: "pending" | "approved" | "rejected" | "cancelled";
  commitmentId: string;
  date: string;
  name: string;
  start: string;
  end: string;
  place: string;
  fromTeacher: { id: string; name: string };
  toTeacher: { id: string; name: string } | null;
  requester: string;
  reason: string;
  decision: string;
  decidedBy: string;
}
export const SWAP_STATUS: Record<string, string> = {
  pending: "Ждёт решения",
  approved: "Одобрена",
  rejected: "Отклонена",
  cancelled: "Отозвана",
};

export default function DutySwaps({ state: s, workspace, onState }: DutyProps) {
  const [status, setStatus] = useState("pending"),
    [swaps, setSwaps] = useState<Swap[]>([]),
    [picks, setPicks] = useState<Record<string, string>>({}),
    [reasons, setReasons] = useState<Record<string, string>>({}),
    [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<Record<string, string>>({}),
    [notice, setNotice] = useState("");
  const slot = useRef({ key: "", id: "" });
  useEffect(() => {
    let live = true;
    api(`duties/swaps${status ? `?status=${status}` : ""}`, workspace)
      .then((r: Swap[]) => {
        if (!live) return;
        setSwaps(r);
        setPicks((p) =>
          Object.fromEntries(
            r.map((x) => [x.id, p[x.id] ?? x.toTeacher?.id ?? ""]),
          ),
        );
      })
      .catch((e) => {
        if (live) setError({ "": e.message });
      });
    return () => {
      live = false;
    };
  }, [workspace, status, reload, s.revision]);
  function candidates(swap: Swap) {
    const duty = (s.commitments || []).find((c) => c.id === swap.commitmentId);
    if (!duty) return [];
    return (
      duty.eligibleTeacherIds.length
        ? duty.eligibleTeacherIds
        : s.teachers.map((t) => t.id)
    )
      .filter((id) => !duty.teacherIds.includes(id))
      .map((id) => ({ id, name: named(s.teachers, id) }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }
  async function decide(swap: Swap, approve: boolean) {
    if (busy) return;
    setBusy(true);
    setError({});
    setNotice("");
    try {
      const body = {
        revision: s.revision,
        version: swap.version,
        approve,
        toTeacherId: approve ? picks[swap.id] || null : null,
        reason: reasons[swap.id] || "",
      };
      const result = await api(
        `duties/swaps/${swap.id}/decide`,
        workspace,
        "POST",
        {
          requestId: requestId(slot, [swap.id, body]),
          ...body,
        },
      );
      slot.current = { key: "", id: "" };
      setNotice(
        approve ? "Замена одобрена, график обновлён." : "Заявка отклонена.",
      );
      if (result.state && result.state.revision !== s.revision)
        await onState(result.state);
      setReload((n) => n + 1);
    } catch (e: any) {
      // The server's reason (a busy colleague, a locked duty) is shown as it is.
      setError({ [swap.id]: e.message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="duty-swaps">
      <div className="dated-toolbar">
        <label className="field">
          <span>Заявки</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="pending">Ждут решения</option>
            <option value="">Все</option>
            <option value="approved">Одобренные</option>
            <option value="rejected">Отклонённые</option>
            <option value="cancelled">Отозванные</option>
          </select>
        </label>
        <button onClick={() => setReload((n) => n + 1)}>Обновить</button>
      </div>
      <p className="muted">
        Учитель просит передать своё будущее дежурство. Одобрение проверяет
        занятость заменяющего по урокам, отсутствиям и другим дежурствам так же,
        как при ручном назначении. Согласие коллеги школа получает отдельно.
      </p>
      {error[""] && (
        <p className="error-banner" role="alert">
          {error[""]}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!swaps.length && <p className="empty-state">Заявок нет.</p>}
      <div className="commitment-list duty-swap-list">
        {swaps.map((x) => (
          <article key={x.id}>
            <div>
              <strong>
                {shortDate(x.date)} · {x.start}–{x.end} · {x.place}
              </strong>
              <p>
                {x.name}: {x.fromTeacher.name} просит замену
                {x.toTeacher ? `, предлагает: ${x.toTeacher.name}` : ""}.
              </p>
              {x.reason && <p>Причина: {x.reason}</p>}
              <p>
                <span className={`duty-badge ${x.status}`}>
                  {SWAP_STATUS[x.status]}
                </span>
                {x.decision && ` · ${x.decision}`}
                {x.decidedBy && ` · ${x.decidedBy}`}
              </p>
              {x.status === "pending" && (
                <div className="dated-fields">
                  <label className="field">
                    <span>Кто заменит</span>
                    <select
                      value={picks[x.id] || ""}
                      onChange={(e) =>
                        setPicks({ ...picks, [x.id]: e.target.value })
                      }
                    >
                      <option value="">Выберите сотрудника</option>
                      {candidates(x).map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>Комментарий к решению</span>
                    <input
                      maxLength={500}
                      value={reasons[x.id] || ""}
                      onChange={(e) =>
                        setReasons({ ...reasons, [x.id]: e.target.value })
                      }
                    />
                  </label>
                </div>
              )}
              {error[x.id] && (
                <p className="error-banner" role="alert">
                  {error[x.id]}
                </p>
              )}
            </div>
            {x.status === "pending" && (
              <div className="dated-actions">
                <button
                  className="primary"
                  disabled={busy || !picks[x.id]}
                  onClick={() => void decide(x, true)}
                >
                  Одобрить
                </button>
                <button disabled={busy} onClick={() => void decide(x, false)}>
                  Отклонить
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
