import { useEffect, useState } from "react";
import { api } from "./api";
import {
  CALENDAR_TYPES,
  METHODS,
  MISSING,
  configProblem,
  staleVersion,
  type Category,
  type CategoryConfig,
  type Draft,
  type Policy,
  type Rule,
} from "./assessmentCategories";

const SLUG = /^[a-z0-9-]{1,40}$/;
const total = (p: Policy) =>
  p.shares.reduce(
    (sum, s) => sum + Math.round(Number(s.percent || 0) * 100),
    0,
  ) / 100;
const shareOf = (p: Policy, id: string) =>
  String(p.shares.find((s) => s.categoryId === id)?.percent ?? "");
// An erased field means "no share", the same as zero.
function withShare(p: Policy, id: string, percent: string): Policy {
  const rest = p.shares.filter((s) => s.categoryId !== id);
  const at = p.shares.findIndex((s) => s.categoryId === id);
  if (percent === "") return { ...p, shares: rest };
  const shares = [...rest];
  shares.splice(at < 0 ? rest.length : at, 0, { categoryId: id, percent });
  return { ...p, shares };
}
const fromConfig = (c: CategoryConfig): Draft => ({
  categories: structuredClone(c.categories),
  defaultCategory: c.defaultCategory,
  policy: structuredClone(
    c.policy ?? { shares: [], method: "average", missing: "renormalize" },
  ),
  rules: structuredClone(c.rules ?? []),
});

// Shares, method and missing-category mode of the school policy or of one rule.
function PolicyFields({
  policy,
  categories,
  onChange,
}: {
  policy: Policy;
  categories: Category[];
  onChange: (p: Policy) => void;
}) {
  return (
    <>
      <div className="category-grid">
        {categories.map((c) => (
          <label className="field" key={c.id}>
            Доля «{c.name}», %
            <input
              type="number"
              min="0"
              max="100"
              step="0.01"
              value={shareOf(policy, c.id)}
              placeholder="0"
              onChange={(e) =>
                onChange(withShare(policy, c.id, e.target.value))
              }
            />
          </label>
        ))}
      </div>
      <p className={total(policy) === 100 ? "muted" : "error"}>
        Сумма долей: {total(policy)} % (нужно 100 %).
      </p>
      <div className="category-grid">
        <label className="field">
          Как считать категорию
          <select
            value={policy.method}
            onChange={(e) =>
              onChange({
                ...policy,
                method: e.target.value as Policy["method"],
              })
            }
          >
            {METHODS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Если в категории нет оценок
          <select
            value={policy.missing}
            onChange={(e) =>
              onChange({
                ...policy,
                missing: e.target.value as Policy["missing"],
              })
            }
          >
            {MISSING.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </>
  );
}

function Picks({
  label,
  items,
  chosen,
  onChange,
}: {
  label: string;
  items: { id: string; name: string }[];
  chosen: string[];
  onChange: (ids: string[]) => void;
}) {
  return (
    <fieldset className="category-picks">
      <legend>{label} (пусто — все)</legend>
      {items.map((i) => (
        <label className="check-line" key={i.id}>
          <input
            type="checkbox"
            checked={chosen.includes(i.id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...chosen, i.id]
                  : chosen.filter((x) => x !== i.id),
              )
            }
          />
          {i.name}
        </label>
      ))}
    </fieldset>
  );
}

// Admin editor of the school's work categories, the share policy and the rules that override it per subject/class.
export default function AssessmentCategoriesEditor({
  workspace,
  config,
  onSaved,
}: {
  workspace: string;
  config: CategoryConfig | null;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null),
    [reason, setReason] = useState(""),
    [slug, setSlug] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  useEffect(() => {
    if (config) setDraft(fromConfig(config));
  }, [config]);
  if (!config?.canEdit || !draft) return null;
  const known = new Set(config.categories.map((c) => c.id));
  const problem = configProblem(draft);
  const patchCategory = (id: string, change: Partial<Category>) =>
    setDraft({
      ...draft,
      categories: draft.categories.map((c) =>
        c.id === id ? { ...c, ...change } : c,
      ),
    });
  const patchRule = (id: string, change: Partial<Rule>) =>
    setDraft({
      ...draft,
      rules: draft.rules.map((r) => (r.id === id ? { ...r, ...change } : r)),
    });
  function moveRule(i: number, by: number) {
    const rules = [...draft!.rules];
    [rules[i], rules[i + by]] = [rules[i + by], rules[i]];
    setDraft({ ...draft!, rules });
  }
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("journal/categories", workspace, "PUT", {
        version: config!.version,
        reason: reason.trim(),
        ...draft,
      });
      setReason("");
      setNotice(
        "Категории и доли сохранены. Они действуют для ведомостей, которые готовятся после сохранения.",
      );
      onSaved();
    } catch (e: any) {
      if (staleVersion(e)) onSaved();
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const slugOk =
    SLUG.test(slug) &&
    !draft.categories.some((c) => c.id === slug) &&
    draft.categories.length < 20;
  return (
    <details>
      <summary>Категории работ и доли в итоге</summary>
      <p>
        Категории (ФО, СОР, СОЧ) называет и настраивает школа. Категорию нельзя
        удалить, её можно отключить: прежние работы сохранят метку. Доли
        определяют, какую часть итога даёт каждая категория. Проценты по
        умолчанию (25/25/50) — предположение, которое школа должна подтвердить.
      </p>
      <fieldset disabled={busy}>
        {draft.categories.map((c) => (
          <div className="category-row" key={c.id}>
            <label className="field">
              Название
              <input
                value={c.name}
                maxLength={12}
                onChange={(e) => patchCategory(c.id, { name: e.target.value })}
              />
            </label>
            <label className="field">
              Описание
              <input
                value={c.title}
                maxLength={80}
                onChange={(e) => patchCategory(c.id, { title: e.target.value })}
              />
            </label>
            <label className="field">
              Цвет
              <input
                type="color"
                value={c.color}
                onChange={(e) => patchCategory(c.id, { color: e.target.value })}
              />
            </label>
            <label className="field">
              Работа календаря
              <select
                value={c.calendar}
                onChange={(e) =>
                  patchCategory(c.id, {
                    calendar: e.target.value as Category["calendar"],
                  })
                }
              >
                {CALENDAR_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="check-line">
              <input
                type="checkbox"
                checked={c.active}
                onChange={(e) =>
                  patchCategory(c.id, { active: e.target.checked })
                }
              />
              Действует
            </label>
            {!known.has(c.id) && (
              <button
                type="button"
                onClick={() =>
                  setDraft({
                    ...draft,
                    categories: draft.categories.filter((x) => x.id !== c.id),
                    policy: {
                      ...draft.policy,
                      shares: draft.policy.shares.filter(
                        (s) => s.categoryId !== c.id,
                      ),
                    },
                    rules: draft.rules.map((r) => ({
                      ...r,
                      shares: r.shares.filter((s) => s.categoryId !== c.id),
                    })),
                  })
                }
              >
                Убрать новую категорию
              </button>
            )}
          </div>
        ))}
        <div className="category-row">
          <label className="field">
            Код новой категории (латиница, цифры, дефис)
            <input
              value={slug}
              maxLength={40}
              onChange={(e) => setSlug(e.target.value.toLowerCase())}
            />
          </label>
          <button
            type="button"
            disabled={!slugOk}
            onClick={() => {
              setDraft({
                ...draft,
                categories: [
                  ...draft.categories,
                  {
                    id: slug,
                    name: slug.slice(0, 12),
                    title: slug,
                    color: "#16a34a",
                    calendar: "",
                    active: true,
                  },
                ],
              });
              setSlug("");
            }}
          >
            Добавить категорию
          </button>
        </div>
        <label className="field">
          Категория новых работ по умолчанию
          <select
            value={draft.defaultCategory}
            onChange={(e) =>
              setDraft({ ...draft, defaultCategory: e.target.value })
            }
          >
            {draft.categories
              .filter((c) => c.active)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </label>
        <h4>Доли итога по умолчанию</h4>
        <PolicyFields
          policy={draft.policy}
          categories={draft.categories}
          onChange={(policy) => setDraft({ ...draft, policy })}
        />
        <h4>Правила для предметов и классов</h4>
        <p className="muted">
          Действует первое подходящее правило сверху; если ни одно не подошло,
          используются доли по умолчанию.
        </p>
        {draft.rules.map((r, i) => (
          <div className="category-rule" key={r.id}>
            <label className="field">
              Название правила
              <input
                value={r.name}
                maxLength={80}
                onChange={(e) => patchRule(r.id, { name: e.target.value })}
              />
            </label>
            <Picks
              label="Предметы"
              items={config.subjects ?? []}
              chosen={r.subjectIds}
              onChange={(subjectIds) => patchRule(r.id, { subjectIds })}
            />
            <Picks
              label="Классы"
              items={config.classes ?? []}
              chosen={r.classIds}
              onChange={(classIds) => patchRule(r.id, { classIds })}
            />
            <PolicyFields
              policy={r}
              categories={draft.categories}
              onChange={(p) => patchRule(r.id, p)}
            />
            <div className="dated-toolbar">
              <button
                type="button"
                disabled={i === 0}
                onClick={() => moveRule(i, -1)}
              >
                Выше
              </button>
              <button
                type="button"
                disabled={i === draft.rules.length - 1}
                onClick={() => moveRule(i, 1)}
              >
                Ниже
              </button>
              <button
                type="button"
                onClick={() =>
                  setDraft({
                    ...draft,
                    rules: draft.rules.filter((x) => x.id !== r.id),
                  })
                }
              >
                Удалить правило
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          disabled={draft.rules.length >= 50}
          onClick={() =>
            setDraft({
              ...draft,
              rules: [
                ...draft.rules,
                {
                  ...structuredClone(draft.policy),
                  id: crypto.randomUUID().replaceAll("-", ""),
                  name: `Правило ${draft.rules.length + 1}`,
                  subjectIds: [],
                  classIds: [],
                },
              ],
            })
          }
        >
          Добавить правило
        </button>
        <label className="field">
          Причина изменения
          <input
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        {problem && (
          <p role="alert" className="error">
            {problem}
          </p>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        <p>
          Изменение не меняет уже подготовленные ведомости и опубликованные
          итоги.
        </p>
        <button
          type="button"
          disabled={busy || !!problem || reason.trim().length < 3}
          onClick={() => void save()}
        >
          Сохранить категории и доли
        </button>
      </fieldset>
    </details>
  );
}
