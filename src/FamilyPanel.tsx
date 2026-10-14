import { useEffect, useState } from "react";
import { api } from "./api";
import type { State } from "./types";
import type { Account } from "./auth";

const relationships: Record<string, string> = {
  unspecified: "Не уточнено",
  mother: "Мать",
  father: "Отец",
  guardian: "Опекун",
  representative: "Другой представитель",
};
const activities: Record<string, string> = {
  active: "Доступ действует",
  scheduled: "Доступ ещё не начался",
  expired: "Срок доступа истёк",
  revoked: "Доступ отозван",
};
type FamilyLink = {
  parentId: string;
  studentId: string;
  studentName: string;
  parentName: string;
  version: number;
  accountVersion: number;
  relationship: string;
  verified: boolean;
  evidence?: string;
  reason?: string;
  startDate: string | null;
  endDate: string | null;
  status: string;
  activity: string;
  legacy: boolean;
  accountActive: boolean;
  workspace?: string;
};
type History = {
  id: number;
  created: string;
  actor: string;
  source: string;
  version: number;
  document: FamilyLink;
};
const keyOf = (r: FamilyLink) => `${r.parentId}:${r.studentId}`;

function LinkSummary({ item }: { item: FamilyLink }) {
  return (
    <>
      <strong>{item.studentName}</strong>
      <p>
        {relationships[item.relationship]} ·{" "}
        {item.verified
          ? "Сведения проверены школой"
          : "Полномочия требуют уточнения"}
      </p>
      <p>
        {activities[item.activity]}
        {!item.accountActive ? " · аккаунт отключён или сменил роль" : ""}
      </p>
      <p>
        {item.startDate ? `С ${item.startDate}` : "Без даты начала"} ·{" "}
        {item.endDate
          ? `по ${item.endDate} включительно`
          : "Без даты окончания"}
      </p>
    </>
  );
}

export function FamilyPortal() {
  const [items, setItems] = useState<FamilyLink[]>([]),
    [next, setNext] = useState<number | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async (offset = 0) => {
    setBusy(true);
    setError("");
    try {
      const r = await api(`portal/family?offset=${offset}`, "school");
      setItems((old) => (offset ? [...old, ...r.items] : r.items));
      setNext(r.nextOffset);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let live = true;
    setBusy(true);
    api("portal/family", "school")
      .then((r) => {
        if (live) {
          setItems(r.items);
          setNext(r.nextOffset);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, []);
  return (
    <section className="family-panel">
      <h2>Мои дети и доступ</h2>
      <p>
        Здесь показаны только ваши связи с детьми. Сведения других
        представителей и внутренние основания проверки доступны школе. Для
        добавления ребёнка примите школьное приглашение или обратитесь к
        администратору.
      </p>
      <p>
        Сроки считаются по времени школы: Asia/Qyzylorda. Окончание действует до
        конца указанного дня.
      </p>
      <button disabled={busy} onClick={() => void load()}>
        Обновить доступ к детям
      </button>
      {error && <p role="alert">{error}</p>}
      {!busy && !items.length && <p>Школа пока не назначила вам детей.</p>}
      {items.map((item) => (
        <article
          className="family-card"
          key={`${item.workspace}:${keyOf(item)}`}
        >
          <LinkSummary item={item} />
          <small>
            {item.workspace === "demo"
              ? "Демонстрационное пространство"
              : "Школа"}
          </small>
        </article>
      ))}
      {next !== null && (
        <button disabled={busy} onClick={() => void load(next)}>
          Предыдущие связи с детьми
        </button>
      )}
    </section>
  );
}

export default function FamilyPanel({
  workspace,
  state,
  users,
  onChanged,
}: {
  workspace: string;
  state: State;
  users: Account[];
  onChanged: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false),
    [query, setQuery] = useState(""),
    [items, setItems] = useState<FamilyLink[]>([]),
    [next, setNext] = useState<number | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [parentSearch, setParentSearch] = useState(""),
    [studentSearch, setStudentSearch] = useState(""),
    [parent, setParent] = useState(""),
    [student, setStudent] = useState("");
  const [form, setForm] = useState<FamilyLink | null>(null),
    [history, setHistory] = useState<History[]>([]),
    [historyNext, setHistoryNext] = useState<number | null>(null);
  const parents = users
    .filter(
      (u) =>
        u.role === "parent" &&
        `${u.name} ${u.login}`
          .toLocaleLowerCase()
          .includes(parentSearch.toLocaleLowerCase()),
    )
    .slice(0, 100);
  const pupils = state.students
    .filter((s) =>
      s.name.toLocaleLowerCase().includes(studentSearch.toLocaleLowerCase()),
    )
    .slice(0, 100);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const list = async (offset = 0) => {
    const r = await api(
      `access/families?${new URLSearchParams({ q: query, offset: String(offset) })}`,
      workspace,
    );
    setItems((old) => (offset ? [...old, ...r.items] : r.items));
    setNext(r.nextOffset);
  };
  const open = async (p: string, s: string) => {
    const r = await api(`access/families/${p}/${s}`, workspace);
    setForm(r);
    setHistory([]);
    setHistoryNext(null);
  };
  const changes = async (before?: number) => {
    if (!form) return;
    const r = await api(
      `access/families/${form.parentId}/${form.studentId}/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setHistory((old) => (before ? [...old, ...r.items] : r.items));
    setHistoryNext(r.nextBefore);
  };
  useEffect(() => {
    if (!expanded) return;
    let live = true;
    setBusy(true);
    const timer = setTimeout(() => {
      api(`access/families?${new URLSearchParams({ q: query })}`, workspace)
        .then((r) => {
          if (live) {
            setItems(r.items);
            setNext(r.nextOffset);
          }
        })
        .catch((e) => {
          if (live) setError(e.message);
        })
        .finally(() => {
          if (live) setBusy(false);
        });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [expanded, query, workspace]);
  return (
    <details
      className="family-panel"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Семьи и доступ к детям</summary>
      {expanded && (
        <>
          <p>
            Одна связь относится к одному аккаунту родителя и одному ученику.
            Нескольким представителям выдаются отдельные аккаунты; один родитель
            может иметь нескольких детей.
          </p>
          <p>
            Старые назначения действуют до изменения школой и отмечены как
            требующие уточнения. Подтверждение означает проверку сотрудником
            школы; система не проверяет документы автоматически.
          </p>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          <label className="field">
            <span>Найти родителя или ученика в реестре</span>
            <input
              type="search"
              value={query}
              maxLength={100}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await list();
                await onChanged();
              })
            }
          >
            Обновить реестр
          </button>
          {items.map((item) => (
            <article className="family-card" key={keyOf(item)}>
              <strong>
                {item.parentName} → {item.studentName}
              </strong>
              <p>
                {activities[item.activity]} ·{" "}
                {item.verified ? "Проверено школой" : "Требует уточнения"}
                {!item.accountActive ? " · аккаунт неактивен" : ""}
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void run(() => open(item.parentId, item.studentId))
                }
              >
                Открыть связь
              </button>
            </article>
          ))}
          {!busy && !items.length && <p>Связи не найдены.</p>}
          {next !== null && (
            <button disabled={busy} onClick={() => void run(() => list(next))}>
              Следующие связи
            </button>
          )}
          <details>
            <summary>Назначить ребёнка или открыть конкретную связь</summary>
            <fieldset className="access-fields" disabled={busy}>
              <label className="field">
                <span>Поиск аккаунта родителя</span>
                <input
                  value={parentSearch}
                  onChange={(e) => {
                    setParentSearch(e.target.value);
                    setParent("");
                  }}
                />
              </label>
              <label className="field">
                <span>Аккаунт родителя</span>
                <select
                  value={parent}
                  onChange={(e) => setParent(e.target.value)}
                >
                  <option value="">Выберите родителя</option>
                  {parents.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · {p.login}
                      {!p.active ? " · отключён" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Поиск ученика</span>
                <input
                  value={studentSearch}
                  onChange={(e) => {
                    setStudentSearch(e.target.value);
                    setStudent("");
                  }}
                />
              </label>
              <label className="field">
                <span>Ученик</span>
                <select
                  value={student}
                  onChange={(e) => setStudent(e.target.value)}
                >
                  <option value="">Выберите ученика</option>
                  {pupils.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {!s.active ? " · неактивен" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <p>
                Показаны первые 100 совпадений. Уточните поиск, если нужной
                записи нет.
              </p>
              <button
                disabled={!parent || !student}
                onClick={() => void run(() => open(parent, student))}
              >
                Подготовить связь
              </button>
            </fieldset>
          </details>
          {form && (
            <form
              className="family-card"
              onSubmit={(e) => {
                e.preventDefault();
                if (
                  !window.confirm(
                    `${form.status === "revoked" ? "Отозвать" : "Сохранить"} доступ ${form.parentName} к ученику ${form.studentName}? Изменение применяется ко всем разделам кабинета.`,
                  )
                )
                  return;
                void run(async () => {
                  await api(
                    `access/families/${form.parentId}/${form.studentId}`,
                    workspace,
                    "PUT",
                    {
                      version: form.version,
                      accountVersion: form.accountVersion,
                      relationship: form.relationship,
                      verified: form.verified,
                      evidence: form.evidence || "",
                      reason: form.reason || "",
                      startDate: form.startDate || null,
                      endDate: form.endDate || null,
                      status: form.status,
                    },
                  );
                  await open(form.parentId, form.studentId);
                  await list();
                  await onChanged();
                  setNotice(
                    "Связь сохранена. Текущие права обновлены во всех разделах кабинета.",
                  );
                });
              }}
            >
              <h3>
                {form.parentName} → {form.studentName}
              </h3>
              <fieldset className="access-fields" disabled={busy}>
                <label className="field">
                  <span>Отношение к ребёнку</span>
                  <select
                    value={form.relationship}
                    onChange={(e) =>
                      setForm({ ...form, relationship: e.target.value })
                    }
                  >
                    {Object.entries(relationships).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={form.verified}
                    onChange={(e) =>
                      setForm({ ...form, verified: e.target.checked })
                    }
                  />
                  Школа проверила сведения о представителе
                </label>
                <label className="field">
                  <span>Основание проверки — только администраторам</span>
                  <textarea
                    value={form.evidence || ""}
                    maxLength={2000}
                    onChange={(e) =>
                      setForm({ ...form, evidence: e.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>Доступ к кабинету ребёнка</span>
                  <select
                    value={form.status}
                    onChange={(e) =>
                      setForm({ ...form, status: e.target.value })
                    }
                  >
                    <option value="active">Разрешён в указанные сроки</option>
                    <option value="revoked">Отозван</option>
                  </select>
                </label>
                <label className="field">
                  <span>Начало доступа (необязательно)</span>
                  <input
                    type="date"
                    value={form.startDate || ""}
                    onChange={(e) =>
                      setForm({ ...form, startDate: e.target.value || null })
                    }
                  />
                </label>
                <label className="field">
                  <span>Последний день доступа (необязательно)</span>
                  <input
                    type="date"
                    value={form.endDate || ""}
                    onChange={(e) =>
                      setForm({ ...form, endDate: e.target.value || null })
                    }
                  />
                </label>
                <p>
                  Asia/Qyzylorda, до конца последнего дня. Срок проверяется при
                  каждом запросе, без перезапуска сервера. Разрешение охватывает
                  действующие разделы кабинета, включая выбор курсов; согласия и
                  выдача ребёнка ещё не входят в этот реестр.
                </p>
                <label className="field">
                  <span>Причина изменения — только администраторам</span>
                  <textarea
                    required
                    maxLength={1000}
                    value={form.reason || ""}
                    onChange={(e) =>
                      setForm({ ...form, reason: e.target.value })
                    }
                  />
                </label>
                <button
                  className="primary"
                  type="submit"
                  disabled={!form.reason?.trim()}
                >
                  Сохранить связь и доступ
                </button>
                <button type="button" onClick={() => void run(() => changes())}>
                  История связи
                </button>
              </fieldset>
              {history.map((h) => (
                <article className="family-history" key={h.id}>
                  <p>
                    {new Date(h.created).toLocaleString("ru-RU")} · {h.actor} ·
                    версия {h.version}
                  </p>
                  <p>
                    {relationships[h.document.relationship]} ·{" "}
                    {h.document.verified ? "Проверено школой" : "Не уточнено"} ·{" "}
                    {h.document.status === "active" ? "Разрешено" : "Отозвано"}
                  </p>
                  <p>
                    {h.document.startDate || "Без начала"} —{" "}
                    {h.document.endDate || "Без окончания"}
                  </p>
                  <p className="journal-text">
                    Основание: {h.document.evidence || "Не указано"}
                  </p>
                  <p className="journal-text">Причина: {h.document.reason}</p>
                  <small>
                    {h.source.startsWith("invitation:")
                      ? `Школьное приглашение ${h.source.split(":")[1]}`
                      : h.source === "account.assignment"
                        ? "Настройка аккаунта"
                        : h.source === "account.revoked"
                          ? "Отзыв в аккаунте"
                          : "Реестр семей"}
                  </small>
                </article>
              ))}
              {historyNext !== null && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(() => changes(historyNext))}
                >
                  Предыдущие изменения связи
                </button>
              )}
            </form>
          )}
        </>
      )}
    </details>
  );
}
