import { useEffect, useRef, useState } from "react";
import { uploadWithRetry } from "./uploads";
import {
  admissionRequest as request,
  admissionResponse,
  admissionLabels as labels,
  recoveryCode,
  mutationId,
  editableApplication,
} from "./admissions";
import type {
  Application,
  ApplicationData,
  Campaign,
  DocumentFile,
  Family,
  HistoryItem,
} from "./admissions";
import {
  ApplicantFields,
  AdmissionHistory,
  SubmittedData,
} from "./AdmissionFields";
import { displayTime } from "./meetings";
import "./Admissions.css";

export default function ApplicantPortal({
  initialCampaign,
}: {
  initialCampaign: string | null;
}) {
  const [family, setFamily] = useState<Family | null>(null),
    [ready, setReady] = useState(false);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]),
    [selected, setSelected] = useState(initialCampaign || "");
  const [cases, setCases] = useState<
    { id: string; childName: string; title: string; status: string }[]
  >([]);
  const [current, setCurrent] = useState<Application | null>(null),
    [draft, setDraft] = useState<ApplicationData | null>(null),
    [files, setFiles] = useState<DocumentFile[]>([]),
    [serverBusy, setServerBusy] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]),
    [moreHistory, setMoreHistory] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [mode, setMode] = useState("login"),
    [login, setLogin] = useState(""),
    [password, setPassword] = useState(""),
    [name, setName] = useState("");
  const [code, setCode] = useState(recoveryCode),
    [savedCode, setSavedCode] = useState(false),
    [oldCode, setOldCode] = useState("");
  const [kind, setKind] = useState(""),
    [reason, setReason] = useState("");
  const pending = useRef<{ payload: string; id: string } | null>(null);
  const dirty =
    !!current &&
    !!draft &&
    JSON.stringify(current.data) !== JSON.stringify(draft);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const leave = () =>
    !dirty ||
    window.confirm(
      "В анкете есть несохранённые изменения. Продолжить без сохранения?",
    );
  function clear() {
    setCurrent(null);
    setDraft(null);
    setFiles([]);
    setHistory([]);
    setCases([]);
    pending.current = null;
  }
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
      if (e.status === 401) {
        setFamily(null);
        clear();
      }
    } finally {
      setBusy(false);
    }
  }
  async function list() {
    setCases((await request("applications")).items);
  }
  useEffect(() => {
    let active = true;
    void Promise.all([
      request("session"),
      request("campaigns"),
      initialCampaign
        ? request("campaigns/" + encodeURIComponent(initialCampaign))
        : Promise.resolve(null),
    ])
      .then(([session, catalog, chosen]) => {
        if (!active) return;
        setFamily(session.user);
        setCampaigns(
          chosen && !catalog.items.some((c: Campaign) => c.id === chosen.id)
            ? [chosen, ...catalog.items]
            : catalog.items,
        );
        if (session.user)
          void request("applications")
            .then((r) => {
              if (active) setCases(r.items);
            })
            .catch((e) => {
              if (active) setError(e.message);
            });
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, [initialCampaign]);
  async function open(id: string) {
    const [item, uploads, log] = await Promise.all([
      request("applications/" + id),
      request("applications/" + id + "/files"),
      request("applications/" + id + "/history"),
    ]);
    setCurrent(item);
    setDraft(item.data);
    setFiles(uploads.items);
    setHistory(log.items);
    setMoreHistory(log.hasMore);
    setKind(item.campaign.data.documents[0]?.id || "");
    setReason("");
    pending.current = null;
  }
  async function mutate(path: string, method: string, data: unknown) {
    pending.current = mutationId(pending.current, { path, method, data });
    const result = await request(path, method, {
      ...(data as object),
      requestId: pending.current.id,
    });
    pending.current = null;
    return result;
  }
  const selectedCampaign = campaigns.find((c) => c.id === selected);
  const editable = !!current && editableApplication(current);
  const fileUrl = (id: string) => "/admissions-api/files/" + id;
  return (
    <main className="admission-ui admission-public">
      <header>
        <div>
          <span className="admission-eyebrow">Атлас · Поступление</span>
          <h1>Кабинет поступающей семьи</h1>
          <p>Подайте анкету и следите за ответами школы здесь.</p>
        </div>
        <a href="/">Вход для учеников и сотрудников</a>
      </header>
      {!ready && <p role="status">Открываю кабинет…</p>}
      {error && (
        <p role="alert" className="admission-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="admission-notice">
          {notice}
        </p>
      )}
      {ready && !family && (
        <div className="admission-layout">
          <section className="admission-card">
            <h2>Приёмные кампании</h2>
            {!campaigns.length && (
              <p>
                Сейчас школа не объявила открытый приём. Если у вас уже есть
                кабинет, войдите, чтобы посмотреть свою заявку.
              </p>
            )}
            <label>
              Куда подать заявку
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                <option value="">Выберите кампанию</option>
                {campaigns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.data.title} · {c.data.year}
                  </option>
                ))}
              </select>
            </label>
            {selectedCampaign && <CampaignInfo campaign={selectedCampaign} />}
          </section>
          <form
            className="admission-card"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const endpoint =
                  mode === "register"
                    ? "register"
                    : mode === "recover"
                      ? "recover"
                      : "login";
                if (mode !== "login" && !savedCode)
                  throw new Error("Сохраните резервный код и подтвердите это.");
                const result = await request(
                  endpoint,
                  "POST",
                  mode === "register"
                    ? {
                        campaignId: selected,
                        login,
                        password,
                        name,
                        recoveryCode: code,
                      }
                    : mode === "recover"
                      ? {
                          login,
                          password,
                          recoveryCode: oldCode,
                          newRecoveryCode: code,
                        }
                      : { login, password },
                );
                setFamily(result.user);
                setPassword("");
                setOldCode("");
                setCode(recoveryCode());
                setSavedCode(false);
                setMode("login");
                await list();
              });
            }}
          >
            <h2>
              {mode === "register"
                ? "Создать кабинет"
                : mode === "recover"
                  ? "Восстановить доступ"
                  : "Войти в кабинет"}
            </h2>
            <p>Кабинет поступления имеет собственные логин и пароль.</p>
            <label>
              Логин
              <input
                required
                autoComplete="username"
                maxLength={64}
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                pattern={
                  mode === "register" ? "[a-z0-9][a-z0-9._\\-]{2,63}" : undefined
                }
              />
            </label>
            {mode === "register" && (
              <>
                <small>
                  От 3 символов: латинские строчные буквы, цифры, точка, дефис
                  или подчёркивание.
                </small>
                <label>
                  Ваше имя
                  <input
                    required
                    maxLength={160}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
              </>
            )}
            {mode === "recover" && (
              <label>
                Сохранённый резервный код
                <input
                  required
                  autoComplete="off"
                  type="password"
                  minLength={32}
                  maxLength={128}
                  value={oldCode}
                  onChange={(e) => setOldCode(e.target.value)}
                />
              </label>
            )}
            <label>
              {mode === "recover" ? "Новый пароль" : "Пароль"}
              <input
                type="password"
                required
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                minLength={mode === "login" ? 1 : 12}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {mode !== "login" && (
              <div className="admission-recovery">
                <strong>
                  Сохраните {mode === "recover" ? "новый " : ""}резервный код
                </strong>
                <p>
                  Он нужен, если вы забудете пароль. Письма для восстановления
                  не отправляются.
                </p>
                <input
                  aria-label="Новый резервный код"
                  readOnly
                  value={code}
                  onFocus={(e) => e.target.select()}
                />
                <label className="admission-check">
                  <input
                    type="checkbox"
                    checked={savedCode}
                    onChange={(e) => setSavedCode(e.target.checked)}
                  />
                  Я сохранил код в надёжном месте
                </label>
              </div>
            )}
            <button
              className="primary"
              disabled={
                busy || (mode === "register" && !selectedCampaign?.accepting)
              }
            >
              {mode === "register"
                ? "Создать кабинет"
                : mode === "recover"
                  ? "Восстановить и войти"
                  : "Войти"}
            </button>
            <div className="admission-actions">
              {[
                ["login", "Уже есть кабинет"],
                ["register", "Новая семья"],
                ["recover", "Забыли пароль?"],
              ]
                .filter(([m]) => m !== mode)
                .map(([m, label]) => (
                  <button
                    key={m}
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setMode(m);
                      setError("");
                      setSavedCode(false);
                      setCode(recoveryCode());
                    }}
                  >
                    {label}
                  </button>
                ))}
            </div>
          </form>
        </div>
      )}
      {family && (
        <>
          <div className="admission-toolbar">
            <strong>
              {family.name} · {family.login}
            </strong>
            <button
              disabled={busy}
              onClick={() => {
                if (leave())
                  void run(async () => {
                    await request("logout", "POST");
                    setFamily(null);
                    clear();
                  });
              }}
            >
              Выйти
            </button>
          </div>
          <div className="admission-layout">
            <aside className="admission-card">
              <h2>Заявки семьи</h2>
              {cases.map((c) => (
                <button
                  className={
                    "admission-case-button" +
                    (current?.id === c.id ? " selected" : "")
                  }
                  key={c.id}
                  disabled={busy}
                  onClick={() => {
                    if (leave()) void run(() => open(c.id));
                  }}
                >
                  <strong>{c.childName || "Новая анкета"}</strong>
                  <small>
                    {c.title} · {labels[c.status]}
                  </small>
                </button>
              ))}
              {!cases.length && (
                <p>
                  Заявок пока нет. Выберите кампанию и создайте анкету ребёнка.
                </p>
              )}
              <label>
                Новая заявка
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                  disabled={busy}
                >
                  <option value="">Выберите кампанию</option>
                  {campaigns
                    .filter((c) => c.accepting)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.data.title} · {c.data.year}
                      </option>
                    ))}
                </select>
              </label>
              <button
                disabled={busy || !selectedCampaign?.accepting}
                onClick={() => {
                  if (leave())
                    void run(async () => {
                      const item = await mutate("applications", "POST", {
                        version: 0,
                        campaignId: selected,
                      });
                      await list();
                      await open(item.id);
                    });
                }}
              >
                Создать анкету ребёнка
              </button>
              <p className="admission-muted">
                Email и телефон указываются в анкете для связи со школой.
                Автоматическая рассылка не подключена; проверяйте решения в
                кабинете.
              </p>
            </aside>
            <section className="admission-card">
              {!current && (
                <>
                  <h2>Выберите заявку</h2>
                  <p>Для каждого ребёнка создайте отдельную анкету.</p>
                  {selectedCampaign && (
                    <CampaignInfo campaign={selectedCampaign} />
                  )}
                </>
              )}
              {current && draft && (
                <>
                  <div className="admission-toolbar">
                    <h2>{current.campaign.data.title}</h2>
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (leave()) void run(() => open(current.id));
                      }}
                    >
                      Обновить заявку
                    </button>
                  </div>
                  <span className="admission-badge">
                    {labels[current.status]}
                  </span>
                  {current.replyUntil && (
                    <p>Срок уточнений: {displayTime(current.replyUntil)}</p>
                  )}
                  <CampaignInfo campaign={current.campaign} compact />
                  {editable ? (
                    <>
                      <ApplicantFields
                        data={draft}
                        campaign={current.campaign}
                        onChange={setDraft}
                        disabled={busy}
                      />
                      <h3>Документы</h3>
                      {current.campaign.data.documents.map((d) => (
                        <p key={d.id}>
                          {d.title}
                          {d.required ? " · обязательно" : " · по желанию"}
                        </p>
                      ))}
                      {!!current.campaign.data.documents.length && (
                        <>
                          <div className="admission-pair">
                            <label>
                              Вид документа
                              <select
                                value={kind}
                                disabled={busy}
                                onChange={(e) => setKind(e.target.value)}
                              >
                                {current.campaign.data.documents.map((d) => (
                                  <option key={d.id} value={d.id}>
                                    {d.title}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label>
                              Добавить PDF, JPG или PNG (до 10 МБ)
                              <input
                                type="file"
                                accept=".pdf,.png,.jpg,.jpeg"
                                disabled={busy || !kind}
                                onChange={(e) => {
                                  const file = e.target.files?.[0];
                                  e.target.value = "";
                                  if (!file) return;
                                  void run(async () => {
                                    if (file.size > 10 * 1024 * 1024)
                                      throw new Error(
                                        "Размер документа не должен превышать 10 МБ.",
                                      );
                                    const result = await admissionResponse(
                                      await uploadWithRetry(
                                        "/admissions-api/applications/" +
                                          current.id +
                                          "/files?applicationVersion=" +
                                          current.version +
                                          "&filename=" +
                                          encodeURIComponent(file.name),
                                        file,
                                        { onBusy: setServerBusy },
                                      ),
                                    );
                                    setFiles((old) => [...old, result]);
                                    setDraft(
                                      (old) =>
                                        old && {
                                          ...old,
                                          files: [
                                            ...old.files,
                                            { id: result.id, kind },
                                          ],
                                        },
                                    );
                                    setNotice(
                                      "Документ загружен. Сохраните анкету, чтобы закрепить выбранные документы.",
                                    );
                                  });
                                }}
                              />
                              {serverBusy && (
                                <small role="status">
                                  Сервер занят, повторяем…
                                </small>
                              )}
                            </label>
                          </div>
                          {files.map((f) => {
                            const chosen = draft.files.find(
                              (x) => x.id === f.id,
                            );
                            return (
                              <div className="admission-file" key={f.id}>
                                <a href={fileUrl(f.id)} download>
                                  {f.filename} · {Math.ceil(f.size / 1024)} КБ
                                </a>
                                <label>
                                  В анкете
                                  <select
                                    disabled={busy}
                                    value={chosen?.kind || ""}
                                    onChange={(e) =>
                                      setDraft({
                                        ...draft,
                                        files: [
                                          ...draft.files.filter(
                                            (x) => x.id !== f.id,
                                          ),
                                          ...(e.target.value
                                            ? [
                                                {
                                                  id: f.id,
                                                  kind: e.target.value,
                                                },
                                              ]
                                            : []),
                                        ],
                                      })
                                    }
                                  >
                                    <option value="">Не включать</option>
                                    {current.campaign.data.documents.map(
                                      (d) => (
                                        <option key={d.id} value={d.id}>
                                          {d.title}
                                        </option>
                                      ),
                                    )}
                                  </select>
                                </label>
                                {!f.linked && !chosen && (
                                  <button
                                    disabled={busy}
                                    onClick={() =>
                                      void run(async () => {
                                        await request(
                                          "files/" + f.id,
                                          "DELETE",
                                        );
                                        setFiles((old) =>
                                          old.filter((x) => x.id !== f.id),
                                        );
                                      })
                                    }
                                  >
                                    Удалить загрузку
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </>
                      )}
                      <div className="admission-actions">
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              await mutate(
                                "applications/" + current.id,
                                "PUT",
                                {
                                  version: current.version,
                                  data: draft,
                                  action: "save",
                                },
                              );
                              await open(current.id);
                              await list();
                              setNotice(
                                "Черновик сохранён. Школа его ещё не рассматривает.",
                              );
                            })
                          }
                        >
                          Сохранить черновик
                        </button>
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              await mutate(
                                "applications/" + current.id,
                                "PUT",
                                {
                                  version: current.version,
                                  data: draft,
                                  action: "submit",
                                },
                              );
                              await open(current.id);
                              await list();
                              setNotice(
                                "Заявка подана. Решение появится в этом кабинете.",
                              );
                            })
                          }
                        >
                          {current.status === "returned"
                            ? "Отправить уточнения"
                            : "Подать заявку"}
                        </button>
                        <span>
                          {dirty ? "Есть несохранённые изменения" : "Сохранено"}
                        </span>
                      </div>
                    </>
                  ) : (
                    <SubmittedData
                      data={current.data}
                      campaign={current.campaign}
                      fileUrl={fileUrl}
                    />
                  )}
                  {current.status !== "withdrawn" && (
                    <details>
                      <summary>Отозвать заявку</summary>
                      <label>
                        Причина отзыва
                        <textarea
                          value={reason}
                          maxLength={2000}
                          onChange={(e) => setReason(e.target.value)}
                        />
                      </label>
                      <button
                        disabled={busy || !reason.trim()}
                        onClick={() => {
                          if (
                            leave() &&
                            window.confirm(
                              "Отозвать эту заявку? Её рассмотрение будет прекращено.",
                            )
                          )
                            void run(async () => {
                              await mutate(
                                "applications/" + current.id + "/withdraw",
                                "POST",
                                { version: current.version, reason },
                              );
                              await open(current.id);
                              await list();
                            });
                        }}
                      >
                        Подтвердить отзыв
                      </button>
                    </details>
                  )}
                  <h3>Ответы школы и история</h3>
                  <AdmissionHistory
                    items={history}
                    campaign={current.campaign}
                    fileUrl={fileUrl}
                  />
                  {!history.length && (
                    <p>После подачи здесь появятся ответы школы.</p>
                  )}
                  {moreHistory && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const r = await request(
                            "applications/" +
                              current.id +
                              "/history?offset=" +
                              history.length,
                          );
                          setHistory((old) => [...old, ...r.items]);
                          setMoreHistory(r.hasMore);
                        })
                      }
                    >
                      Показать более ранние записи
                    </button>
                  )}
                </>
              )}
            </section>
          </div>
        </>
      )}
    </main>
  );
}

export function CampaignInfo({
  campaign,
  compact = false,
}: {
  campaign: Campaign;
  compact?: boolean;
}) {
  return (
    <div className="admission-campaign-info">
      <p>
        <strong>{campaign.data.year}</strong> · {labels[campaign.status]}
      </p>
      <p>
        Приём: {displayTime(campaign.data.opensAt)} —{" "}
        {displayTime(campaign.data.closesAt)} (время школы)
      </p>
      {!compact && <p className="admission-pre">{campaign.data.description}</p>}
      <p className="admission-pre">
        Связаться со школой: {campaign.data.contact}
      </p>
      {!compact && (
        <details>
          <summary>Условия обработки сведений</summary>
          <p className="admission-pre">{campaign.data.privacyNotice}</p>
        </details>
      )}
    </div>
  );
}
