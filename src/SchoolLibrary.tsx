import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { displayTime } from "./meetings";
import { BookEditor, CopyEditor, LibrarySettings } from "./LibraryEditors";
import LibraryInventory from "./LibraryInventory";
import {
  blankBook,
  kinds,
  conditions,
  copyStatuses,
  loanStatuses,
  actions,
  type Book,
  type Copy,
  type CopyData,
  type Options,
  type Config,
  type Loan,
  type CartRow,
  type Preview,
  type History,
} from "./libraryTypes";
import "./SchoolLibrary.css";

export default function SchoolLibrary({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const spaces =
    user.role === "admin"
      ? ["school", "demo"]
      : Array.from(
          new Set([...user.workspaces, ...user.grants.map((g) => g.workspace)]),
        );
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const [options, setOptions] = useState<Options | null>(null),
    [tab, setTab] = useState("catalog"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [books, setBooks] = useState<Book[]>([]),
    [bookNext, setBookNext] = useState<number | null>(null),
    [q, setQ] = useState(""),
    [query, setQuery] = useState(""),
    [kind, setKind] = useState(""),
    [archived, setArchived] = useState(false);
  const [book, setBook] = useState<Book | null>(null),
    [copies, setCopies] = useState<Copy[]>([]),
    [copyNext, setCopyNext] = useState<number | null>(null),
    [bookEdit, setBookEdit] = useState<Book | null>(null),
    [copyEdit, setCopyEdit] = useState<Copy | null>(null),
    [adding, setAdding] = useState(false),
    [inventory, setInventory] = useState(""),
    [location, setLocation] = useState("");
  const [loans, setLoans] = useState<Loan[]>([]),
    [loanNext, setLoanNext] = useState<number | null>(null),
    [loan, setLoan] = useState<Loan | null>(null),
    [status, setStatus] = useState("open"),
    [overdue, setOverdue] = useState(false),
    [returnCondition, setReturnCondition] = useState("good"),
    [renewDate, setRenewDate] = useState("");
  const [cart, setCart] = useState<CartRow[]>([]),
    [borrowerKind, setBorrowerKind] = useState("student"),
    [borrower, setBorrower] = useState(""),
    [borrowerSearch, setBorrowerSearch] = useState(""),
    [classId, setClassId] = useState(""),
    [due, setDue] = useState(""),
    [preview, setPreview] = useState<Preview | null>(null);
  const [settings, setSettings] = useState<Config | null>(null),
    [dirty, setDirty] = useState(false),
    [history, setHistory] = useState<{
      entity: string;
      id: string;
      items: History[];
      nextBefore: number | null;
    } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const canLeave = (withCart = true) =>
    !(dirty || (withCart && cart.length)) ||
    window.confirm(
      "Есть несохранённые изменения или подготовленная выдача. Продолжить без сохранения?",
    );
  function clearDraft() {
    setBookEdit(null);
    setCopyEdit(null);
    setAdding(false);
    setSettings(null);
    setDirty(false);
    setInventory("");
    setPreview(null);
  }
  function clear() {
    clearDraft();
    setBook(null);
    setCopies([]);
    setCopyNext(null);
    setLoan(null);
    setCart([]);
    setHistory(null);
  }
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty || cart.length) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, cart]);
  const bookPath = (before?: number) =>
    `library/titles?q=${encodeURIComponent(q)}&kind=${kind}&archived=${archived}${before ? "&before=" + before : ""}`;
  const loanPath = (before?: number) =>
    `library/loans?status=${status}&overdue=${overdue}${before ? "&before=" + before : ""}`;
  async function load() {
    const n = ++sequence.current;
    const [o, b, l] = await Promise.all([
      api("library/options", workspace),
      api(bookPath(), workspace),
      api(loanPath(), workspace),
    ]);
    if (n !== sequence.current) return;
    setOptions(o);
    setBooks(b.items);
    setBookNext(b.nextBefore);
    setLoans(l.items);
    setLoanNext(l.nextBefore);
    setDue((v) => v || o.defaultDue);
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      setPreview(null);
      if ([401, 403, 404].includes(e.status)) {
        clear();
        setOptions(null);
        setArchived(false);
        setTab("catalog");
        setBooks([]);
        setLoans([]);
      }
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    clear();
    setOptions(null);
    setBooks([]);
    setLoans([]);
    setDue("");
    void run(load);
    return () => {
      ++sequence.current;
    };
  }, [workspace, q, kind, archived, status, overdue]);
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const r = await api("library/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return r;
  }
  async function changed() {
    clear();
    await load();
    setNotice("Изменения сохранены.");
  }
  function switchValue(change: () => void) {
    if (canLeave()) {
      clear();
      change();
    }
  }
  const [copyQuery, setCopyQuery] = useState(""),
    [copySearch, setCopySearch] = useState("");
  async function openBook(
    value: Book,
    more = false,
    filter = more ? copyQuery : "",
  ) {
    if (!more && !canLeave(false)) return;
    const r = options?.canIssue
      ? await api(
          `library/titles/${value.id}/copies?q=${encodeURIComponent(filter)}${more && copyNext ? "&before=" + copyNext : ""}`,
          workspace,
        )
      : { items: [], nextBefore: null };
    if (!more) clearDraft();
    setCopyQuery(filter);
    setCopySearch(filter);
    setBook(value);
    setCopies(more ? [...copies, ...r.items] : r.items);
    setCopyNext(r.nextBefore);
    setHistory(null);
  }
  async function showHistory(entity: string, id: string, more = false) {
    await run(async () => {
      const r = await api(
        `library/history/${entity}/${id}${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
        workspace,
      );
      setHistory({
        entity,
        id,
        items: more ? [...history!.items, ...r.items] : r.items,
        nextBefore: r.nextBefore,
      });
    });
  }
  const people =
    (borrowerKind === "student" ? options?.students : options?.employees) || [];
  const visiblePeople = people.filter(
    (p) =>
      (borrowerKind !== "student" || !classId || p.classId === classId) &&
      `${p.name} ${p.className || ""}`
        .toLocaleLowerCase()
        .includes(borrowerSearch.toLocaleLowerCase()),
  );
  function addToCart(c: Copy) {
    const p = people.find((p) => p.id === borrower);
    if (!p) {
      setError("Выберите читателя перед добавлением экземпляра.");
      return;
    }
    setCart([
      ...cart,
      {
        copyId: c.id,
        copyVersion: c.version,
        borrower: { kind: borrowerKind, id: p.id },
        label: `${c.inventory} · ${c.title.title} → ${p.name}`,
      },
    ]);
    setPreview(null);
    setNotice(
      "Экземпляр добавлен в список выдачи. Можно выбрать следующего читателя или другую книгу.",
    );
  }
  const issueBody = () => ({
    items: cart.map(({ copyId, copyVersion, borrower }) => ({
      copyId,
      copyVersion,
      borrower,
    })),
    due,
  });
  async function loanAction(action: string) {
    if (!loan) return;
    const reason = ["renew", "lost", "writeoff", "cancel"].includes(action)
      ? window.prompt("Причина действия")
      : "";
    if (reason === null) return;
    if (
      ["return", "found"].includes(action) &&
      !window.confirm(
        `Подтвердить получение книги «${loan.title.title}», № ${loan.inventory}? Состояние: ${conditions[returnCondition]}.`,
      )
    )
      return;
    await run(async () => {
      const r = await mutate(`loans/${loan.id}/action`, {
        version: loan.version,
        action,
        reason,
        condition: returnCondition,
        due: action === "renew" ? renewDate : null,
      });
      await load();
      setLoan(r);
      setHistory(null);
      setNotice("Действие сохранено.");
    });
  }
  const csv = `/api/library/export?workspace=${encodeURIComponent(workspace)}&status=${status}&overdue=${overdue}`;
  return (
    <dialog
      className="school-library"
      ref={dialog}
      aria-labelledby="library-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && canLeave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="library-title">Библиотека и учебники</h2>
          <p>Фонд, выдачи и возвраты</p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (canLeave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <fieldset disabled={busy}>
        <div className="library-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) =>
                switchValue(() => {
                  setWorkspace(e.target.value);
                  setArchived(false);
                  setTab("catalog");
                })
              }
            >
              {spaces.map((w) => (
                <option key={w} value={w}>
                  {w === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (canLeave())
                void run(async () => {
                  clear();
                  await load();
                });
            }}
          >
            Обновить
          </button>
        </div>
        <nav className="library-tabs" aria-label="Разделы библиотеки">
          {[
            ["catalog", "Каталог"],
            [
              "loans",
              options?.canIssue ? "Выдачи и задолженность" : "Мои книги",
            ],
            ...(options?.canCatalog ? [["inventory", "Инвентаризация"]] : []),
            ...(options?.admin ? [["settings", "Настройки и доступ"]] : []),
          ].map(([v, n]) => (
            <button
              key={v}
              aria-pressed={tab === v}
              onClick={() => switchValue(() => setTab(v))}
            >
              {n}
            </button>
          ))}
        </nav>
        {options && tab === "catalog" && (
          <section>
            <form
              className="library-controls"
              onSubmit={(e) => {
                e.preventDefault();
                switchValue(() => setQ(query));
              }}
            >
              <label>
                Поиск по каталогу
                <input
                  maxLength={200}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Название, автор, ISBN, предмет"
                />
              </label>
              <button>Найти</button>
              <label>
                Вид
                <select
                  value={kind}
                  onChange={(e) => switchValue(() => setKind(e.target.value))}
                >
                  <option value="">Все</option>
                  {Object.entries(kinds).map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              {options.canCatalog && (
                <label className="library-check">
                  <input
                    type="checkbox"
                    checked={archived}
                    onChange={(e) =>
                      switchValue(() => setArchived(e.target.checked))
                    }
                  />
                  Включая архив названий
                </label>
              )}
            </form>
            {options.canCatalog && (
              <button
                onClick={() => {
                  if (canLeave()) {
                    clear();
                    setBookEdit(blankBook());
                  }
                }}
              >
                Добавить название
              </button>
            )}
            {bookEdit && (
              <BookEditor
                value={bookEdit}
                change={(v) => {
                  setBookEdit(v);
                  setDirty(true);
                }}
                cancel={() => {
                  if (canLeave(false)) clearDraft();
                }}
                save={() => {
                  const reason = bookEdit.id
                    ? window.prompt("Причина изменения названия")
                    : "";
                  if (reason === null) return;
                  void run(async () => {
                    await mutate(
                      bookEdit.id ? "titles/" + bookEdit.id : "titles",
                      {
                        version: bookEdit.version,
                        data: bookEdit.data,
                        archived: bookEdit.archived,
                        reason,
                      },
                      bookEdit.id ? "PUT" : "POST",
                    );
                    await changed();
                  });
                }}
              />
            )}
            <div className="library-grid">
              <section className="library-list" aria-label="Названия">
                <h3>Каталог</h3>
                {books.length === 0 && <p>Названия не найдены.</p>}
                {books.map((b) => (
                  <button
                    key={b.id}
                    className={book?.id === b.id ? "selected" : ""}
                    onClick={() => void run(() => openBook(b))}
                  >
                    <strong>{b.data.title}</strong>
                    <span>
                      {b.data.author || kinds[b.data.kind]} · {b.available}{" "}
                      доступно из {b.total}
                      {b.archived ? " · Архив" : ""}
                    </span>
                  </button>
                ))}
                {bookNext && (
                  <button
                    onClick={() =>
                      void run(async () => {
                        const r = await api(bookPath(bookNext), workspace);
                        setBooks([...books, ...r.items]);
                        setBookNext(r.nextBefore);
                      })
                    }
                  >
                    Ещё названия
                  </button>
                )}
              </section>
              {book && (
                <section>
                  <h3>{book.data.title}</h3>
                  <p>
                    {[
                      book.data.author,
                      book.data.publisher,
                      book.data.year,
                      book.data.isbn,
                      book.data.subject,
                      book.data.grade,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  <p>{book.data.description}</p>
                  {options.canCatalog && (
                    <div className="library-actions">
                      <button
                        onClick={() => {
                          if (canLeave(false)) {
                            clearDraft();
                            setBookEdit({ ...book, data: { ...book.data } });
                          }
                        }}
                      >
                        Изменить название
                      </button>
                      <button
                        disabled={book.archived}
                        onClick={() => {
                          if (canLeave(false)) {
                            clearDraft();
                            setAdding(true);
                          }
                        }}
                      >
                        Добавить экземпляры
                      </button>
                      <button
                        onClick={() => void showHistory("title", book.id)}
                      >
                        История названия
                      </button>
                    </div>
                  )}
                  {adding && (
                    <form
                      className="library-add-copies"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(async () => {
                          const items = inventory
                            .split("\n")
                            .map((x) => x.trim())
                            .filter(Boolean)
                            .map((inventory) => ({
                              inventory,
                              location,
                              condition: "good",
                              status: "available",
                            }));
                          await mutate(`titles/${book.id}/copies`, {
                            titleVersion: book.version,
                            items,
                          });
                          await changed();
                        });
                      }}
                    >
                      <h4>Поступление экземпляров</h4>
                      <label>
                        Инвентарные номера — по одному на строку
                        <textarea
                          required
                          rows={5}
                          value={inventory}
                          onChange={(e) => {
                            setInventory(e.target.value);
                            setDirty(true);
                          }}
                        />
                      </label>
                      <label>
                        Место хранения
                        <input
                          maxLength={160}
                          value={location}
                          onChange={(e) => {
                            setLocation(e.target.value);
                            setDirty(true);
                          }}
                        />
                      </label>
                      <p>
                        До 200 экземпляров за один раз. Если номер повторяется,
                        весь пакет останется без изменений.
                      </p>
                      <button>Добавить в фонд</button>
                      <button
                        type="button"
                        onClick={() => {
                          if (canLeave(false)) clearDraft();
                        }}
                      >
                        Отмена
                      </button>
                    </form>
                  )}
                  {copyEdit && (
                    <form
                      className="library-copy-editor"
                      onSubmit={(e) => {
                        e.preventDefault();
                        const reason = window.prompt(
                          "Причина изменения экземпляра",
                        );
                        if (reason)
                          void run(async () => {
                            const { inventory, location, condition, status } =
                              copyEdit;
                            await mutate(
                              "copies/" + copyEdit.id,
                              {
                                version: copyEdit.version,
                                data: {
                                  inventory,
                                  location,
                                  condition,
                                  status,
                                },
                                reason,
                              },
                              "PUT",
                            );
                            await changed();
                          });
                      }}
                    >
                      <h4>Изменить экземпляр</h4>
                      <CopyEditor
                        value={copyEdit}
                        change={(d: CopyData) => {
                          setCopyEdit({ ...copyEdit, ...d });
                          setDirty(true);
                        }}
                      />
                      <button>Сохранить экземпляр</button>
                      <button
                        type="button"
                        onClick={() => {
                          if (canLeave(false)) clearDraft();
                        }}
                      >
                        Отмена
                      </button>
                    </form>
                  )}
                  {options.canIssue && (
                    <>
                      <h4>Читатель для выдачи</h4>
                      <div className="library-controls">
                        <label>
                          Кому
                          <select
                            value={borrowerKind}
                            onChange={(e) => {
                              setBorrowerKind(e.target.value);
                              setBorrower("");
                            }}
                          >
                            <option value="student">Ученику</option>
                            {options.canCatalog && (
                              <option value="employee">Сотруднику</option>
                            )}
                          </select>
                        </label>
                        {borrowerKind === "student" && (
                          <label>
                            Класс
                            <select
                              value={classId}
                              onChange={(e) => {
                                setClassId(e.target.value);
                                setBorrower("");
                              }}
                            >
                              <option value="">Все доступные классы</option>
                              {options.classes.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.name}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                        <label>
                          Найти читателя
                          <input
                            value={borrowerSearch}
                            onChange={(e) => {
                              setBorrowerSearch(e.target.value);
                              setBorrower("");
                            }}
                          />
                        </label>
                        <label>
                          Читатель
                          <select
                            value={borrower}
                            onChange={(e) => setBorrower(e.target.value)}
                          >
                            <option value="">Выберите читателя</option>
                            {visiblePeople.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name}
                                {p.className ? " · " + p.className : ""}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <h4>Экземпляры</h4>
                      <form
                        className="library-controls"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void run(() => openBook(book, false, copySearch));
                        }}
                      >
                        <label>
                          Найти инвентарный номер
                          <input
                            maxLength={80}
                            value={copySearch}
                            onChange={(e) => setCopySearch(e.target.value)}
                          />
                        </label>
                        <button>Найти экземпляр</button>
                      </form>
                      {copies.map((c) => (
                        <article key={c.id} className="library-card">
                          <strong>{c.inventory}</strong>
                          <p>
                            {c.onLoan
                              ? "Выдача не закрыта"
                              : copyStatuses[c.status]}{" "}
                            · {conditions[c.condition]} ·{" "}
                            {c.location || "Место не указано"}
                          </p>
                          <div className="library-actions">
                            <button
                              disabled={
                                c.onLoan ||
                                c.status !== "available" ||
                                c.condition === "damaged" ||
                                book.archived ||
                                cart.some((r) => r.copyId === c.id) ||
                                cart.length >= 100 ||
                                !borrower
                              }
                              onClick={() => addToCart(c)}
                            >
                              В список выдачи
                            </button>
                            {options.canCatalog && (
                              <>
                                <button
                                  disabled={c.onLoan}
                                  onClick={() => {
                                    if (canLeave(false)) {
                                      clearDraft();
                                      setCopyEdit(c);
                                    }
                                  }}
                                >
                                  Изменить экземпляр
                                </button>
                                <button
                                  onClick={() => void showHistory("copy", c.id)}
                                >
                                  История экземпляра
                                </button>
                              </>
                            )}
                          </div>
                        </article>
                      ))}
                      {copyNext && (
                        <button
                          onClick={() => void run(() => openBook(book, true))}
                        >
                          Ещё экземпляры
                        </button>
                      )}
                    </>
                  )}
                </section>
              )}
            </div>
            {cart.length > 0 && (
              <section className="library-cart">
                <h3>Подготовленная выдача ({cart.length})</h3>
                <p>
                  Каждый экземпляр закрепляется за указанным читателем. До
                  подтверждения книги остаются доступными другим библиотекарям.
                </p>
                {cart.map((r, i) => (
                  <div className="library-cart-row" key={r.copyId}>
                    <span>{r.label}</span>
                    <button
                      aria-label={`Убрать экземпляр ${i + 1}`}
                      onClick={() => {
                        setCart(cart.filter((_, j) => j !== i));
                        setPreview(null);
                      }}
                    >
                      Убрать
                    </button>
                  </div>
                ))}
                <label>
                  Вернуть до
                  <input
                    required
                    type="date"
                    min={options.today}
                    value={due}
                    onChange={(e) => {
                      setDue(e.target.value);
                      setPreview(null);
                    }}
                  />
                </label>
                <button
                  onClick={() =>
                    void run(async () => {
                      const p = await mutate("loans/preview", issueBody());
                      setPreview(p);
                    })
                  }
                >
                  Проверить выдачу
                </button>
                {preview && (
                  <section className="library-preview">
                    <h4>Проверка перед выдачей</h4>
                    {preview.items.map((r) => (
                      <p key={r.copy.id}>
                        {r.copy.inventory} · {r.copy.title.title} →{" "}
                        {r.borrower.name}
                      </p>
                    ))}
                    <p>
                      Вернуть до: {preview.due}. Выдаётся весь список одной
                      операцией.
                    </p>
                    <button
                      onClick={() =>
                        void run(async () => {
                          await mutate("loans", {
                            ...issueBody(),
                            previewHash: preview.previewHash,
                          });
                          await changed();
                          setTab("loans");
                        })
                      }
                    >
                      Подтвердить выдачу {preview.items.length} экз.
                    </button>
                    <button onClick={() => setPreview(null)}>
                      Отмена проверки
                    </button>
                  </section>
                )}
              </section>
            )}
          </section>
        )}
        {options && tab === "loans" && (
          <section>
            <div className="library-controls">
              <label>
                Состояние выдачи
                <select
                  value={status}
                  onChange={(e) => switchValue(() => setStatus(e.target.value))}
                >
                  <option value="open">Не возвращены</option>
                  <option value="">Вся история</option>
                  {Object.entries(loanStatuses).map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <label className="library-check">
                <input
                  type="checkbox"
                  checked={overdue}
                  onChange={(e) =>
                    switchValue(() => setOverdue(e.target.checked))
                  }
                />
                Просроченные и потерянные
              </label>
              <a href={csv}>Скачать список CSV</a>
            </div>
            <p>
              Просрочка определяется по времени школы. Денежные штрафы не
              начисляются.
            </p>
            <div className="library-grid">
              <div className="library-list">
                {loans.length === 0 && <p>Выдач по этому фильтру нет.</p>}
                {loans.map((l) => (
                  <button
                    key={l.id}
                    className={loan?.id === l.id ? "selected" : ""}
                    onClick={() =>
                      void run(async () => {
                        setLoan(await api("library/loans/" + l.id, workspace));
                        setReturnCondition(l.conditionOut);
                        setRenewDate("");
                        setHistory(null);
                      })
                    }
                  >
                    <strong>
                      {l.title.title} · {l.inventory}
                    </strong>
                    <span>
                      {l.borrowerName} · до {l.due}
                    </span>
                    <span>
                      {loanStatuses[l.status]}
                      {l.overdue ? " · Просрочено" : ""}
                    </span>
                  </button>
                ))}
                {loanNext && (
                  <button
                    onClick={() =>
                      void run(async () => {
                        const r = await api(loanPath(loanNext), workspace);
                        setLoans([...loans, ...r.items]);
                        setLoanNext(r.nextBefore);
                      })
                    }
                  >
                    Ещё выдачи
                  </button>
                )}
              </div>
              {loan && (
                <section className="library-loan">
                  <h3>{loan.title.title}</h3>
                  <p>
                    {loan.inventory} · {loan.borrowerName}
                  </p>
                  <p>
                    {loanStatuses[loan.status]}
                    {loan.overdue ? " · Просрочено" : ""}. Выдана{" "}
                    {displayTime(loan.issued)}, вернуть до {loan.due}.
                  </p>
                  <p>
                    При выдаче: {conditions[loan.conditionOut]}.
                    {loan.conditionIn
                      ? " При возврате: " + conditions[loan.conditionIn] + "."
                      : ""}{" "}
                    Продлений: {loan.renewals}.
                  </p>
                  {loan.returned && (
                    <p>Закрыта: {displayTime(loan.returned)}</p>
                  )}
                  {loan.canManage && (
                    <>
                      <div className="library-actions">
                        <button
                          onClick={() => void showHistory("loan", loan.id)}
                        >
                          История выдачи
                        </button>
                        {["active", "lost"].includes(loan.status) && (
                          <button onClick={() => void loanAction("remind")}>
                            Напомнить читателю
                          </button>
                        )}
                      </div>
                      {["active", "lost"].includes(loan.status) && (
                        <>
                          <label>
                            Состояние при возврате
                            <select
                              value={returnCondition}
                              onChange={(e) =>
                                setReturnCondition(e.target.value)
                              }
                            >
                              {Object.entries(conditions).map(([v, n]) => (
                                <option key={v} value={v}>
                                  {n}
                                </option>
                              ))}
                            </select>
                          </label>
                          <button
                            onClick={() =>
                              void loanAction(
                                loan.status === "lost" ? "found" : "return",
                              )
                            }
                          >
                            {loan.status === "lost"
                              ? "Найдена и возвращена"
                              : "Принять возврат"}
                          </button>
                        </>
                      )}
                      {loan.status === "active" && (
                        <>
                          <div className="library-controls">
                            <label>
                              Новый срок возврата
                              <input
                                type="date"
                                min={loan.due}
                                value={renewDate}
                                onChange={(e) => setRenewDate(e.target.value)}
                              />
                            </label>
                            <button
                              disabled={!renewDate}
                              onClick={() => void loanAction("renew")}
                            >
                              Продлить
                            </button>
                          </div>
                          <button onClick={() => void loanAction("lost")}>
                            Сообщить о потере
                          </button>
                          {options.admin && (
                            <button onClick={() => void loanAction("cancel")}>
                              Отменить ошибочную выдачу
                            </button>
                          )}
                        </>
                      )}
                      {loan.status === "lost" && options.admin && (
                        <button onClick={() => void loanAction("writeoff")}>
                          Согласовать списание и закрыть выдачу
                        </button>
                      )}
                    </>
                  )}
                </section>
              )}
            </div>
          </section>
        )}
        {options?.canCatalog && tab === "inventory" && (
          <LibraryInventory
            key={workspace}
            workspace={workspace}
            run={run}
            mutate={mutate}
            history={(e, id) => void showHistory(e, id)}
          />
        )}
        {options?.admin && tab === "settings" && (
          <section>
            {settings ? (
              <LibrarySettings
                value={settings}
                options={options}
                change={(v) => {
                  setSettings(v);
                  setDirty(true);
                }}
                cancel={() => {
                  if (canLeave()) clearDraft();
                }}
                save={() => {
                  const reason = window.prompt(
                    "Причина изменения правил или доступа",
                  );
                  if (reason)
                    void run(async () => {
                      await mutate("config", { ...settings, reason }, "PUT");
                      await changed();
                    });
                }}
              />
            ) : (
              <>
                <p>
                  Срок по умолчанию: {options.config?.loanDays} дней. Продлений:{" "}
                  {options.config?.maxRenewals}. Книг на руках: до{" "}
                  {options.config?.maxLoans}.
                </p>
                <p>
                  Назначено ответственных: {options.config?.assignments.length}.
                  Администрация всегда имеет полный доступ.
                </p>
                <button
                  onClick={() => setSettings(structuredClone(options.config!))}
                >
                  Изменить правила и доступ
                </button>
                <button onClick={() => void showHistory("config", workspace)}>
                  История настроек
                </button>
              </>
            )}
          </section>
        )}
        {history && (
          <section className="library-history">
            <h3>История</h3>
            {history.items.map((h) => (
              <article key={h.seq}>
                <strong>{actions[h.action] || h.action}</strong>
                <p>
                  {h.actor} · {displayTime(h.created)}
                </p>
                {h.reason && <p>{h.reason}</p>}
                <details>
                  <summary>Сохранённые сведения</summary>
                  <HistoryRecord entity={history.entity} value={h.document} />
                </details>
              </article>
            ))}
            {history.nextBefore && (
              <button
                onClick={() =>
                  void showHistory(history.entity, history.id, true)
                }
              >
                Ещё события
              </button>
            )}
            <button onClick={() => setHistory(null)}>Скрыть историю</button>
          </section>
        )}
      </fieldset>
    </dialog>
  );
}
function HistoryRecord({ entity, value }: { entity: string; value: any }) {
  const values = value.before
    ? [
        ["До изменения", value.before],
        ["После изменения", value.after],
      ]
    : [["Запись", value]];
  return (
    <>
      {values.map(([label, v]) => (
        <div key={label}>
          <strong>{label}</strong>
          {entity === "loan" || (entity === "copy" && v.borrowerName) ? (
            <p>
              {v.title?.title} · {v.inventory} · {v.borrowerName} ·{" "}
              {loanStatuses[v.status]} · до {v.due}
            </p>
          ) : entity === "title" ? (
            <p>
              {v.data?.title} · {v.data?.author} ·{" "}
              {v.archived ? "Архив" : "В каталоге"}
            </p>
          ) : entity === "config" ? (
            <div>
              <p>
                Срок: {v.loanDays} дней. Продлений: {v.maxRenewals}. Книг на
                руках: {v.maxLoans}. Назначений: {v.assignments?.length || 0}.
              </p>
              {v.assignments?.map((a: any) => (
                <p key={a.accountId}>
                  {a.accountName || "Сотрудник"}:{" "}
                  {a.wholeSchool
                    ? "вся школа и фонд"
                    : a.classNames?.join(", ") || "выбранные классы"}
                </p>
              ))}
            </div>
          ) : entity === "copy" ? (
            <p>
              {v.inventory} · {v.location} · {conditions[v.condition]} ·{" "}
              {copyStatuses[v.status]}
            </p>
          ) : (
            <p>
              {v.copy?.inventory || v.title} · {v.result || v.status}{" "}
              {v.note || ""}
            </p>
          )}
        </div>
      ))}
    </>
  );
}
