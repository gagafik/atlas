import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { State } from "./types";
import type { Account } from "./auth";
import FamilyLinksTable, { familyOpenId } from "./FamilyLinksTable";
import { leaveDecision, type LoadStatus } from "./accessModel";
import {
  activities,
  filterLinks,
  linkCounts,
  linkFormDirty,
  linkKey as keyOf,
  relationships,
  savedLinkOutcome,
  type FamilyLink,
  type LinkFilter,
} from "./familyLinksModel";
import "./FamilyPanel.css";

type History = {
  id: number;
  created: string;
  actor: string;
  source: string;
  version: number;
  document: FamilyLink;
};

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

const leaveQuestion =
  "В связи есть несохранённые изменения. Уйти без сохранения?";
const linkFilters: { id: LinkFilter; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "unclear", label: "Требуют уточнения" },
  { id: "inactive", label: "Доступ не действует" },
];

// The administrator's registry of the parent-pupil links: a table; a link and the picker of a new one open in place of it.
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
  const [query, setQuery] = useState(""),
    [items, setItems] = useState<FamilyLink[]>([]),
    [next, setNext] = useState<number | null>(null),
    [status, setStatus] = useState<LoadStatus>("loading"),
    [filter, setFilter] = useState<LinkFilter>("all"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [parentSearch, setParentSearch] = useState(""),
    [studentSearch, setStudentSearch] = useState(""),
    [parent, setParent] = useState(""),
    [student, setStudent] = useState(""),
    [picking, setPicking] = useState(false);
  const [form, setForm] = useState<FamilyLink | null>(null),
    // The link as it was opened or last saved, to tell whether the form holds changes.
    [base, setBase] = useState<FamilyLink | null>(null),
    [history, setHistory] = useState<History[]>([]),
    [historyNext, setHistoryNext] = useState<number | null>(null);
  const [highlightKey, setHighlightKey] = useState<string | null>(null),
    [focusTick, setFocusTick] = useState(0);
  // The button that gets the focus when the registry comes back; the link saved while the form was open; a failed load.
  const returnFocus = useRef<string | null>(null),
    saved = useRef<FamilyLink | null>(null),
    loadFailed = useRef(false),
    heading = useRef<HTMLHeadingElement>(null);
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
    setStatus("ready");
    loadFailed.current = false;
  };
  const open = async (p: string, s: string) => {
    const r = await api(`access/families/${p}/${s}`, workspace);
    setForm(r);
    setBase(r);
    setHistory([]);
    setHistoryNext(null);
    setFocusTick((n) => n + 1);
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
    let live = true;
    setBusy(true);
    const timer = setTimeout(() => {
      api(`access/families?${new URLSearchParams({ q: query })}`, workspace)
        .then((r) => {
          if (live) {
            setItems(r.items);
            setNext(r.nextOffset);
            setStatus("ready");
            // The line about a load that failed goes away with the load that succeeded.
            if (loadFailed.current) setError("");
            loadFailed.current = false;
          }
        })
        .catch((e) => {
          if (live) {
            // Rows of another search are not the answer to this one.
            setItems([]);
            setNext(null);
            setStatus("failed");
            loadFailed.current = true;
            setError(e.message);
          }
        })
        .finally(() => {
          if (live) setBusy(false);
        });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, workspace]);
  // The page asks before the link with changes is lost by leaving the screen, the workspace or the program.
  const dirty = linkFormDirty(form, base);
  useEffect(() => {
    if (!dirty) return;
    const leave = (e: Event) => {
      if (!window.confirm(leaveQuestion)) e.preventDefault();
    };
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("atlas-homework-leave", leave);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, [dirty]);
  // A form or the picker opens: its heading takes the focus, so that a screen reader says what has opened.
  useEffect(() => {
    if (!focusTick) return;
    heading.current?.focus({ preventScroll: true });
    heading.current?.scrollIntoView?.({ block: "start" });
  }, [focusTick]);
  const view = form ? "form" : picking ? "pick" : "list";
  // The registry comes back: the button of the link that was open (or the add button) gets the focus.
  useEffect(() => {
    if (view !== "list" || busy || !returnFocus.current) return;
    const el =
      document.getElementById(returnFocus.current) ??
      document.getElementById("family-add");
    returnFocus.current = null;
    el?.focus({ preventScroll: true });
    el?.scrollIntoView?.({ block: "nearest" });
  }, [view, busy]);
  // Leaving the open link: nothing while it saves, one question when it holds changes.
  function leaveForm() {
    const decision = leaveDecision(dirty, busy);
    return (
      decision === "go" || (decision === "ask" && window.confirm(leaveQuestion))
    );
  }
  function discardForm() {
    setForm(null);
    setBase(null);
    setHistory([]);
    setHistoryNext(null);
  }
  function openLink(item: FamilyLink) {
    if (!leaveForm()) return;
    if (saved.current && keyOf(saved.current) !== keyOf(item))
      saved.current = null;
    setHighlightKey(null);
    returnFocus.current = familyOpenId(item);
    void run(() => open(item.parentId, item.studentId));
  }
  function openPicker() {
    if (!leaveForm()) return;
    discardForm();
    saved.current = null;
    setHighlightKey(null);
    setError("");
    setNotice("");
    returnFocus.current = "family-add";
    setPicking(true);
    setFocusTick((n) => n + 1);
  }
  // Back to the registry: the saved link is marked when the registry shows it, and the notice says so when it does not.
  function backToRegistry() {
    if (!leaveForm()) return;
    setError("");
    setNotice("");
    const link = saved.current;
    saved.current = null;
    discardForm();
    setPicking(false);
    if (!link) return;
    const where = savedLinkOutcome(items, filter, keyOf(link));
    if (where === "visible") {
      setHighlightKey(keyOf(link));
      returnFocus.current = familyOpenId(link);
    } else
      setNotice(
        where === "filtered"
          ? "Связь сохранена. Выбранный фильтр её не показывает: выберите «Все»."
          : "Связь сохранена. Поиск или загруженные страницы реестра её не показывают.",
      );
  }
  const filterCounts = linkCounts(items),
    rows = filterLinks(items, filter);
  const messages = (
    <>
      {error && (
        <p role="alert" className="error-banner family-message">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="info-line family-message">
          {notice}
        </p>
      )}
    </>
  );
  return (
    <section className="family-links">
      {/* While a link is open the messages are next to its buttons; the notice is repeated here, as before. */}
      {view === "list" && error && (
        <p role="alert" className="error-banner family-message">
          {error}
        </p>
      )}
      {notice && (
        <p
          role={view === "list" ? "status" : undefined}
          className="info-line family-message"
        >
          {notice}
        </p>
      )}
      <div className="family-browse" hidden={view !== "list"}>
        <div className="family-toolbar">
          <label>
            <span>Найти родителя или ученика в реестре</span>
            <input
              type="search"
              value={query}
              maxLength={100}
              onChange={(e) => {
                setQuery(e.target.value);
                setHighlightKey(null);
              }}
            />
          </label>
          <div className="segmented" role="group" aria-label="Состояние связи">
            {linkFilters.map((f) => (
              <button
                key={f.id}
                type="button"
                className={filter === f.id ? "selected" : ""}
                aria-pressed={filter === f.id}
                onClick={() => {
                  setFilter(f.id);
                  setHighlightKey(null);
                }}
              >
                {f.label} <span>{filterCounts[f.id]}</span>
              </button>
            ))}
          </div>
          <button
            className="family-reload"
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
          <button
            id="family-add"
            className="primary"
            disabled={busy}
            onClick={openPicker}
          >
            ＋ Назначить ребёнка
          </button>
        </div>
        <details className="family-help">
          <summary>Как устроены связи</summary>
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
        </details>
        <FamilyLinksTable
          rows={rows}
          status={status}
          busy={busy}
          highlightKey={highlightKey}
          onOpen={openLink}
        >
          {(!!items.length || next !== null) && (
            <div className="family-more">
              <span>
                Показано {rows.length}
                {rows.length !== items.length
                  ? ` из ${items.length} загруженных`
                  : ""}
              </span>
              {next !== null && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => list(next))}
                >
                  Следующие связи
                </button>
              )}
            </div>
          )}
        </FamilyLinksTable>
      </div>
      {view === "pick" && (
        <div className="dated-form family-form">
          <div className="family-form-head">
            <button type="button" disabled={busy} onClick={backToRegistry}>
              ← К реестру
            </button>
            <h3 ref={heading} tabIndex={-1}>
              Назначить ребёнка или открыть связь
            </h3>
          </div>
          <fieldset className="access-fields" disabled={busy}>
            <div className="dated-fields">
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
            </div>
            <p>
              Показаны первые 100 совпадений. Уточните поиск, если нужной записи
              нет.
            </p>
            {messages}
            <div className="family-form-actions">
              <button
                disabled={!parent || !student}
                onClick={() =>
                  void run(async () => {
                    await open(parent, student);
                    setPicking(false);
                  })
                }
              >
                Подготовить связь
              </button>
            </div>
          </fieldset>
        </div>
      )}
      {form && (
        <form
          className="dated-form family-form"
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
              saved.current = form;
              await open(form.parentId, form.studentId);
              await list();
              await onChanged();
              setNotice(
                "Связь сохранена. Текущие права обновлены во всех разделах кабинета.",
              );
            });
          }}
        >
          <div className="family-form-head">
            <button type="button" disabled={busy} onClick={backToRegistry}>
              ← К реестру
            </button>
            <h3 ref={heading} tabIndex={-1}>
              {form.parentName} → {form.studentName}
            </h3>
          </div>
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
            <label className="check-line">
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
                onChange={(e) => setForm({ ...form, evidence: e.target.value })}
              />
            </label>
            <div className="dated-fields">
              <label className="field">
                <span>Доступ к кабинету ребёнка</span>
                <select
                  value={form.status}
                  onChange={(e) => setForm({ ...form, status: e.target.value })}
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
            </div>
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
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
              />
            </label>
            {messages}
            <div className="family-form-actions">
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
            </div>
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
    </section>
  );
}
