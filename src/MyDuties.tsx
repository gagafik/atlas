import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { addDays, requestId, shortDate } from "./dutySchedule";
import { SWAP_STATUS, type Swap } from "./DutySwaps";
import "./DutySchedule.css";

interface Item {
  id: string;
  date: string;
  weekday: string;
  start: string;
  end: string;
  name: string;
  place: string;
  teacherId: string;
  colleagues: string[];
  required: number;
  assigned: number;
  closed: string;
  swap: "pending" | "approved" | "rejected" | null;
  canSwap: boolean;
}
interface Mine {
  today: string;
  start: string;
  end: string;
  teachers: { id: string; name: string }[];
  items: Item[];
}
interface Options {
  today: string;
  teachers: { id: string; name: string }[];
  colleagues: { id: string; name: string }[];
}
const RANGES: [string, string, number][] = [
  ["two", "Две недели", 13],
  ["month", "Месяц", 29],
  ["year", "До конца учебного года", 365],
];

export default function MyDuties({ workspace }: { workspace: string }) {
  const [options, setOptions] = useState<Options | null>(null),
    [range, setRange] = useState("two"),
    [data, setData] = useState<Mine | null>(null),
    [pending, setPending] = useState<Swap[]>([]),
    [reload, setReload] = useState(0);
  const [asking, setAsking] = useState<{
      id: string;
      teacherId: string;
      to: string;
      reason: string;
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const slot = useRef({ key: "", id: "" });
  useEffect(() => {
    let live = true;
    api("duties/options", workspace)
      .then((r) => {
        if (live) setOptions(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  useEffect(() => {
    if (!options) return;
    let live = true;
    const days = RANGES.find((r) => r[0] === range)![2];
    Promise.all([
      api(
        `duties/mine?start=${options.today}&end=${addDays(options.today, days)}`,
        workspace,
      ),
      api("duties/swaps?status=pending", workspace),
    ])
      .then(([mine, swaps]) => {
        if (!live) return;
        setData(mine);
        setPending(swaps);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, options, range, reload]);
  async function ask() {
    if (!asking || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const body = {
        commitmentId: asking.id,
        fromTeacherId: asking.teacherId,
        toTeacherId: asking.to || null,
        reason: asking.reason,
      };
      await api("duties/swaps", workspace, "POST", {
        requestId: requestId(slot, body),
        ...body,
      });
      slot.current = { key: "", id: "" };
      setAsking(null);
      setNotice("Заявка отправлена администрации расписания.");
      setReload((n) => n + 1);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function withdraw(swap: Swap) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`duties/swaps/${swap.id}/cancel`, workspace, "POST", {
        version: swap.version,
      });
      setNotice("Заявка отозвана.");
      setReload((n) => n + 1);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const groups = new Map<string, Item[]>();
  for (const item of data?.items || [])
    groups.set(item.date, [...(groups.get(item.date) || []), item]);
  return (
    <section className="my-duties">
      <div className="dated-toolbar duty-controls">
        <label className="field">
          <span>Период</span>
          <select value={range} onChange={(e) => setRange(e.target.value)}>
            {RANGES.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button disabled={!data?.items.length} onClick={() => window.print()}>
          Печать
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {data && (
        <div className="duty-print my-duties-list">
          <h3 className="duty-print-title">
            Мои дежурства · {shortDate(data.start)}–{shortDate(data.end)}
            {data.teachers.length > 1 &&
              ` · ${data.teachers.map((t) => t.name).join(", ")}`}
          </h3>
          {!data.items.length && (
            <p className="empty-state">Ближайших дежурств нет.</p>
          )}
          {[...groups.entries()].map(([date, items]) => (
            <section key={date} className="duty-day">
              <h4>
                {items[0].weekday ? `${items[0].weekday}, ` : ""}
                {shortDate(date)}
              </h4>
              {items.map((i) => {
                const request = pending.find(
                  (p) =>
                    p.commitmentId === i.id && p.fromTeacher.id === i.teacherId,
                );
                return (
                  <article key={i.id + i.teacherId} className="duty-card">
                    <div>
                      <strong>
                        {i.start}–{i.end} · {i.place}
                      </strong>
                      <p>
                        {i.name}
                        {data.teachers.length > 1 &&
                          ` · ${data.teachers.find((t) => t.id === i.teacherId)?.name}`}
                      </p>
                      <p>
                        {i.colleagues.length
                          ? `Вместе с: ${i.colleagues.join(", ")}`
                          : "Дежурите один"}
                        {i.assigned < i.required &&
                          ` · не хватает дежурных: ${i.required - i.assigned}`}
                      </p>
                      <p>
                        {i.closed && (
                          <span className="duty-badge closed">
                            Нет занятий по календарю: {i.closed}
                          </span>
                        )}
                        {i.swap && (
                          <span className={`duty-badge ${i.swap}`}>
                            Замена: {SWAP_STATUS[i.swap].toLowerCase()}
                          </span>
                        )}
                      </p>
                      {asking?.id === i.id && (
                        <div className="dated-fields duty-noprint">
                          <label className="field">
                            <span>Кого предлагаете (необязательно)</span>
                            <select
                              value={asking.to}
                              onChange={(e) =>
                                setAsking({ ...asking, to: e.target.value })
                              }
                            >
                              <option value="">
                                Пусть решит администрация
                              </option>
                              {options?.colleagues.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.name}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="field">
                            <span>Причина</span>
                            <input
                              maxLength={500}
                              value={asking.reason}
                              onChange={(e) =>
                                setAsking({ ...asking, reason: e.target.value })
                              }
                            />
                          </label>
                          <div className="modal-actions">
                            <button
                              disabled={busy}
                              onClick={() => setAsking(null)}
                            >
                              Отмена
                            </button>
                            <button
                              className="primary"
                              disabled={busy}
                              onClick={() => void ask()}
                            >
                              Отправить заявку
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="dated-actions duty-noprint">
                      {i.canSwap && !i.swap && asking?.id !== i.id && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            setAsking({
                              id: i.id,
                              teacherId: i.teacherId,
                              to: "",
                              reason: "",
                            })
                          }
                        >
                          Попросить замену
                        </button>
                      )}
                      {request && (
                        <button
                          disabled={busy}
                          onClick={() => void withdraw(request)}
                        >
                          Отозвать заявку
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </section>
          ))}
          <p className="muted duty-noprint">
            Замену утверждает администрация расписания после проверки занятости
            коллеги. Согласуйте её с коллегой заранее.
          </p>
        </div>
      )}
    </section>
  );
}
