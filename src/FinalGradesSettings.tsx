import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import AssessmentCategoriesEditor from "./AssessmentCategoriesEditor";
import type { CategoryConfig } from "./assessmentCategories";
import FinalSelect from "./FinalSelect";
import GradebookPreparationPanel from "./GradebookPreparationPanel";
import type { Options, Scale } from "./finalGradesTypes";
import { settingsLocked } from "./reportCardsView";
import { useBusy } from "./useBusy";
import type { Preparations } from "./usePreparations";
import "./FinalGrades.css";

export type SettingsTab = "scales" | "categories" | "preparation" | "report";
const TABS: { id: SettingsTab; title: string }[] = [
  { id: "scales", title: "Шкалы" },
  { id: "categories", title: "Категории и доли" },
  { id: "preparation", title: "Подготовка ведомостей" },
  { id: "report", title: "Табель" },
];

function ScaleEditor({
  workspace,
  scales,
  onSaved,
}: {
  workspace: string;
  scales: Scale[];
  onSaved: () => void;
}) {
  const { busy, error, notice, setNotice, run } = useBusy();
  const blank = (): Scale => ({
    id: crypto.randomUUID().replaceAll("-", ""),
    version: 0,
    name: "",
    kind: "thresholds",
    rounding: "none",
    minCount: 1,
    active: true,
    bands: [
      { label: "", minPercent: 0 },
      { label: "", minPercent: 50 },
    ],
  });
  const [value, setValue] = useState<Scale>(blank);
  return (
    <section>
      <h3>Школьные шкалы оценивания</h3>
      <p>
        Порог — минимальный процент для оценки. Рекомендация считается по долям
        категорий работ (ФО, СОР, СОЧ) школы; внутри категории — среднее
        процентов с учётом веса работ. Категории без оценок, пропуски и работы с
        нулевым весом не снижают результат. Текстовые отметки не переводятся в
        баллы. Ведомости, подготовленные до введения категорий, считаются по
        среднему всех работ.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy}>
        <FinalSelect
          label="Редактировать шкалу"
          value={value.version ? value.id : ""}
          onChange={(id) =>
            setValue(
              structuredClone(scales.find((s) => s.id === id) || blank()),
            )
          }
          items={scales}
          empty="Новая шкала"
        />
        <label className="field">
          Название шкалы
          <input
            value={value.name}
            maxLength={120}
            onChange={(e) => setValue({ ...value, name: e.target.value })}
          />
        </label>
        <label className="field">
          Тип шкалы
          <select
            value={value.kind}
            onChange={(e) =>
              setValue({ ...value, kind: e.target.value as Scale["kind"] })
            }
          >
            <option value="thresholds">Пороги процентов</option>
            <option value="labels">Итог выбирается вручную</option>
          </select>
        </label>
        {value.bands.map((band, i) => (
          <div className="final-band" key={i}>
            <label className="field">
              Оценка {i + 1}
              <input
                value={band.label}
                maxLength={30}
                onChange={(e) =>
                  setValue({
                    ...value,
                    bands: value.bands.map((b, n) =>
                      n === i ? { ...b, label: e.target.value } : b,
                    ),
                  })
                }
              />
            </label>
            <label className="field">
              Вес для среднего балла {i + 1}
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={band.points ?? ""}
                onChange={(e) =>
                  setValue({
                    ...value,
                    bands: value.bands.map((b, n) =>
                      n === i ? { ...b, points: e.target.value } : b,
                    ),
                  })
                }
              />
            </label>
            {value.kind === "thresholds" && (
              <label className="field">
                Порог {i + 1}, %
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.0001"
                  value={band.minPercent}
                  onChange={(e) =>
                    setValue({
                      ...value,
                      bands: value.bands.map((b, n) =>
                        n === i ? { ...b, minPercent: e.target.value } : b,
                      ),
                    })
                  }
                />
              </label>
            )}
            <button
              disabled={value.bands.length <= 2}
              onClick={() =>
                setValue({
                  ...value,
                  bands: value.bands.filter((_, n) => n !== i),
                })
              }
            >
              Удалить оценку {i + 1}
            </button>
          </div>
        ))}
        <button
          disabled={value.bands.length >= 30}
          onClick={() =>
            setValue({
              ...value,
              bands: [...value.bands, { label: "", minPercent: 100 }],
            })
          }
        >
          Добавить оценку
        </button>
        <label className="field">
          Округление процента перед выбором оценки
          <select
            value={value.rounding}
            onChange={(e) => setValue({ ...value, rounding: e.target.value })}
          >
            <option value="none">Без округления</option>
            <option value="0">До целого, половина вверх</option>
            <option value="1">До десятых, половина вверх</option>
            <option value="2">До сотых, половина вверх</option>
          </select>
        </label>
        <label className="field">
          Минимум работ с баллами и положительным весом
          <input
            type="number"
            min="1"
            max="10000"
            value={value.minCount}
            onChange={(e) =>
              setValue({ ...value, minCount: Number(e.target.value) })
            }
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.active}
            onChange={(e) => setValue({ ...value, active: e.target.checked })}
          />{" "}
          Доступна для новых итогов
        </label>
        <p>
          Изменение шкалы не меняет уже подготовленные и опубликованные итоги.
        </p>
        <p>
          Вес оценки нужен для среднего балла в табеле и для годовой оценки по
          итогам периодов. Укажите его у всех оценок шкалы или ни у одной.
        </p>
        <button
          onClick={() =>
            void run(async () => {
              const { id, ...body } = {
                ...value,
                bands: value.bands.map((b) => ({
                  ...b,
                  points: b.points === "" ? null : b.points,
                })),
              };
              await api(`final-grades/scales/${id}`, workspace, "PUT", body);
              onSaved();
              setValue(blank());
              setNotice("Шкала сохранена.");
            })
          }
        >
          Сохранить шкалу
        </button>
      </fieldset>
    </section>
  );
}

// The phrases of the verbal assessment and the rule of the yearly grade of the school.
function ReportSettings({
  workspace,
  options,
  onSaved,
}: {
  workspace: string;
  options: Options;
  onSaved: () => void;
}) {
  const { busy, error, notice, setNotice, run } = useBusy();
  const [phrases, setPhrases] = useState(options.phrases.join("\n")),
    [yearRule, setYearRule] = useState(options.yearRule);
  return (
    <section>
      <h3>Фразы словесной оценки и годовая оценка школы</h3>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <label className="field">
        Готовые фразы, по одной в строке
        <textarea
          rows={5}
          value={phrases}
          onChange={(e) => setPhrases(e.target.value)}
        />
      </label>
      <label className="field">
        Годовая оценка
        <select
          value={yearRule}
          onChange={(e) => setYearRule(e.target.value as Options["yearRule"])}
        >
          <option value="periods">По итогам четвертей или полугодий</option>
          <option value="works">По всем работам года</option>
        </select>
      </label>
      <p>
        Рекомендация по итогам периодов работает для шкал с весом оценок;
        округление идёт к ближайшей оценке, середина — вверх. Правило нужно
        подтвердить в школе.
      </p>
      <button
        disabled={busy}
        onClick={() =>
          void run(async () => {
            await api("final-grades/report-settings", workspace, "PUT", {
              version: options.reportVersion,
              phrases: phrases
                .split("\n")
                .map((p) => p.trim())
                .filter(Boolean),
              yearRule,
            });
            onSaved();
            setNotice("Настройки табеля сохранены.");
          })
        }
      >
        Сохранить настройки табеля
      </button>
    </section>
  );
}

// «Настройки»: everything that is set up once and not used every day, in one dialog with tabs.
export default function FinalGradesSettings({
  workspace,
  year,
  options,
  categories,
  tab: first,
  periodId,
  bookId,
  preparations,
  onCategoriesSaved,
  onScaleSaved,
  onReportSaved,
  onOpenBook,
  onPickBook,
  leave,
  onClose,
}: {
  workspace: string;
  // A past school year: everything is read-only.
  year?: string;
  options: Options;
  categories: CategoryConfig | null;
  tab: SettingsTab;
  // The period and the statement the screen shows now.
  periodId: string;
  bookId: string;
  preparations: Preparations;
  onCategoriesSaved: () => void;
  onScaleSaved: () => void;
  onReportSaved: () => void;
  // A statement that was prepared: open it and close the settings.
  onOpenBook: (id: string) => Promise<void>;
  // An older statement was picked from the list.
  onPickBook: (id: string) => void;
  leave: () => boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = useState(first);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  return (
    <dialog
      className="finals-settings"
      ref={dialog}
      aria-labelledby="finals-settings-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2 id="finals-settings-title">Настройки табелей</h2>
        <button onClick={onClose}>Закрыть</button>
      </header>
      <div className="finals-tabs" role="tablist" aria-label="Разделы настроек">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`finals-settings-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="finals-settings-panel"
            onClick={() => setTab(t.id)}
          >
            {t.title}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id="finals-settings-panel"
        aria-labelledby={`finals-settings-tab-${tab}`}
      >
        {year && (
          <p role="status">
            Это архивный учебный год: настройки можно смотреть, но не менять.
          </p>
        )}
        <fieldset disabled={settingsLocked(year, tab)}>
          {tab === "scales" && (
            <ScaleEditor
              workspace={workspace}
              scales={options.scales}
              onSaved={onScaleSaved}
            />
          )}
          {tab === "categories" && (
            <AssessmentCategoriesEditor
              workspace={workspace}
              config={categories}
              onSaved={onCategoriesSaved}
            />
          )}
          {tab === "preparation" && (
            <>
              <GradebookPreparationPanel
                periods={options.periods}
                preparations={preparations}
                initialPeriodId={periodId}
                readOnly={!!year}
                onOpen={onOpenBook}
              />
              <FinalSelect
                label="Ведомость"
                value={bookId}
                items={options.books.map((b) => ({
                  id: b.id,
                  name: `${b.period.name} · закрытие ${b.closureVersion}${b.current ? "" : " · архив"}`,
                }))}
                onChange={(id) => {
                  if (id && leave()) onPickBook(id);
                }}
              />
              <p>
                Для периода открывается новейшая ведомость. Здесь можно выбрать
                ведомость более раннего закрытия.
              </p>
            </>
          )}
          {tab === "report" && (
            <ReportSettings
              workspace={workspace}
              options={options}
              onSaved={onReportSaved}
            />
          )}
        </fieldset>
      </div>
    </dialog>
  );
}
