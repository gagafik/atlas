import { useState } from "react";
import type { FormEvent } from "react";
import {
  addDays,
  mealNames,
  schoolDay,
  schoolInput,
  schoolISO,
  type CanteenRun,
} from "./canteenHelpers";
import { formatMoney, moneyInput, parseMoney } from "./payments";

export function ClassChoice({
  classes,
  value,
  onChange,
  label = "Классы",
}: {
  classes: any[];
  value: string[];
  onChange: (ids: string[]) => void;
  label?: string;
}) {
  return (
    <details className="canteen-class-choice">
      <summary>
        {label}:{" "}
        {value.length
          ? value
              .map((id) => classes.find((c) => c.id === id)?.name || id)
              .join(", ")
          : "все"}
      </summary>
      <p>Пустой выбор означает все допустимые классы.</p>
      <div className="canteen-check-grid">
        {classes.map((c) => (
          <label className="canteen-check" key={c.id}>
            <input
              type="checkbox"
              checked={value.includes(c.id)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...value, c.id]
                    : value.filter((id) => id !== c.id),
                )
              }
            />
            {c.name}
          </label>
        ))}
      </div>
    </details>
  );
}

export function MenuEditor({
  menu,
  options,
  run,
  onSaved,
  onCancel,
  onDirty,
  onError,
}: {
  menu?: any;
  options: any;
  run: CanteenRun;
  onSaved: (id: string) => void;
  onCancel: () => void;
  onDirty: () => void;
  onError: (text: string) => void;
}) {
  const next = addDays(schoolDay(), 1);
  const [draft, setDraft] = useState<any>(() =>
    menu?.definition
      ? {
          ...menu.definition,
          opensAt: schoolInput(menu.definition.opensAt),
          orderClosesAt: schoolInput(menu.definition.orderClosesAt),
          cancelClosesAt: schoolInput(menu.definition.cancelClosesAt),
          variants: menu.definition.variants.map((v: any) => ({
            ...v,
            priceText: moneyInput(v.price, menu.decimals),
            allergenText: v.allergens.join(", "),
            capacityText: v.capacity ?? "",
          })),
        }
      : {
          day: next,
          meal: "lunch",
          title: "Школьный обед",
          starts: "12:00",
          ends: "14:00",
          opensAt: schoolDay() + "T08:00",
          orderClosesAt: next + "T09:00",
          cancelClosesAt: next + "T09:30",
          classIds: [],
          notice: "",
          variants: [
            {
              id: "standard",
              title: "Основной вариант",
              dishes: "",
              allergens: [],
              allergenText: "",
              notes: "",
              priceText: "0",
              capacityText: "",
              classIds: [],
            },
          ],
        },
  );
  const change = (key: string, value: any) => {
    onDirty();
    setDraft({ ...draft, [key]: value });
  };
  const variant = (index: number, key: string, value: any) =>
    change(
      "variants",
      draft.variants.map((v: any, i: number) =>
        i === index ? { ...v, [key]: value } : v,
      ),
    );
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try {
      const definition = {
        ...draft,
        opensAt: schoolISO(draft.opensAt),
        orderClosesAt: schoolISO(draft.orderClosesAt),
        cancelClosesAt: schoolISO(draft.cancelClosesAt),
        variants: draft.variants.map((v: any) => ({
          id: v.id,
          title: v.title,
          dishes: v.dishes,
          notes: v.notes,
          allergens: v.allergenText
            .split(",")
            .map((s: string) => s.trim())
            .filter(Boolean),
          classIds: v.classIds,
          price: parseMoney(v.priceText, options.decimals),
          capacity: v.capacityText === "" ? null : Number(v.capacityText),
        })),
      };
      const r = await run(
        menu ? `menus/${menu.id}` : "menus",
        { definition, ...(menu ? { version: menu.version } : {}) },
        menu ? "PUT" : "POST",
      );
      onSaved(r.id);
    } catch (e: any) {
      onError(e.message);
    }
  }
  return (
    <form className="canteen-card canteen-editor" onSubmit={submit}>
      <h3>{menu ? "Редактирование черновика" : "Новое меню"}</h3>
      <div className="canteen-grid">
        <label>
          Дата питания
          <input
            type="date"
            required
            value={draft.day}
            onChange={(e) => {
              const delta =
                (Date.parse(e.target.value + "T12:00:00Z") -
                  Date.parse(draft.day + "T12:00:00Z")) /
                86400000;
              if (!Number.isFinite(delta)) return;
              onDirty();
              setDraft({
                ...draft,
                day: e.target.value,
                orderClosesAt:
                  addDays(draft.orderClosesAt.slice(0, 10), delta) +
                  draft.orderClosesAt.slice(10),
                cancelClosesAt:
                  addDays(draft.cancelClosesAt.slice(0, 10), delta) +
                  draft.cancelClosesAt.slice(10),
              });
            }}
          />
        </label>
        <label>
          Приём пищи
          <select
            value={draft.meal}
            onChange={(e) => change("meal", e.target.value)}
          >
            {Object.entries(mealNames).map(([id, name]) => (
              <option value={id} key={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Название
          <input
            required
            maxLength={160}
            value={draft.title}
            onChange={(e) => change("title", e.target.value)}
          />
        </label>
        <label>
          Выдача с
          <input
            type="time"
            required
            value={draft.starts}
            onChange={(e) => change("starts", e.target.value)}
          />
        </label>
        <label>
          Выдача до
          <input
            type="time"
            required
            value={draft.ends}
            onChange={(e) => change("ends", e.target.value)}
          />
        </label>
        <label>
          Открыть заказы
          <input
            type="datetime-local"
            required
            value={draft.opensAt}
            onChange={(e) => change("opensAt", e.target.value)}
          />
        </label>
        <label>
          Заказать до
          <input
            type="datetime-local"
            required
            value={draft.orderClosesAt}
            onChange={(e) => change("orderClosesAt", e.target.value)}
          />
        </label>
        <label>
          Отменить до
          <input
            type="datetime-local"
            required
            value={draft.cancelClosesAt}
            onChange={(e) => change("cancelClosesAt", e.target.value)}
          />
        </label>
      </div>
      <p className="canteen-muted">
        Время школы: UTC+5. После публикации состав, цены, аллергены и сроки
        фиксируются.
      </p>
      <ClassChoice
        classes={options.classes}
        value={draft.classIds}
        onChange={(v) => change("classIds", v)}
        label="Кому доступно меню"
      />
      <label>
        Объявление к меню
        <textarea
          maxLength={2000}
          value={draft.notice}
          onChange={(e) => change("notice", e.target.value)}
        />
      </label>
      {draft.variants.map((v: any, i: number) => (
        <fieldset className="canteen-variant" key={v.id}>
          <legend>Вариант {i + 1}</legend>
          <label>
            Название варианта
            <input
              required
              maxLength={120}
              value={v.title}
              onChange={(e) => variant(i, "title", e.target.value)}
            />
          </label>
          <label>
            Блюда и состав
            <textarea
              required
              maxLength={3000}
              value={v.dishes}
              onChange={(e) => variant(i, "dishes", e.target.value)}
            />
          </label>
          <div className="canteen-grid">
            <label>
              Аллергены через запятую
              <input
                placeholder="Молоко, яйца, пшеница"
                value={v.allergenText}
                onChange={(e) => variant(i, "allergenText", e.target.value)}
              />
            </label>
            <label>
              Цена, {options.currency}
              <input
                inputMode="decimal"
                required
                value={v.priceText}
                onChange={(e) => variant(i, "priceText", e.target.value)}
              />
              <small>0 — включено в питание школы.</small>
            </label>
            <label>
              Лимит порций
              <input
                type="number"
                min={1}
                max={5000}
                placeholder="Без отдельного лимита"
                value={v.capacityText}
                onChange={(e) => variant(i, "capacityText", e.target.value)}
              />
            </label>
          </div>
          <ClassChoice
            classes={options.classes.filter(
              (c: any) =>
                !draft.classIds.length || draft.classIds.includes(c.id),
            )}
            value={v.classIds}
            onChange={(ids) => variant(i, "classIds", ids)}
            label="Классы этого варианта"
          />
          <label>
            Примечание
            <textarea
              maxLength={1000}
              value={v.notes}
              onChange={(e) => variant(i, "notes", e.target.value)}
            />
          </label>
          {draft.variants.length > 1 && (
            <button
              type="button"
              onClick={() =>
                change(
                  "variants",
                  draft.variants.filter((_: any, n: number) => i !== n),
                )
              }
            >
              Убрать вариант
            </button>
          )}
        </fieldset>
      ))}
      <div className="canteen-actions">
        {draft.variants.length < 12 && (
          <button
            type="button"
            onClick={() =>
              change("variants", [
                ...draft.variants,
                {
                  id:
                    "v" + crypto.randomUUID().replaceAll("-", "").slice(0, 12),
                  title: "",
                  dishes: "",
                  notes: "",
                  allergenText: "",
                  priceText: "0",
                  capacityText: "",
                  classIds: [],
                },
              ])
            }
          >
            Добавить вариант
          </button>
        )}
        <button className="canteen-primary" type="submit">
          Сохранить черновик
        </button>
        <button type="button" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

export function MealBooking({
  menu,
  options,
  run,
  onSaved,
  onDirty,
  onError,
}: {
  menu: any;
  options: any;
  run: CanteenRun;
  onSaved: () => void;
  onDirty: () => void;
  onError: (text: string) => void;
}) {
  const [variantId, setVariant] = useState(menu.definition.variants[0].id),
    [selected, setSelected] = useState<string[]>([]),
    [search, setSearch] = useState(""),
    [classId, setClass] = useState(""),
    [override, setOverride] = useState(false),
    [reason, setReason] = useState(""),
    [preview, setPreview] = useState<any>(null);
  const v = menu.definition.variants.find((x: any) => x.id === variantId);
  const eligible = options.students.filter(
    (p: any) =>
      (!menu.definition.classIds.length ||
        menu.definition.classIds.includes(p.classId)) &&
      (!v.classIds.length || v.classIds.includes(p.classId)),
  );
  const shown = eligible
    .filter(
      (p: any) =>
        (!classId || p.classId === classId) &&
        p.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
    )
    .slice(0, 100);
  const invalidate = () => {
    onDirty();
    setPreview(null);
  };
  const data = {
    action: "reserve",
    reservations: selected.map((id) => ({
      menuId: menu.id,
      menuVersion: menu.version,
      studentId: id,
      variantId,
      expectedPrice: v.price,
    })),
    overrideCutoff: override,
    reason,
  };
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try {
      if (!selected.length) throw new Error("Выберите хотя бы одного ученика.");
      if (!preview) setPreview(await run("batch/preview", data));
      else {
        await run("batch", { ...data, previewHash: preview.previewHash });
        setPreview(null);
        setSelected([]);
        onSaved();
      }
    } catch (e: any) {
      setPreview(null);
      onError(e.message);
    }
  }
  return (
    <form className="canteen-card" onSubmit={submit}>
      <h3>Оформить питание</h3>
      <label>
        Вариант
        <select
          value={variantId}
          onChange={(e) => {
            invalidate();
            setVariant(e.target.value);
            setSelected([]);
          }}
        >
          {menu.definition.variants.map((x: any) => (
            <option value={x.id} key={x.id}>
              {x.title} ·{" "}
              {x.price
                ? formatMoney(x.price, menu.decimals, menu.currency)
                : "включено"}
            </option>
          ))}
        </select>
      </label>
      <div className="canteen-toolbar">
        <label>
          Найти ученика
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        {options.permission === "manager" && (
          <label>
            Класс
            <select value={classId} onChange={(e) => setClass(e.target.value)}>
              <option value="">Все классы</option>
              {options.classes.map((c: any) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="canteen-choice-list">
        {shown.map((p: any) => (
          <label className="canteen-check" key={p.id}>
            <input
              type="checkbox"
              checked={selected.includes(p.id)}
              disabled={!selected.includes(p.id) && selected.length >= 100}
              onChange={(e) => {
                invalidate();
                setSelected(
                  e.target.checked
                    ? [...selected, p.id]
                    : selected.filter((id) => id !== p.id),
                );
              }}
            />
            {p.name} <span className="canteen-muted">{p.className}</span>
          </label>
        ))}
      </div>
      {!shown.length && <p>Нет подходящих учеников для выбранного варианта.</p>}
      <div className="canteen-actions">
        <button
          type="button"
          onClick={() => {
            invalidate();
            setSelected(shown.map((p: any) => p.id));
          }}
        >
          Выбрать показанных
        </button>
        <button
          type="button"
          onClick={() => {
            invalidate();
            setSelected([]);
          }}
        >
          Снять выбор
        </button>
        <span>Выбрано {selected.length}, до 100 за действие.</span>
      </div>
      {options.permission === "manager" && (
        <>
          <label className="canteen-check">
            <input
              type="checkbox"
              checked={override}
              onChange={(e) => {
                invalidate();
                setOverride(e.target.checked);
              }}
            />
            Исключение по сроку заказа
          </label>
          {override && (
            <label>
              Основание школы
              <input
                required
                maxLength={1000}
                value={reason}
                onChange={(e) => {
                  invalidate();
                  setReason(e.target.value);
                }}
              />
            </label>
          )}
        </>
      )}
      {v.price > 0 && (
        <p>
          Будет учтено начисление в финансовом кабинете.{" "}
          {options.config.allowDebt
            ? "Школа разрешила заказы с задолженностью."
            : "Для заказа нужна достаточная сумма на лицевом счёте."}
        </p>
      )}
      {preview && (
        <div className="canteen-preview" role="status">
          <strong>Проверено заказов: {preview.count}</strong>
          <p>
            Всего:{" "}
            {formatMoney(
              (BigInt(v.price) * BigInt(preview.count)).toString(),
              menu.decimals,
              menu.currency,
            )}
            .
          </p>
          <ul>
            {preview.items.map((x: any) => (
              <li key={x.studentId}>
                {x.name} · {x.title}
              </li>
            ))}
          </ul>
          <p>Нажмите подтверждение, чтобы сохранить все заказы вместе.</p>
        </div>
      )}
      <button
        className="canteen-primary"
        disabled={!selected.length || (v.price > 0 && !options.canOrderPaid)}
        type="submit"
      >
        {preview ? "Подтвердить заказы" : "Проверить заказы"}
      </button>
      {v.price > 0 && !options.canOrderPaid && (
        <p>
          Платное питание оформляет родитель-плательщик или администрация школы.
          Вы можете выбрать включённый в питание вариант.
        </p>
      )}
    </form>
  );
}

export function CanteenSettings({
  options,
  run,
  onSaved,
  onDirty,
  onError,
}: {
  options: any;
  run: CanteenRun;
  onSaved: () => void;
  onDirty: () => void;
  onError: (text: string) => void;
}) {
  const [cfg, setCfg] = useState<any>(options.config),
    [reason, setReason] = useState("");
  const change = (key: string, value: any) => {
    onDirty();
    setCfg({ ...cfg, [key]: value });
  };
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try {
      await run("config", { ...cfg, reason }, "PUT");
      onSaved();
    } catch (e: any) {
      onError(e.message);
    }
  }
  return (
    <form className="canteen-card" onSubmit={submit}>
      <h3>Правила и сотрудники столовой</h3>
      <label className="canteen-check">
        <input
          type="checkbox"
          checked={cfg.financeEnabled}
          onChange={(e) => change("financeEnabled", e.target.checked)}
        />
        Включить платные варианты питания и связь с лицевыми счетами
      </label>
      <label className="canteen-check">
        <input
          type="checkbox"
          checked={cfg.allowDebt}
          onChange={(e) => change("allowDebt", e.target.checked)}
        />
        Разрешить платные заказы при недостаточном балансе
      </label>
      <label className="canteen-check">
        <input
          type="checkbox"
          checked={cfg.studentOrdering}
          onChange={(e) => change("studentOrdering", e.target.checked)}
        />
        Разрешить ученикам самим заказывать включённое питание
      </label>
      <p>
        Без финансовой настройки доступны только варианты с ценой 0. Платные
        заказы семьи оформляет родитель с действующим доступом плательщика.
      </p>
      <p>
        Администратор столовой ведёт меню и заказы. Сотрудник выдачи отмечает
        питание своих классов. Кухня получает только сводные количества.
      </p>
      <div className="canteen-staff">
        {options.staff.map((p: any) => {
          const current = cfg.delegates.find((a: any) => a.userId === p.id);
          return (
            <div className="canteen-staff-row" key={p.id}>
              <label>
                {p.name}
                <select
                  value={current?.role || ""}
                  onChange={(e) =>
                    change("delegates", [
                      ...cfg.delegates.filter((a: any) => a.userId !== p.id),
                      ...(e.target.value
                        ? [{ userId: p.id, role: e.target.value, classIds: [] }]
                        : []),
                    ])
                  }
                >
                  <option value="">Без отдельного назначения</option>
                  <option value="manager">Администратор столовой</option>
                  <option value="issuer">Выдача питания</option>
                  <option value="kitchen">Кухня — количества</option>
                </select>
              </label>
              {current?.role === "issuer" && (
                <ClassChoice
                  classes={options.classes}
                  value={current.classIds}
                  onChange={(ids) =>
                    change(
                      "delegates",
                      cfg.delegates.map((a: any) =>
                        a.userId === p.id ? { ...a, classIds: ids } : a,
                      ),
                    )
                  }
                />
              )}
            </div>
          );
        })}
      </div>
      <label>
        Основание изменения
        <input
          required
          maxLength={1000}
          value={reason}
          onChange={(e) => {
            onDirty();
            setReason(e.target.value);
          }}
        />
      </label>
      <button className="canteen-primary" type="submit">
        Сохранить настройки
      </button>
    </form>
  );
}
