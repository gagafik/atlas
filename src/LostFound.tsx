import { useEffect, useRef, useState } from "react";
import { uploadFile } from "./uploads";
import { api } from "./api";
import type { Account } from "./auth";
import { displayTime } from "./meetings";
import "./LostFound.css";

type Data = {
  title: string;
  description: string;
  category: string;
  foundOn: string;
  foundPlace: string;
  privateNote?: string;
  pickupInstructions?: string;
  photoIds: string[];
};
type Item = {
  id: string;
  version: number;
  status: string;
  created: string;
  updated: string;
  data: Data;
};
type Claim = {
  id: string;
  version: number;
  status: string;
  created: string;
  updated: string;
  userId?: string;
  ownerCurrent: boolean;
  canWithdraw: boolean;
  pickupInstructions?: string;
  item: Item;
  data: {
    proof: string;
    claimantName: string;
    ownerName: string;
    decision: string;
    receivedBy: string;
    reviewerName: string;
  };
};
type Photo = { id: string; filename: string; size: number; linked: boolean };
type Config = { version: number; accountIds: string[] };
type Options = {
  admin: boolean;
  canManage: boolean;
  canClaim: boolean;
  selfClaim: boolean;
  students: { id: string; name: string }[];
  staff: { id: string; name: string; role: string }[];
  today: string;
  config: Config | null;
};
type History = {
  items: {
    seq: number;
    claimId: string;
    actor: string;
    created: string;
    action: string;
    reason: string;
    data: unknown;
  }[];
  nextBefore: number | null;
};
const categories: Record<string, string> = {
  clothing: "Одежда",
  electronics: "Электроника",
  books: "Книги и тетради",
  keys: "Ключи",
  other: "Другое",
};
const states: Record<string, string> = {
  draft: "Черновик",
  open: "Ищем владельца",
  reserved: "Владелец подтверждён",
  returned: "Передана владельцу",
  archived: "В архиве",
  pending: "Ожидает проверки",
  approved: "Можно получить",
  rejected: "Отклонена",
  withdrawn: "Отозвана",
  collected: "Получена",
};
const actions: Record<string, string> = {
  saved: "Карточка сохранена",
  publish: "Опубликовано",
  withdraw: "Скрыто из каталога",
  archive: "Архивировано",
  configured: "Назначены ответственные",
  claimed: "Подана заявка",
  ...states,
};

function HistoryData({
  value,
  photoURL,
}: {
  value: any;
  photoURL: (id: string) => string;
}) {
  if (value === null || value === undefined) return <p>Нет прежней записи.</p>;
  if (typeof value === "string") return <p>{states[value] || value}</p>;
  if (Array.isArray(value))
    return (
      <p>
        {value
          .map((v) => v.name)
          .filter(Boolean)
          .join(", ") || "Назначенных сотрудников нет."}
      </p>
    );
  if ("before" in value || "after" in value)
    return (
      <>
        <h4>До изменения</h4>
        <HistoryData value={value.before} photoURL={photoURL} />
        <h4>После изменения</h4>
        <HistoryData value={value.after} photoURL={photoURL} />
      </>
    );
  if ("assignments" in value)
    return <HistoryData value={value.assignments} photoURL={photoURL} />;
  const labels: Record<string, string> = {
    title: "Название",
    description: "Описание",
    category: "Категория",
    foundOn: "Дата находки",
    foundPlace: "Место находки",
    privateNote: "Служебные приметы",
    pickupInstructions: "Порядок получения",
    proof: "Доказательство заявителя",
    claimantName: "Заявитель",
    ownerName: "Владелец",
    decision: "Решение",
    receivedBy: "Получил",
    reviewerName: "Проверил",
  };
  return (
    <>
      <dl>
        {Object.entries(labels)
          .filter(([key]) => value[key])
          .map(([key, label]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd className="lf-prose">
                {key === "category"
                  ? categories[value[key]]
                  : String(value[key])}
              </dd>
            </div>
          ))}
      </dl>
      {value.photoIds?.length > 0 && (
        <div className="lf-photos">
          {value.photoIds.map((id: string) => (
            <img key={id} src={photoURL(id)} alt="Фото из истории находки" />
          ))}
        </div>
      )}
    </>
  );
}

export default function LostFound({
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
      : [
          ...new Set([
            ...user.workspaces,
            ...user.grants.map((g) => g.workspace),
          ]),
        ];
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [serverBusy, setServerBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [tab, setTab] = useState("catalog"),
    [query, setQuery] = useState(""),
    [q, setQ] = useState(""),
    [category, setCategory] = useState(""),
    [status, setStatus] = useState("");
  const [items, setItems] = useState<Item[]>([]),
    [next, setNext] = useState<number | null>(null),
    [selected, setSelected] = useState<Item | null>(null);
  const [draft, setDraft] = useState<Item | null>(null),
    [photos, setPhotos] = useState<Photo[]>([]),
    [dirty, setDirty] = useState(false),
    [proof, setProof] = useState(""),
    [student, setStudent] = useState("");
  const [claims, setClaims] = useState<Claim[]>([]),
    [claimNext, setClaimNext] = useState<number | null>(null),
    [claim, setClaim] = useState<Claim | null>(null),
    [claimFilter, setClaimFilter] = useState(""),
    [itemFilter, setItemFilter] = useState("");
  const [config, setConfig] = useState<Config | null>(null),
    [history, setHistory] = useState<History | null>(null),
    [historyId, setHistoryId] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  const unsaved = dirty || Boolean(proof.trim());
  const leave = () =>
    !unsaved ||
    window.confirm(
      "Есть несохранённые изменения. Отбросить их и перейти дальше?",
    );
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (unsaved) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [unsaved]);
  function clear() {
    setSelected(null);
    setDraft(null);
    setPhotos([]);
    setDirty(false);
    setProof("");
    setStudent("");
    setClaim(null);
    setHistory(null);
    setConfig(null);
  }
  function change(fn: () => void) {
    if (leave()) {
      clear();
      fn();
    }
  }
  const itemPath = (before?: number) =>
    `lost-found/items?q=${encodeURIComponent(q)}&category=${category}&status=${status}${before ? "&before=" + before : ""}`;
  const claimsPath = (before?: number) =>
    `lost-found/claims?all=${tab === "queue"}&status=${claimFilter}&itemId=${encodeURIComponent(itemFilter)}${before ? "&before=" + before : ""}`;
  async function load() {
    const n = ++sequence.current;
    const o: Options = await api("lost-found/options", workspace);
    const [list, rows] = await Promise.all([
      api(itemPath(), workspace),
      api(claimsPath(), workspace),
    ]);
    if (n !== sequence.current) return;
    setOptions(o);
    setItems(list.items);
    setNext(list.nextBefore);
    setClaims(rows.items);
    setClaimNext(rows.nextBefore);
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      if ([401, 403, 404].includes(e.status)) {
        clear();
        setOptions(null);
        setItems([]);
        setClaims([]);
      }
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    clear();
    setOptions(null);
    setItems([]);
    setClaims([]);
    void run(load);
    return () => {
      ++sequence.current;
    };
  }, [workspace, tab, q, category, status, claimFilter, itemFilter]);
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api("lost-found/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  async function openItem(item: Item) {
    if (!leave()) return;
    const r = await api("lost-found/items/" + item.id, workspace);
    clear();
    setSelected(r);
  }
  async function editItem() {
    if (!selected || !leave()) return;
    const p = await api(
      "lost-found/items/" + selected.id + "/photos",
      workspace,
    );
    setPhotos(p.items);
    setDraft({ ...selected, data: { ...selected.data } });
    setProof("");
    setDirty(false);
    setHistory(null);
  }
  function updateData(key: keyof Data, value: any) {
    if (draft) {
      setDraft({ ...draft, data: { ...draft.data, [key]: value } });
      setDirty(true);
    }
  }
  async function save() {
    if (!draft) return;
    const reason = draft.id ? window.prompt("Причина изменения карточки") : "";
    if (reason === null) return;
    const r = await mutate(
      "items" + (draft.id ? "/" + draft.id : ""),
      { version: draft.version, data: draft.data, reason },
      draft.id ? "PUT" : "POST",
    );
    clear();
    await load();
    setSelected(r);
    setNotice("Карточка сохранена. Черновик нужно опубликовать отдельно.");
  }
  async function itemAction(action: string) {
    if (!selected || !leave()) return;
    const reason = window.prompt(
      action === "archive"
        ? "Причина закрытия. Ожидающие заявки будут отклонены с этим пояснением."
        : "Причина действия",
    );
    if (!reason) return;
    await run(async () => {
      const r = await mutate(`items/${selected.id}/action`, {
        version: selected.version,
        action,
        reason,
      });
      clear();
      await load();
      setSelected(r);
      setNotice("Состояние находки изменено.");
    });
  }
  async function showHistory(id: string, more = false) {
    const r: History = await api(
      `lost-found/history/${id}${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
      workspace,
    );
    setHistory(
      more && history ? { ...r, items: [...history.items, ...r.items] } : r,
    );
    setHistoryId(id);
  }
  async function upload(file: File) {
    if (!draft?.id) return;
    if (
      !["image/jpeg", "image/png"].includes(file.type) ||
      file.size > 10 * 1024 * 1024
    )
      throw new Error("Выберите JPG или PNG не больше 10 МБ.");
    const data = await uploadFile(
      `/api/lost-found/items/${draft.id}/photos?workspace=${encodeURIComponent(workspace)}&itemVersion=${draft.version}`,
      file,
      {
        contentType: file.type,
        onBusy: setServerBusy,
        fallback: "Не удалось загрузить фото.",
      },
    );
    setPhotos([...photos, data]);
    updateData("photoIds", [...draft.data.photoIds, data.id]);
  }
  async function submitClaim() {
    if (!selected) return;
    const r = await mutate(`items/${selected.id}/claims`, {
      itemVersion: selected.version,
      studentId: options?.selfClaim ? null : student,
      proof,
    });
    setProof("");
    setDirty(false);
    setTab("mine");
    setItemFilter("");
    setClaimFilter("");
    setClaim(r);
    setNotice("Заявка отправлена. Подтверждение появится здесь.");
  }
  async function claimAction(action: string) {
    if (!claim) return;
    const reason = window.prompt(
      action === "collect"
        ? "Как проверены владелец и передача вещи?"
        : "Пояснение решения",
    );
    if (!reason) return;
    const receivedBy =
      action === "collect"
        ? window.prompt("Кому фактически передана вещь? Укажите имя.")
        : "";
    if (receivedBy === null) return;
    await run(async () => {
      const r = await mutate(`claims/${claim.id}/action`, {
        version: claim.version,
        itemVersion: claim.item.version,
        action,
        reason,
        receivedBy,
      });
      await load();
      setClaim(r);
      setHistory(null);
      setNotice("Действие записано.");
    });
  }
  const photoURL = (id: string) =>
    `/api/lost-found/photos/${id}?workspace=${encodeURIComponent(workspace)}`;
  const newItem = () => {
    if (!options) return;
    change(() => {
      setDraft({
        id: "",
        version: 0,
        status: "draft",
        created: "",
        updated: "",
        data: {
          title: "",
          description: "",
          category: "other",
          foundOn: options.today,
          foundPlace: "",
          privateNote: "",
          pickupInstructions: "",
          photoIds: [],
        },
      });
    });
  };
  const queue = (id = "") =>
    change(() => {
      setItemFilter(id);
      setClaimFilter("");
      setTab("queue");
    });
  return (
    <dialog
      className="lost-found"
      ref={dialog}
      aria-labelledby="lost-found-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="lost-found-title">Потерянные вещи</h2>
          <p>Помогаем находкам вернуться к владельцам</p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (leave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <fieldset disabled={busy}>
        <div className="lf-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) =>
                change(() => {
                  setWorkspace(e.target.value);
                  setStatus("");
                  setTab("catalog");
                  setItemFilter("");
                })
              }
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (leave()) {
                clear();
                if (!options && (status || tab !== "catalog")) {
                  setStatus("");
                  setTab("catalog");
                } else void run(load);
              }
            }}
          >
            Обновить
          </button>
        </div>
        <nav className="lf-tabs" aria-label="Разделы потерянных вещей">
          {[
            ["catalog", "Находки"],
            ["mine", "Мои заявки"],
            ...(options?.canManage ? [["queue", "Проверка заявок"]] : []),
            ...(options?.admin ? [["settings", "Ответственные"]] : []),
          ].map(([id, label]) => (
            <button
              key={id}
              aria-pressed={tab === id}
              onClick={() =>
                change(() => {
                  setTab(id);
                  setStatus("");
                  setItemFilter("");
                  setClaimFilter("");
                })
              }
            >
              {label}
            </button>
          ))}
        </nav>
        {options && tab === "catalog" && (
          <>
            <form
              className="lf-controls"
              onSubmit={(e) => {
                e.preventDefault();
                change(() => setQ(query));
              }}
            >
              <label>
                Найти вещь
                <input
                  value={query}
                  maxLength={200}
                  placeholder="Название, описание или место"
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <button>Найти</button>
              <label>
                Категория
                <select
                  value={category}
                  onChange={(e) => change(() => setCategory(e.target.value))}
                >
                  <option value="">Все категории</option>
                  {Object.entries(categories).map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              {options.canManage && (
                <label>
                  Состояние
                  <select
                    value={status}
                    onChange={(e) => change(() => setStatus(e.target.value))}
                  >
                    <option value="">Открытые и подтверждённые</option>
                    {["draft", "open", "reserved", "returned", "archived"].map(
                      (s) => (
                        <option key={s} value={s}>
                          {states[s]}
                        </option>
                      ),
                    )}
                  </select>
                </label>
              )}
            </form>
            {options.canManage && (
              <button onClick={newItem}>Добавить находку</button>
            )}
            <div className="lf-grid">
              <section className="lf-list" aria-label="Список находок">
                {!items.length && <p>По этому фильтру находок нет.</p>}
                {items.map((i) => (
                  <button
                    key={i.id}
                    onClick={() => void run(() => openItem(i))}
                  >
                    <strong>{i.data.title}</strong>
                    <span>
                      {states[i.status]} · {i.data.foundOn}
                    </span>
                    <span>{i.data.foundPlace}</span>
                  </button>
                ))}
                {next && (
                  <button
                    onClick={() =>
                      void run(async () => {
                        const r = await api(itemPath(next), workspace);
                        setItems([...items, ...r.items]);
                        setNext(r.nextBefore);
                      })
                    }
                  >
                    Ещё находки
                  </button>
                )}
              </section>
              <section className="lf-detail">
                {draft ? (
                  <form
                    className="lf-editor"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void run(save);
                    }}
                  >
                    <h3>{draft.id ? "Изменить карточку" : "Новая находка"}</h3>
                    <label>
                      Название
                      <input
                        required
                        minLength={2}
                        maxLength={160}
                        value={draft.data.title}
                        onChange={(e) => updateData("title", e.target.value)}
                      />
                    </label>
                    <div className="lf-controls">
                      <label>
                        Категория
                        <select
                          value={draft.data.category}
                          onChange={(e) =>
                            updateData("category", e.target.value)
                          }
                        >
                          {Object.entries(categories).map(([v, n]) => (
                            <option key={v} value={v}>
                              {n}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Когда найдена
                        <input
                          required
                          type="date"
                          max={options.today}
                          value={draft.data.foundOn}
                          onChange={(e) =>
                            updateData("foundOn", e.target.value)
                          }
                        />
                      </label>
                    </div>
                    <label>
                      Где найдена
                      <input
                        required
                        minLength={2}
                        maxLength={200}
                        value={draft.data.foundPlace}
                        onChange={(e) =>
                          updateData("foundPlace", e.target.value)
                        }
                      />
                    </label>
                    <label>
                      Описание для всей школы
                      <textarea
                        maxLength={2000}
                        value={draft.data.description}
                        onChange={(e) =>
                          updateData("description", e.target.value)
                        }
                      />
                    </label>
                    <p>
                      Оставьте особые приметы для проверки владельца в служебном
                      поле. Фото после публикации видны пользователям школы.
                    </p>
                    <label>
                      Служебные приметы — только ответственным
                      <textarea
                        maxLength={2000}
                        value={draft.data.privateNote || ""}
                        onChange={(e) =>
                          updateData("privateNote", e.target.value)
                        }
                      />
                    </label>
                    <label>
                      Как получить — только подтверждённому заявителю
                      <textarea
                        maxLength={600}
                        value={draft.data.pickupInstructions || ""}
                        onChange={(e) =>
                          updateData("pickupInstructions", e.target.value)
                        }
                      />
                    </label>
                    {draft.id ? (
                      <section>
                        <h4>Фотографии</h4>
                        <p>
                          До трёх фото. Выбранные фотографии появятся после
                          сохранения карточки. Загрузка удаляет метаданные и
                          уменьшает размер.
                        </p>
                        <label>
                          Загрузить фото
                          <input
                            type="file"
                            accept="image/jpeg,image/png"
                            disabled={draft.data.photoIds.length >= 3}
                            onChange={(e) => {
                              const file = e.target.files?.[0];
                              e.target.value = "";
                              if (file) void run(() => upload(file));
                            }}
                          />
                          {serverBusy && (
                            <small role="status">
                              Сервер занят, повторяем…
                            </small>
                          )}
                        </label>
                        <div className="lf-photos">
                          {photos.map((f) => (
                            <div key={f.id}>
                              <img
                                src={photoURL(f.id)}
                                alt="Фото находки для выбора"
                              />
                              <label className="lf-check">
                                <input
                                  type="checkbox"
                                  checked={draft.data.photoIds.includes(f.id)}
                                  disabled={
                                    !draft.data.photoIds.includes(f.id) &&
                                    draft.data.photoIds.length >= 3
                                  }
                                  onChange={(e) =>
                                    updateData(
                                      "photoIds",
                                      e.target.checked
                                        ? [...draft.data.photoIds, f.id]
                                        : draft.data.photoIds.filter(
                                            (id) => id !== f.id,
                                          ),
                                    )
                                  }
                                />
                                Показывать в карточке
                              </label>
                              {!f.linked &&
                                !draft.data.photoIds.includes(f.id) && (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      void run(async () => {
                                        await api(
                                          "lost-found/photos/" + f.id,
                                          workspace,
                                          "DELETE",
                                        );
                                        setPhotos(
                                          photos.filter((p) => p.id !== f.id),
                                        );
                                      })
                                    }
                                  >
                                    Удалить загрузку
                                  </button>
                                )}
                            </div>
                          ))}
                        </div>
                      </section>
                    ) : (
                      <p>Сначала сохраните черновик, затем добавьте фото.</p>
                    )}
                    <div className="lf-controls">
                      <button>Сохранить карточку</button>
                      <button
                        type="button"
                        onClick={() => {
                          if (leave()) {
                            setDraft(null);
                            setDirty(false);
                          }
                        }}
                      >
                        Отмена
                      </button>
                    </div>
                  </form>
                ) : selected ? (
                  <>
                    <h3>{selected.data.title}</h3>
                    <p>
                      {states[selected.status]} · {selected.data.foundOn} ·{" "}
                      {categories[selected.data.category]}
                    </p>
                    <p>Место находки: {selected.data.foundPlace}</p>
                    <p className="lf-prose">{selected.data.description}</p>
                    <div className="lf-photos">
                      {selected.data.photoIds.map((id) => (
                        <img
                          key={id}
                          src={photoURL(id)}
                          alt={selected.data.title}
                        />
                      ))}
                    </div>
                    {options.canManage && (
                      <section className="lf-private">
                        <h4>Для ответственных</h4>
                        <p className="lf-prose">
                          Особые приметы:{" "}
                          {selected.data.privateNote || "Не указаны"}
                        </p>
                        <p className="lf-prose">
                          Получение:{" "}
                          {selected.data.pickupInstructions || "Не указано"}
                        </p>
                        <div className="lf-controls">
                          {["draft", "open"].includes(selected.status) && (
                            <button onClick={() => void run(editItem)}>
                              Изменить карточку
                            </button>
                          )}
                          {selected.status === "draft" && (
                            <button onClick={() => void itemAction("publish")}>
                              Опубликовать
                            </button>
                          )}
                          {selected.status === "open" && (
                            <button onClick={() => void itemAction("withdraw")}>
                              Скрыть из каталога
                            </button>
                          )}
                          {["draft", "open", "returned"].includes(
                            selected.status,
                          ) && (
                            <button onClick={() => void itemAction("archive")}>
                              В архив
                            </button>
                          )}
                          <button onClick={() => queue(selected.id)}>
                            Заявки на эту вещь
                          </button>
                          <button
                            onClick={() =>
                              void run(() => showHistory(selected.id))
                            }
                          >
                            История находки
                          </button>
                        </div>
                      </section>
                    )}
                    {selected.status === "reserved" && (
                      <p>
                        Владелец подтверждён. Новые заявки временно закрыты.
                      </p>
                    )}
                    {options.canClaim && selected.status === "open" && (
                      <form
                        className="lf-claim-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void run(submitClaim);
                        }}
                      >
                        <h4>Это ваша вещь?</h4>
                        {!options.selfClaim && (
                          <label>
                            Владелец
                            <select
                              required
                              value={student}
                              onChange={(e) => setStudent(e.target.value)}
                            >
                              <option value="">Выберите ребёнка</option>
                              {options.students.map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.name}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                        <label>
                          Как подтвердить, что вещь ваша?
                          <textarea
                            required
                            minLength={3}
                            maxLength={2000}
                            value={proof}
                            onChange={(e) => setProof(e.target.value)}
                            placeholder="Опишите особые приметы, которых нет в объявлении"
                          />
                        </label>
                        <p>
                          Ответ увидят только вы и ответственные сотрудники.
                          Отправка заявки ещё не означает подтверждение
                          владельца.
                        </p>
                        <button>Отправить заявку</button>
                      </form>
                    )}
                  </>
                ) : (
                  <p>Выберите находку, чтобы посмотреть подробности.</p>
                )}
              </section>
            </div>
          </>
        )}
        {options && ["mine", "queue"].includes(tab) && (
          <>
            <div className="lf-controls">
              <label>
                Состояние заявки
                <select
                  value={claimFilter}
                  onChange={(e) => change(() => setClaimFilter(e.target.value))}
                >
                  <option value="">Все</option>
                  {[
                    "pending",
                    "approved",
                    "rejected",
                    "withdrawn",
                    "collected",
                  ].map((v) => (
                    <option key={v} value={v}>
                      {states[v]}
                    </option>
                  ))}
                </select>
              </label>
              {itemFilter && (
                <button onClick={() => change(() => setItemFilter(""))}>
                  Показать заявки на все вещи
                </button>
              )}
              <a
                href={`/api/lost-found/export?workspace=${encodeURIComponent(workspace)}&all=${tab === "queue"}&status=${claimFilter}`}
              >
                Скачать CSV по состоянию
              </a>
            </div>
            <div className="lf-grid">
              <section className="lf-list" aria-label="Список заявок">
                {!claims.length && <p>Заявок по этому фильтру нет.</p>}
                {claims.map((c) => (
                  <button
                    key={c.id}
                    onClick={() =>
                      void run(async () => {
                        setClaim(
                          await api("lost-found/claims/" + c.id, workspace),
                        );
                        setHistory(null);
                      })
                    }
                  >
                    <strong>{c.item.data.title}</strong>
                    <span>
                      {c.data.ownerName} · {states[c.status]}
                    </span>
                    <span>{displayTime(c.created)}</span>
                  </button>
                ))}
                {claimNext && (
                  <button
                    onClick={() =>
                      void run(async () => {
                        const r = await api(claimsPath(claimNext), workspace);
                        setClaims([...claims, ...r.items]);
                        setClaimNext(r.nextBefore);
                      })
                    }
                  >
                    Ещё заявки
                  </button>
                )}
              </section>
              {claim && (
                <section className="lf-claim-detail">
                  <h3>{claim.item.data.title}</h3>
                  <p>{states[claim.status]}</p>
                  <p>
                    Владелец: {claim.data.ownerName}. Заявитель:{" "}
                    {claim.data.claimantName}.
                  </p>
                  <h4>Подтверждение владельца</h4>
                  <p className="lf-prose">{claim.data.proof}</p>
                  {options.canManage && (
                    <section className="lf-private">
                      <h4>Служебные приметы находки</h4>
                      <p className="lf-prose">
                        {claim.item.data.privateNote || "Не указаны"}
                      </p>
                    </section>
                  )}
                  {claim.data.decision && (
                    <p className="lf-prose">Решение: {claim.data.decision}</p>
                  )}
                  {claim.pickupInstructions && (
                    <p className="lf-prose">
                      Как получить: {claim.pickupInstructions}
                    </p>
                  )}
                  {claim.data.receivedBy && (
                    <p>Получил: {claim.data.receivedBy}</p>
                  )}
                  {!claim.ownerCurrent && (
                    <p role="alert">
                      Доступ заявителя изменился. Подтвердить или выдать вещь по
                      этой заявке нельзя.
                    </p>
                  )}
                  <div className="lf-controls">
                    {claim.canWithdraw && (
                      <button onClick={() => void claimAction("withdraw")}>
                        Отозвать заявку
                      </button>
                    )}
                    {options.canManage && (
                      <>
                        <button
                          onClick={() =>
                            void run(() => showHistory(claim.item.id))
                          }
                        >
                          История находки
                        </button>
                        {claim.status === "pending" && (
                          <button
                            disabled={
                              !claim.ownerCurrent ||
                              claim.item.status !== "open" ||
                              claim.userId === user.id
                            }
                            onClick={() => void claimAction("approve")}
                          >
                            Подтвердить владельца
                          </button>
                        )}
                        {["pending", "approved"].includes(claim.status) && (
                          <button onClick={() => void claimAction("reject")}>
                            Отклонить заявку
                          </button>
                        )}
                        {claim.status === "approved" && (
                          <>
                            <button onClick={() => void claimAction("reopen")}>
                              Вернуть на проверку
                            </button>
                            <button
                              disabled={
                                !claim.ownerCurrent || claim.userId === user.id
                              }
                              onClick={() => void claimAction("collect")}
                            >
                              Подтвердить передачу вещи
                            </button>
                          </>
                        )}
                      </>
                    )}
                  </div>
                  {options.canManage && (
                    <p>
                      Подтверждение владельца резервирует вещь. Передачу
                      отмечайте только после фактического получения. Собственную
                      заявку проверяет другой ответственный.
                    </p>
                  )}
                </section>
              )}
            </div>
          </>
        )}
        {options?.admin && tab === "settings" && (
          <section className="lf-settings">
            <h3>Ответственные за находки</h3>
            <p>
              Администраторы имеют доступ ко всему разделу. Назначенные
              сотрудники публикуют находки, видят доказательства заявителей и
              подтверждают выдачу. После изменения прав аккаунта назначение
              нужно проверить заново.
            </p>
            {!config ? (
              <>
                <button
                  onClick={() => {
                    setConfig(options.config);
                    setDirty(false);
                  }}
                >
                  Изменить ответственных
                </button>
                <ul>
                  {options.config?.accountIds.map((id) => (
                    <li key={id}>
                      {options.staff.find((s) => s.id === id)?.name ||
                        "Аккаунт больше недоступен"}
                    </li>
                  ))}
                </ul>
                <button onClick={() => void run(() => showHistory("config"))}>
                  История назначений
                </button>
              </>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const reason = window.prompt(
                    "Причина изменения ответственных",
                  );
                  if (reason)
                    void run(async () => {
                      await mutate("config", { ...config, reason }, "PUT");
                      clear();
                      await load();
                      setNotice("Ответственные сохранены.");
                    });
                }}
              >
                {options.staff
                  .filter((s) => s.role !== "admin")
                  .map((s) => (
                    <label key={s.id} className="lf-check">
                      <input
                        type="checkbox"
                        checked={config.accountIds.includes(s.id)}
                        onChange={(e) => {
                          setConfig({
                            ...config,
                            accountIds: e.target.checked
                              ? [...config.accountIds, s.id]
                              : config.accountIds.filter((id) => id !== s.id),
                          });
                          setDirty(true);
                        }}
                      />
                      {s.name} · {s.role}
                    </label>
                  ))}
                <div className="lf-controls">
                  <button>Сохранить ответственных</button>
                  <button
                    type="button"
                    onClick={() => {
                      if (leave()) {
                        setConfig(null);
                        setDirty(false);
                      }
                    }}
                  >
                    Отмена
                  </button>
                </div>
              </form>
            )}
          </section>
        )}
        {history && (
          <section className="lf-history">
            <h3>История</h3>
            {history.items.map((h) => (
              <article key={h.seq}>
                <strong>{actions[h.action] || h.action}</strong>
                <p>
                  {displayTime(h.created)} · {h.actor}
                </p>
                <p className="lf-prose">{h.reason}</p>
                <details>
                  <summary>Сохранённые данные</summary>
                  <HistoryData value={h.data} photoURL={photoURL} />
                </details>
              </article>
            ))}
            {history.nextBefore && (
              <button
                onClick={() => void run(() => showHistory(historyId, true))}
              >
                Ещё история
              </button>
            )}
            <button onClick={() => setHistory(null)}>Скрыть историю</button>
          </section>
        )}
      </fieldset>
    </dialog>
  );
}
