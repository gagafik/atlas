import { useEffect, useState } from "react";
import { api } from "./api";
import { displayTime } from "./meetings";
import {
  auditResults,
  type Audit,
  type AuditDetail,
  type AuditItem,
} from "./libraryTypes";

export default function LibraryInventory({
  workspace,
  run,
  mutate,
  history,
}: {
  workspace: string;
  run: (f: () => Promise<void>) => Promise<void>;
  mutate: (path: string, body: object, method?: string) => Promise<any>;
  history: (entity: string, id: string) => void;
}) {
  const [audits, setAudits] = useState<Audit[]>([]),
    [next, setNext] = useState<number | null>(null),
    [detail, setDetail] = useState<AuditDetail | null>(null),
    [filter, setFilter] = useState("");
  async function load(more = false) {
    const r = await api(
      "library/audits" + (more && next ? "?before=" + next : ""),
      workspace,
    );
    setAudits(more ? [...audits, ...r.items] : r.items);
    setNext(r.nextBefore);
  }
  async function open(id: string, more = false, value = filter) {
    const r: AuditDetail = await api(
      `library/audits/${id}?result=${value}${more && detail?.nextBefore ? "&before=" + detail.nextBefore : ""}`,
      workspace,
    );
    setDetail({ ...r, items: more ? [...detail!.items, ...r.items] : r.items });
  }
  useEffect(() => {
    void run(() => load());
  }, [workspace]);
  async function mark(item: AuditItem, result: string) {
    const note = ["missing", "refresh"].includes(result)
      ? window.prompt(
          result === "refresh"
            ? "Почему нужно сверить новое состояние?"
            : "Где проверили и что не найдено?",
        )
      : "";
    if (note === null) return;
    await run(async () => {
      await mutate(`audits/${detail!.audit.id}/copies/${item.copyId}`, {
        version: item.version,
        result,
        note,
      });
      await open(detail!.audit.id);
      await load();
    });
  }
  return (
    <section className="library-inventory">
      <div className="library-actions">
        <h3>Инвентаризация</h3>
        <button
          onClick={() => {
            const title = window.prompt("Название сверки");
            if (title)
              void run(async () => {
                const a = await mutate("audits", { title });
                await load();
                await open(a.id);
              });
          }}
        >
          Начать сверку
        </button>
        <button onClick={() => void run(() => load())}>Обновить сверки</button>
      </div>
      <p>
        Сверка фиксирует экземпляры, находившиеся в фонде на момент начала.
        Новые поступления войдут в следующую сверку. Отметка «не найден» не
        списывает книгу и не меняет задолженность.
      </p>
      <div className="library-grid">
        <div className="library-list">
          {audits.map((a) => (
            <button key={a.id} onClick={() => void run(() => open(a.id))}>
              <strong>{a.title}</strong>
              <span>
                {a.status === "open"
                  ? "Открыта"
                  : a.status === "closed"
                    ? "Завершена"
                    : "Отменена"}{" "}
                · {displayTime(a.created)}
              </span>
            </button>
          ))}
          {next && (
            <button onClick={() => void run(() => load(true))}>
              Ещё сверки
            </button>
          )}
        </div>
        {detail && (
          <div>
            <h3>{detail.audit.title}</h3>
            <p>
              {Object.entries(detail.audit.counts)
                .map(([k, n]) => `${auditResults[k]}: ${n}`)
                .join(" · ")}
            </p>
            {detail.audit.changed > 0 && (
              <p role="alert">
                После проверки изменились экземпляры: {detail.audit.changed}.
                Сверьте их повторно.
              </p>
            )}
            <label>
              Результат
              <select
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  void run(() => open(detail.audit.id, false, e.target.value));
                }}
              >
                <option value="">Все экземпляры</option>
                {Object.entries(auditResults).map(([v, n]) => (
                  <option key={v} value={v}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            {detail.items.map((i) => (
              <article className="library-card" key={i.copyId}>
                <strong>
                  {i.copy.inventory} · {i.copy.title.title}
                </strong>
                <p>
                  {auditResults[i.result]} ·{" "}
                  {i.copy.location || "Место хранения не задано"}
                  {i.changed ? " · Состояние изменилось" : ""}
                </p>
                {i.note && <p>{i.note}</p>}
                {detail.audit.status === "open" && (
                  <div className="library-actions">
                    {i.changed ? (
                      <button onClick={() => void mark(i, "refresh")}>
                        Сверить новое состояние
                      </button>
                    ) : i.copy.onLoan ? (
                      <span>
                        Выдача не закрыта — физическое наличие не
                        подтверждается.
                      </span>
                    ) : (
                      <>
                        <button onClick={() => void mark(i, "seen")}>
                          На месте
                        </button>
                        <button onClick={() => void mark(i, "missing")}>
                          Не найден
                        </button>
                      </>
                    )}
                  </div>
                )}
              </article>
            ))}
            {detail.nextBefore && (
              <button
                onClick={() => void run(() => open(detail.audit.id, true))}
              >
                Ещё экземпляры
              </button>
            )}
            <div className="library-actions">
              <button onClick={() => history("audit", detail.audit.id)}>
                История сверки
              </button>
              {detail.audit.status === "open" &&
                ["close", "cancel"].map((action) => (
                  <button
                    key={action}
                    onClick={() => {
                      const reason = window.prompt(
                        action === "close"
                          ? "Итог и основание завершения"
                          : "Причина отмены",
                      );
                      if (reason)
                        void run(async () => {
                          await mutate(`audits/${detail.audit.id}/action`, {
                            version: detail.audit.version,
                            action,
                            reason,
                          });
                          await open(detail.audit.id);
                          await load();
                        });
                    }}
                  >
                    {action === "close"
                      ? "Завершить сверку"
                      : "Отменить сверку"}
                  </button>
                ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
