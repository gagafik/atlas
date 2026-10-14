import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { State } from "./types";
import { named, teacherNames } from "./utils";
import ChangePreview, { type Preview } from "./ChangePreview";

type Connection = {
  server: string;
  local: boolean;
  model?: string;
  models?: string[];
};
type Prepared = {
  token: string;
  revision: number;
  model: string;
  server: string;
  external: boolean;
  payload: unknown;
};
type Answer = {
  answer: string;
  revision: number;
  model: string;
  rejected: string[];
  proposals: { id: string; title: string; explanation: string }[];
};

export default function AssistantPanel({
  state: s,
  workspace,
  onApply,
}: {
  state: State;
  workspace: string;
  onApply: (preview: Preview) => Promise<void>;
}) {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [model, setModel] = useState("");
  const [question, setQuestion] = useState("");
  const [selection, setSelection] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [external, setExternal] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const active = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    api("assistant/status", workspace)
      .then((value) => {
        if (mounted.current) {
          setConnection(value);
          setModel(value.model || "");
        }
      })
      .catch((e) => {
        if (mounted.current) setError(e.message);
      });
    return () => {
      mounted.current = false;
    };
  }, [workspace]);
  async function run(label: string, task: () => Promise<void>) {
    if (active.current) return;
    active.current = true;
    setBusy(label);
    setError("");
    try {
      await task();
    } catch (e: any) {
      if (mounted.current) setError(e.message);
    } finally {
      active.current = false;
      if (mounted.current) setBusy("");
    }
  }
  const stale = prepared && prepared.revision !== s.revision;
  const matches = s.workloads.filter((w) =>
    `${named(s.groups, w.groupId)} ${named(s.subjects, w.subjectId)} ${teacherNames(s, w)}`
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase()),
  );
  return (
    <section className="assistant-panel">
      <div className="assistant-intro">
        <h2>Помощник составителя</h2>
        <p>
          Объясняет данные и предлагает новые ограничения, переносы или обмен
          уроков. Сначала вы проверяете запрос к модели, затем — предложенные
          изменения. Применение проходит обычную проверку расписания.
        </p>
      </div>
      <details className="assistant-connection" open={!model}>
        <summary>
          Подключение модели{" "}
          {connection
            ? `· ${connection.local ? "локальный сервер" : "внешний сервер"}`
            : ""}
        </summary>
        <p>{connection?.server || "Подключение ещё не настроено"}</p>
        <p className="muted">
          Нужен запущенный Ollama с установленной моделью. При открытии этого
          раздела школьные данные не отправляются. Проверка подключения
          запрашивает только список моделей.
        </p>
        <button
          disabled={!!busy}
          onClick={() =>
            void run("Проверяем подключение…", async () => {
              const value = await api(
                "assistant/connection",
                workspace,
                "POST",
              );
              if (!mounted.current) return;
              setConnection(value);
              if (!model && value.models?.length) setModel(value.models[0]);
              if (!value.models?.length)
                setError(
                  "Сервер доступен, но моделей нет. Установите модель в Ollama и проверьте снова.",
                );
            })
          }
        >
          Проверить подключение
        </button>
        <p className="muted">
          Для другого сервера и модели используются ATLAS_AI_URL и
          ATLAS_AI_MODEL при запуске приложения. Настройки описаны в README
          проекта.
        </p>
      </details>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run("Готовим запрос…", async () => {
            if (prepared)
              await api(
                `assistant/request/${prepared.token}`,
                workspace,
                "DELETE",
              ).catch(() => {});
            const value = await api("assistant/prepare", workspace, "POST", {
              revision: s.revision,
              question: question.trim(),
              model,
              workloadIds: selection,
            });
            if (!mounted.current) return;
            setPrepared(value);
            setAnswer(null);
            setPreview(null);
            setExternal(false);
          });
        }}
      >
        <label className="field">
          <span>Модель</span>
          <input
            required
            maxLength={120}
            list="assistant-models"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={!!busy}
            placeholder="Имя установленной модели"
          />
          <datalist id="assistant-models">
            {connection?.models?.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>
        <label className="field">
          <span>Вопрос или пожелание</span>
          <textarea
            required
            maxLength={4000}
            rows={4}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={!!busy}
            placeholder="Например: предложи правило, чтобы выбранный учитель заканчивал занятия до 15:00."
          />
        </label>
        <div className="assistant-examples">
          {[
            "Объясни, какие данные ещё нужны для генерации расписания.",
            "Предложи пожелание: выбранный учитель заканчивает занятия до 15:00.",
            "Как проверить, почему выбранные группы нельзя поставить одновременно?",
          ].map((q) => (
            <button
              key={q}
              type="button"
              disabled={!!busy}
              onClick={() => setQuestion(q)}
            >
              {q}
            </button>
          ))}
        </div>
        <details className="assistant-scope">
          <summary>
            Данные для вопроса · выбрано нагрузок: {selection.length} из 30
          </summary>
          <p className="muted">
            Без выбора модель получает только общие счётчики проверки. Для
            конкретного правила или переноса выберите нагрузки. Перед отправкой
            будут показаны точные данные; составы учеников и учебный журнал не
            включаются.
          </p>
          <label className="field">
            <span>Найти нагрузку</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={!!busy}
              placeholder="Класс, предмет или учитель"
            />
          </label>
          <div className="assistant-loads">
            {matches.slice(0, 100).map((w) => (
              <label key={w.id} className="inline-check">
                <input
                  type="checkbox"
                  checked={selection.includes(w.id)}
                  disabled={
                    !!busy ||
                    (!selection.includes(w.id) && selection.length >= 30)
                  }
                  onChange={(e) =>
                    setSelection(
                      e.target.checked
                        ? [...selection, w.id]
                        : selection.filter((id) => id !== w.id),
                    )
                  }
                />
                {named(s.groups, w.groupId)} · {named(s.subjects, w.subjectId)}{" "}
                · {teacherNames(s, w)}
              </label>
            ))}
          </div>
          {matches.length > 100 && <p>Показаны первые 100. Уточните поиск.</p>}
          <button
            type="button"
            disabled={!!busy}
            onClick={() => setSelection([])}
          >
            Снять выбор
          </button>
        </details>
        <button
          className="primary"
          disabled={!!busy || !question.trim() || !model.trim()}
        >
          Подготовить запрос
        </button>
      </form>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {busy && <p role="status">{busy}</p>}
      {prepared && (
        <section className="assistant-request">
          <h3>Запрос к модели</h3>
          <p>
            {prepared.model} · {prepared.server} · версия {prepared.revision}
          </p>
          <details>
            <summary>Посмотреть точный вопрос и данные</summary>
            <pre>{JSON.stringify(prepared.payload, null, 2)}</pre>
          </details>
          <p className="muted">
            Каждый запрос самостоятельный: предыдущие ответы автоматически не
            отправляются. Запрос и ответ хранятся в памяти сервера до 20 минут.
            Копия вопроса остаётся у выбранного сервера модели согласно его
            настройкам.
          </p>
          {prepared.external && (
            <label className="inline-check">
              <input
                type="checkbox"
                checked={external}
                onChange={(e) => setExternal(e.target.checked)}
                disabled={!!busy || !!answer}
              />
              Разрешаю отправить показанный вопрос и данные внешней модели.
            </label>
          )}
          {stale && (
            <p role="alert" className="generation-warning">
              Расписание изменилось. Подготовьте запрос заново по текущей
              версии.
            </p>
          )}
          {!answer && (
            <button
              disabled={!!busy || !!stale || (prepared.external && !external)}
              onClick={() =>
                void run("Модель готовит ответ…", async () => {
                  const result = await api("assistant/ask", workspace, "POST", {
                    token: prepared.token,
                    allowExternal: external,
                  });
                  if (mounted.current) setAnswer(result);
                })
              }
            >
              Отправить модели
            </button>
          )}
          <button
            disabled={!!busy}
            onClick={() =>
              void run("Очищаем запрос…", async () => {
                await api(
                  `assistant/request/${prepared.token}`,
                  workspace,
                  "DELETE",
                ).catch(() => {});
                setPrepared(null);
                setAnswer(null);
                setPreview(null);
              })
            }
          >
            Очистить запрос и ответ
          </button>
        </section>
      )}
      {answer && (
        <section className="assistant-answer" aria-label="Ответ помощника">
          <h3>Ответ модели</h3>
          <p className="assistant-text">{answer.answer}</p>
          <p className="muted">
            Ответ может содержать ошибки. Текст ответа не меняет расписание;
            каждое предложение проверяется и применяется отдельно.
          </p>
          {answer.rejected.map((text, i) => (
            <p key={i} className="generation-warning">
              {text}
            </p>
          ))}
          {answer.proposals.map((p) => (
            <article key={p.id}>
              <h4>{p.title}</h4>
              <p className="assistant-text">{p.explanation}</p>
              <button
                disabled={!!busy || !!stale}
                onClick={() =>
                  void run("Проверяем предложение…", async () => {
                    const result = await api(
                      "assistant/preview",
                      workspace,
                      "POST",
                      { token: prepared!.token, proposalId: p.id },
                    );
                    if (mounted.current) setPreview(result);
                  })
                }
              >
                Проверить изменения
              </button>
            </article>
          ))}
        </section>
      )}
      {preview && (
        <ChangePreview
          preview={preview}
          busy={!!busy}
          onCancel={() => setPreview(null)}
          onApply={() =>
            run("Применяем проверенные изменения…", async () => {
              await onApply(preview);
              setPreview(null);
            })
          }
          title="Предложение помощника: проверьте изменения"
        />
      )}
    </section>
  );
}
