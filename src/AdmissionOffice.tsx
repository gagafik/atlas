import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import type {
  Application,
  Campaign,
  CampaignData,
  HistoryItem,
} from "./admissions";
import { admissionLabels as labels, mutationId } from "./admissions";
import { AdmissionHistory, SubmittedData } from "./AdmissionFields";
import { displayTime, schoolInput, schoolISO } from "./meetings";
import "./Admissions.css";

function blank(): CampaignData {
  return {
    title: "",
    year: String(new Date().getFullYear() + 1),
    description: "",
    contact: "",
    privacyNotice: "",
    grades: [1],
    opensAt: new Date().toISOString(),
    closesAt: new Date(Date.now() + 30 * 86400000).toISOString(),
    documents: [],
    questions: [],
  };
}
type Summary = {
  id: string;
  status: string;
  childName: string;
  grade: number;
  updated: string;
};
export default function AdmissionOffice({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const workspaces = [
    ...new Set(
      user.role === "admin"
        ? ["school", "demo"]
        : [...user.workspaces, ...user.grants.map((g) => g.workspace)],
    ),
  ];
  return (
    <div className="modal-backdrop">
      <section
        className="admission-ui admission-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Приём в школу"
      >
        <AdmissionsWorkspace
          key={workspace}
          workspace={workspace}
          workspaces={workspaces}
          onWorkspace={setWorkspace}
          onClose={onClose}
        />
      </section>
    </div>
  );
}
function AdmissionsWorkspace({
  workspace,
  workspaces,
  onWorkspace,
  onClose,
}: {
  workspace: string;
  workspaces: string[];
  onWorkspace: (v: string) => void;
  onClose: () => void;
}) {
  const [options, setOptions] = useState<{
    canManage: boolean;
    staff: { id: string; name: string; role: string }[];
  } | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]),
    [moreCampaigns, setMoreCampaigns] = useState(false),
    [campaignPage, setCampaignPage] = useState(0);
  const [campaign, setCampaign] = useState<Campaign | null>(null),
    [editing, setEditing] = useState(false),
    [data, setData] = useState<CampaignData>(blank),
    [baseline, setBaseline] = useState("");
  const [reviewers, setReviewers] = useState<string[]>([]),
    [assigned, setAssigned] = useState<string[]>([]);
  const [cases, setCases] = useState<Summary[]>([]),
    [moreCases, setMoreCases] = useState(false),
    [page, setPage] = useState(0),
    [counts, setCounts] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState(""),
    [search, setSearch] = useState(""),
    [query, setQuery] = useState("");
  const [current, setCurrent] = useState<Application | null>(null),
    [history, setHistory] = useState<HistoryItem[]>([]),
    [moreHistory, setMoreHistory] = useState(false);
  const [action, setAction] = useState("in_review"),
    [message, setMessage] = useState(""),
    [replyUntil, setReplyUntil] = useState(
      schoolInput(new Date(Date.now() + 7 * 86400000).toISOString()),
    );
  const [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const pending = useRef<{ payload: string; id: string } | null>(null);
  const dirty =
    (editing && JSON.stringify(data) !== baseline) ||
    JSON.stringify(reviewers) !== JSON.stringify(assigned) ||
    !!message;
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const leave = () =>
    !dirty ||
    window.confirm("Есть несохранённые изменения. Продолжить без сохранения?");
  const call = (path: string, method = "GET", body?: unknown) =>
    api("admissions/" + path, workspace, method, body);
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function mutate(path: string, method: string, body: object) {
    pending.current = mutationId(pending.current, {
      workspace,
      path,
      method,
      body,
    });
    const result = await call(path, method, {
      ...body,
      requestId: pending.current.id,
    });
    pending.current = null;
    return result;
  }
  async function listing(offset = campaignPage) {
    const r = await call("campaigns?offset=" + offset);
    setCampaigns(r.items);
    setMoreCampaigns(r.hasMore);
    setCampaignPage(offset);
  }
  async function caseList(
    id: string,
    offset = page,
    status = filter,
    text = query,
  ) {
    const r = await call(
      "campaigns/" +
        id +
        "/applications?offset=" +
        offset +
        "&status=" +
        status +
        "&q=" +
        encodeURIComponent(text),
    );
    setCases(r.items);
    setMoreCases(r.hasMore);
    setCounts(r.counts);
    setPage(offset);
  }
  async function openCampaign(id: string) {
    const r = await call("campaigns/" + id);
    setCampaign(r);
    setData(r.data);
    setBaseline(JSON.stringify(r.data));
    const ids = (r.reviewers || []).map((a: { id: string }) => a.id);
    setReviewers(ids);
    setAssigned(ids);
    setEditing(false);
    setCurrent(null);
    setMessage("");
    setReason("");
    pending.current = null;
    setFilter("");
    setSearch("");
    setQuery("");
    await caseList(id, 0, "", "");
  }
  async function openCase(id: string) {
    const [r, log] = await Promise.all([
      call("applications/" + id),
      call("applications/" + id + "/history"),
    ]);
    setCurrent(r);
    setHistory(log.items);
    setMoreHistory(log.hasMore);
    setMessage("");
    setAction(
      ["accepted", "rejected"].includes(r.status)
        ? "note"
        : r.status === "returned"
          ? "returned"
          : r.status === "withdrawn"
            ? "note"
            : "in_review",
    );
    pending.current = null;
  }
  useEffect(() => {
    let active = true;
    void Promise.all([call("options"), call("campaigns")])
      .then(([o, c]) => {
        if (active) {
          setOptions(o);
          setCampaigns(c.items);
          setMoreCampaigns(c.hasMore);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [workspace]); // Component resets when the workspace changes.
  const fileUrl = (id: string) =>
    "/api/admissions/files/" + id + "?workspace=" + workspace;
  return (
    <>
      <header>
        <div>
          <span className="admission-eyebrow">Атлас · Приёмная комиссия</span>
          <h1>Приём в школу</h1>
        </div>
        <div className="admission-actions">
          <label>
            Пространство
            <select
              value={workspace}
              disabled={busy}
              onChange={(e) => {
                if (leave()) onWorkspace(e.target.value);
              }}
            >
              {workspaces.map((w) => (
                <option value={w} key={w}>
                  {w === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={busy}
            onClick={() => {
              if (leave()) onClose();
            }}
          >
            Закрыть
          </button>
        </div>
      </header>
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
      <div className="admission-layout">
        <aside className="admission-card">
          <h2>Кампании</h2>
          {options?.canManage && (
            <button
              disabled={busy}
              onClick={() => {
                if (!leave()) return;
                const next = blank();
                setCampaign(null);
                setData(next);
                setBaseline(JSON.stringify(next));
                setEditing(true);
                setCurrent(null);
                setReviewers([]);
                setAssigned([]);
                setMessage("");
                setReason("");
              }}
            >
              Новая кампания
            </button>
          )}
          {campaigns.map((c) => (
            <button
              className={
                "admission-case-button" +
                (campaign?.id === c.id ? " selected" : "")
              }
              key={c.id}
              disabled={busy}
              onClick={() => {
                if (leave()) void run(() => openCampaign(c.id));
              }}
            >
              <strong>{c.data.title}</strong>
              <small>
                {c.data.year} · {labels[c.status]}
              </small>
            </button>
          ))}
          {options && !campaigns.length && (
            <p>
              {options.canManage
                ? "Создайте кампанию, чтобы объявить приём."
                : "Администратор назначает сотрудников отдельно для каждой кампании."}
            </p>
          )}
          <div className="admission-actions">
            <button
              disabled={busy || !campaignPage}
              onClick={() =>
                void run(() => listing(Math.max(0, campaignPage - 30)))
              }
            >
              Назад
            </button>
            <button
              disabled={busy || !moreCampaigns}
              onClick={() => void run(() => listing(campaignPage + 30))}
            >
              Далее
            </button>
          </div>
        </aside>
        <div className="admission-main">
          {editing && (
            <form
              className="admission-card"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  const r = await mutate(
                    "campaigns" + (campaign ? "/" + campaign.id : ""),
                    campaign ? "PUT" : "POST",
                    { version: campaign?.version || 0, data },
                  );
                  await listing();
                  await openCampaign(r.id);
                  setNotice(
                    "Кампания сохранена. Назначьте комиссию и откройте приём.",
                  );
                });
              }}
            >
              <h2>
                {campaign
                  ? "Редактировать черновик"
                  : "Новая приёмная кампания"}
              </h2>
              <fieldset disabled={busy}>
                <legend>Условия приёма</legend>
                <label>
                  Название
                  <input
                    required
                    maxLength={160}
                    value={data.title}
                    onChange={(e) =>
                      setData({ ...data, title: e.target.value })
                    }
                  />
                </label>
                <label>
                  Учебный год
                  <input
                    required
                    maxLength={40}
                    value={data.year}
                    onChange={(e) => setData({ ...data, year: e.target.value })}
                  />
                </label>
                <div className="admission-pair">
                  <label>
                    Начало приёма, время школы
                    <input
                      required
                      type="datetime-local"
                      value={schoolInput(data.opensAt)}
                      onChange={(e) => {
                        if (e.target.value)
                          setData({
                            ...data,
                            opensAt: schoolISO(e.target.value),
                          });
                      }}
                    />
                  </label>
                  <label>
                    Окончание приёма, время школы
                    <input
                      required
                      type="datetime-local"
                      value={schoolInput(data.closesAt)}
                      onChange={(e) => {
                        if (e.target.value)
                          setData({
                            ...data,
                            closesAt: schoolISO(e.target.value),
                          });
                      }}
                    />
                  </label>
                </div>
                <fieldset>
                  <legend>Параллели</legend>
                  <div className="admission-actions">
                    {Array.from({ length: 12 }, (_, g) => (
                      <label className="admission-check" key={g}>
                        <input
                          type="checkbox"
                          checked={data.grades.includes(g)}
                          onChange={(e) =>
                            setData({
                              ...data,
                              grades: e.target.checked
                                ? [...data.grades, g].sort((a, b) => a - b)
                                : data.grades.filter((x) => x !== g),
                            })
                          }
                        />
                        {g}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <label>
                  Описание для семьи
                  <textarea
                    maxLength={5000}
                    value={data.description}
                    onChange={(e) =>
                      setData({ ...data, description: e.target.value })
                    }
                  />
                </label>
                <label>
                  Контакты приёмной комиссии
                  <textarea
                    required
                    maxLength={1000}
                    value={data.contact}
                    onChange={(e) =>
                      setData({ ...data, contact: e.target.value })
                    }
                  />
                </label>
                <label>
                  Условия обработки сведений и документов
                  <textarea
                    required
                    maxLength={5000}
                    value={data.privacyNotice}
                    onChange={(e) =>
                      setData({ ...data, privacyNotice: e.target.value })
                    }
                  />
                </label>
                <h3>Документы</h3>
                {data.documents.map((d, i) => (
                  <div className="admission-editor-row" key={d.id}>
                    <label>
                      Название документа {i + 1}
                      <input
                        required
                        maxLength={160}
                        value={d.title}
                        onChange={(e) =>
                          setData({
                            ...data,
                            documents: data.documents.map((x, j) =>
                              i === j ? { ...x, title: e.target.value } : x,
                            ),
                          })
                        }
                      />
                    </label>
                    <label className="admission-check">
                      <input
                        type="checkbox"
                        checked={d.required}
                        onChange={(e) =>
                          setData({
                            ...data,
                            documents: data.documents.map((x, j) =>
                              i === j
                                ? { ...x, required: e.target.checked }
                                : x,
                            ),
                          })
                        }
                      />
                      Обязательный
                    </label>
                    <button
                      type="button"
                      onClick={() =>
                        setData({
                          ...data,
                          documents: data.documents.filter((_, j) => j !== i),
                        })
                      }
                    >
                      Убрать документ
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  disabled={data.documents.length >= 8}
                  onClick={() =>
                    setData({
                      ...data,
                      documents: [
                        ...data.documents,
                        {
                          id: crypto.randomUUID().replaceAll("-", ""),
                          title: "",
                          required: true,
                        },
                      ],
                    })
                  }
                >
                  Добавить вид документа
                </button>
                <h3>Дополнительные вопросы</h3>
                {data.questions.map((q, i) => (
                  <fieldset key={q.id}>
                    <legend>Вопрос {i + 1}</legend>
                    <label>
                      Текст вопроса
                      <input
                        required
                        maxLength={300}
                        value={q.title}
                        onChange={(e) =>
                          setData({
                            ...data,
                            questions: data.questions.map((x, j) =>
                              i === j ? { ...x, title: e.target.value } : x,
                            ),
                          })
                        }
                      />
                    </label>
                    <label>
                      Тип ответа
                      <select
                        value={q.kind}
                        onChange={(e) =>
                          setData({
                            ...data,
                            questions: data.questions.map((x, j) =>
                              i === j
                                ? {
                                    ...x,
                                    kind: e.target.value as typeof x.kind,
                                    choices: [],
                                  }
                                : x,
                            ),
                          })
                        }
                      >
                        {[
                          ["text", "Текст"],
                          ["single", "Один вариант"],
                          ["multiple", "Несколько вариантов"],
                          ["date", "Дата"],
                          ["number", "Число"],
                          ["consent", "Подтверждение"],
                        ].map(([v, label]) => (
                          <option key={v} value={v}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    {["single", "multiple"].includes(q.kind) && (
                      <label>
                        Варианты, по одному в строке
                        <textarea
                          value={q.choices.join("\n")}
                          onChange={(e) =>
                            setData({
                              ...data,
                              questions: data.questions.map((x, j) =>
                                i === j
                                  ? {
                                      ...x,
                                      choices: e.target.value.split("\n"),
                                    }
                                  : x,
                              ),
                            })
                          }
                        />
                      </label>
                    )}
                    <label className="admission-check">
                      <input
                        type="checkbox"
                        checked={q.required}
                        onChange={(e) =>
                          setData({
                            ...data,
                            questions: data.questions.map((x, j) =>
                              i === j
                                ? { ...x, required: e.target.checked }
                                : x,
                            ),
                          })
                        }
                      />
                      Обязательный
                    </label>
                    <button
                      type="button"
                      onClick={() =>
                        setData({
                          ...data,
                          questions: data.questions.filter((_, j) => j !== i),
                        })
                      }
                    >
                      Убрать вопрос
                    </button>
                  </fieldset>
                ))}
                <button
                  type="button"
                  disabled={data.questions.length >= 20}
                  onClick={() =>
                    setData({
                      ...data,
                      questions: [
                        ...data.questions,
                        {
                          id: crypto.randomUUID(),
                          title: "",
                          kind: "text",
                          required: true,
                          choices: [],
                        },
                      ],
                    })
                  }
                >
                  Добавить вопрос
                </button>
              </fieldset>
              <p>
                После открытия приёма условия анкеты фиксируются. Закрывать
                приём и менять состав комиссии можно отдельно.
              </p>
              <button className="primary" disabled={busy}>
                Сохранить кампанию
              </button>
            </form>
          )}
          {!editing && campaign && (
            <>
              <section className="admission-card">
                <div className="admission-toolbar">
                  <h2>
                    {campaign.data.title} · {campaign.data.year}
                  </h2>
                  <span className="admission-badge">
                    {labels[campaign.status]}
                  </span>
                </div>
                <p>
                  Приём: {displayTime(campaign.data.opensAt)} —{" "}
                  {displayTime(campaign.data.closesAt)}
                </p>
                <p className="admission-pre">{campaign.data.description}</p>
                {campaign.published && (
                  <p>
                    <a
                      href={"/?apply=" + campaign.id}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Открыть страницу для семей
                    </a>{" "}
                    · эту ссылку можно отправить поступающим
                  </p>
                )}
                {options?.canManage && (
                  <details>
                    <summary>Настройки кампании и комиссия</summary>
                    {!campaign.published && campaign.status === "draft" && (
                      <button
                        disabled={busy}
                        onClick={() => {
                          if (leave()) setEditing(true);
                        }}
                      >
                        Редактировать черновик
                      </button>
                    )}
                    <fieldset disabled={busy}>
                      <legend>Назначенные сотрудники</legend>
                      {options.staff.map((p) => (
                        <label className="admission-check" key={p.id}>
                          <input
                            type="checkbox"
                            checked={reviewers.includes(p.id)}
                            onChange={(e) =>
                              setReviewers((old) =>
                                e.target.checked
                                  ? [...old, p.id]
                                  : old.filter((x) => x !== p.id),
                              )
                            }
                          />
                          {p.name} · {p.role}
                        </label>
                      ))}
                      {campaign.reviewers?.some((r) => !r.current) && (
                        <p>
                          Часть назначений потеряла доступ. Проверьте список и
                          назначьте действующих сотрудников заново.
                        </p>
                      )}
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await mutate(
                              "campaigns/" + campaign.id + "/reviewers",
                              "PUT",
                              {
                                version: campaign.version,
                                accountIds: reviewers.filter((id) =>
                                  options.staff.some((p) => p.id === id),
                                ),
                              },
                            );
                            await openCampaign(campaign.id);
                            setNotice("Состав комиссии обновлён.");
                          })
                        }
                      >
                        Сохранить комиссию
                      </button>
                    </fieldset>
                    {campaign.status !== "archived" && (
                      <>
                        <label>
                          Причина изменения приёма
                          <input
                            maxLength={1000}
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                          />
                        </label>
                        <div className="admission-actions">
                          {[
                            ["open", "Открыть приём"],
                            ["close", "Закрыть приём"],
                            ["archive", "В архив"],
                          ]
                            .filter(
                              ([a]) =>
                                a !== campaign.status &&
                                !(
                                  a === "close" && campaign.status !== "open"
                                ),
                            )
                            .map(([a, label]) => (
                              <button
                                key={a}
                                disabled={busy || !reason.trim()}
                                onClick={() => {
                                  if (dirty && !leave()) return;
                                  if (
                                    a === "open" &&
                                    !window.confirm(
                                      "Опубликовать условия и открыть регистрацию для поступающих семей? После этого анкету нельзя изменить.",
                                    )
                                  )
                                    return;
                                  void run(async () => {
                                    await mutate(
                                      "campaigns/" + campaign.id + "/action",
                                      "POST",
                                      {
                                        version: campaign.version,
                                        action: a,
                                        reason,
                                      },
                                    );
                                    await listing();
                                    await openCampaign(campaign.id);
                                  });
                                }}
                              >
                                {label}
                              </button>
                            ))}
                        </div>
                      </>
                    )}
                  </details>
                )}
              </section>
              <section className="admission-card">
                <div className="admission-toolbar">
                  <h2>Поданные заявки</h2>
                  <a
                    href={
                      "/api/admissions/campaigns/" +
                      campaign.id +
                      "/export?workspace=" +
                      workspace
                    }
                    download
                  >
                    Скачать CSV
                  </a>
                </div>
                <p>
                  {Object.entries(counts)
                    .map(([key, count]) => `${labels[key]}: ${count}`)
                    .join(" · ") || "Поданных заявок пока нет"}
                </p>
                <form
                  className="admission-actions"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      setQuery(search);
                      await caseList(campaign.id, 0, filter, search);
                    });
                  }}
                >
                  <label>
                    Статус
                    <select
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    >
                      <option value="">Все поданные</option>
                      {[
                        "submitted",
                        "in_review",
                        "returned",
                        "waitlisted",
                        "accepted",
                        "rejected",
                        "withdrawn",
                      ].map((k) => (
                        <option key={k} value={k}>
                          {labels[k]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Ребёнок, представитель или контакт
                    <input
                      value={search}
                      maxLength={100}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </label>
                  <button disabled={busy}>Найти</button>
                </form>
                <div className="admission-case-grid">
                  {cases.map((c) => (
                    <button
                      className={
                        "admission-case-button" +
                        (current?.id === c.id ? " selected" : "")
                      }
                      disabled={busy}
                      key={c.id}
                      onClick={() => {
                        if (leave()) void run(() => openCase(c.id));
                      }}
                    >
                      <strong>{c.childName}</strong>
                      <small>
                        {c.grade} класс · {labels[c.status]}
                      </small>
                    </button>
                  ))}
                </div>
                <div className="admission-actions">
                  <button
                    disabled={busy || !page}
                    onClick={() =>
                      void run(() =>
                        caseList(campaign.id, Math.max(0, page - 30)),
                      )
                    }
                  >
                    Предыдущие заявки
                  </button>
                  <button
                    disabled={busy || !moreCases}
                    onClick={() =>
                      void run(() => caseList(campaign.id, page + 30))
                    }
                  >
                    Следующие заявки
                  </button>
                </div>
              </section>
              {current && (
                <section className="admission-card">
                  <div className="admission-toolbar">
                    <h2>{current.data.childName}</h2>
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (leave()) void run(() => openCase(current.id));
                      }}
                    >
                      Обновить заявку
                    </button>
                  </div>
                  <p>
                    <strong>{labels[current.status]}</strong> · Подано{" "}
                    {current.submittedAt
                      ? displayTime(current.submittedAt)
                      : ""}
                  </p>
                  {current.replyUntil && (
                    <p>Срок уточнений: {displayTime(current.replyUntil)}</p>
                  )}
                  <SubmittedData
                    data={current.data}
                    campaign={current.campaign}
                    fileUrl={fileUrl}
                  />
                  {campaign.status !== "archived" && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(async () => {
                          await mutate(
                            "applications/" + current.id + "/review",
                            "POST",
                            {
                              version: current.version,
                              action,
                              message,
                              replyUntil:
                                action === "returned"
                                  ? schoolISO(replyUntil)
                                  : null,
                            },
                          );
                          await openCase(current.id);
                          await caseList(campaign.id);
                          setNotice(
                            action === "note"
                              ? "Заметка сохранена только для комиссии. Семья её не видит."
                              : "Изменение сохранено. Ответ семье доступен в её кабинете.",
                          );
                        });
                      }}
                    >
                      <h3>Рассмотрение</h3>
                      <label>
                        Действие
                        <select
                          value={action}
                          onChange={(e) => setAction(e.target.value)}
                        >
                          {[
                            "note",
                            "message",
                            ...([
                              "submitted",
                              "in_review",
                              "waitlisted",
                            ].includes(current.status)
                              ? [
                                  "in_review",
                                  "returned",
                                  "waitlisted",
                                  ...(options?.canManage
                                    ? ["accepted", "rejected"]
                                    : []),
                                ]
                              : current.status === "returned"
                                ? ["returned"]
                                : options?.canManage &&
                                    ["accepted", "rejected"].includes(
                                      current.status,
                                    )
                                  ? ["reopen"]
                                  : []),
                          ].map((a) => (
                            <option key={a} value={a}>
                              {labels[a]}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {action === "note"
                          ? "Заметка только для комиссии"
                          : "Объяснение или сообщение для семьи"}
                        <textarea
                          required
                          maxLength={4000}
                          value={message}
                          onChange={(e) => setMessage(e.target.value)}
                        />
                      </label>
                      {action === "returned" && (
                        <label>
                          Уточнения до, время школы
                          <input
                            required
                            type="datetime-local"
                            value={replyUntil}
                            onChange={(e) => setReplyUntil(e.target.value)}
                          />
                        </label>
                      )}
                      <button
                        className="primary"
                        disabled={busy || !message.trim()}
                      >
                        Сохранить результат
                      </button>
                    </form>
                  )}
                  <h3>История</h3>
                  <AdmissionHistory
                    items={history}
                    campaign={current.campaign}
                    fileUrl={fileUrl}
                  />
                  {moreHistory && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const r = await call(
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
                </section>
              )}
            </>
          )}
          {!editing && !campaign && (
            <section className="admission-card">
              <h2>От анкеты до решения</h2>
              <p>
                Откройте кампанию, чтобы увидеть поданные заявки и документы.
                Черновики семьи доступны только самой семье.
              </p>
              <p>
                Положительное решение сохраняется здесь. Создание ученика и
                школьных аккаунтов выполняется отдельно.
              </p>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
