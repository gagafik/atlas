import { useEffect, useRef } from "react";
import { Multi } from "./DatedSchedule";
import { roleNames, type AccessView, type Grant, type Role } from "./auth";
import { viewNames, type AccessForm } from "./accessModel";

type Items = Parameters<typeof Multi>[0]["items"];

// The form of an account, shown in place of the list. It keeps no state of its own: the values, the grants editor and what
// the buttons do are decided by AccessPanel; the only thing it does by itself is to take the focus when it opens.
export default function AccessUserForm({
  form,
  setForm,
  repeat,
  onRepeat,
  busy,
  error,
  grantSpace,
  onGrantSpace,
  grantView,
  onGrantView,
  targets,
  onTargets,
  table,
  allowedViews,
  onRole,
  onAddGrant,
  onEditGrant,
  onRemoveGrant,
  onSubmit,
  onBack,
}: {
  form: AccessForm;
  setForm: (form: AccessForm) => void;
  repeat: string;
  onRepeat: (value: string) => void;
  busy: boolean;
  // Repeated next to the buttons: a refused save must be visible without scrolling.
  error: string;
  grantSpace: "school" | "demo";
  onGrantSpace: (space: "school" | "demo") => void;
  grantView: AccessView;
  onGrantView: (view: AccessView) => void;
  targets: string[];
  onTargets: (ids: string[]) => void;
  // The directory of the chosen view, or nothing while it loads.
  table: Items | null | undefined;
  allowedViews: AccessView[];
  onRole: (role: Role) => void;
  onAddGrant: () => void;
  onEditGrant: (grant: Grant) => void;
  onRemoveGrant: (index: number) => void;
  onSubmit: () => void;
  // Back to the list; the page asks first when the form holds something.
  onBack: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  // The form takes the focus when it opens, on its heading, so that a screen reader says what has opened.
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    heading.current?.scrollIntoView?.({ block: "start" });
  }, []);
  return (
    <form
      className="dated-form access-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="access-form-head">
        <button type="button" onClick={onBack}>
          ← К списку
        </button>
        <h3 ref={heading} tabIndex={-1}>
          {form.id ? "Изменить учётную запись" : "Новая учётная запись"}
        </h3>
      </div>
      <fieldset className="access-fields" disabled={busy}>
        <div className="dated-fields">
          <label className="field">
            <span>Имя пользователя</span>
            <input
              required
              maxLength={160}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Логин пользователя</span>
            <input
              autoComplete="off"
              required
              pattern={"[a-z0-9][a-z0-9._\\-]{2,63}"}
              value={form.login}
              onChange={(e) =>
                setForm({ ...form, login: e.target.value.toLowerCase() })
              }
            />
          </label>
          <label className="field">
            <span>Роль</span>
            <select
              value={form.role}
              onChange={(e) => onRole(e.target.value as Role)}
            >
              {Object.entries(roleNames).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="check-line">
          <input
            type="checkbox"
            checked={form.active}
            onChange={(e) => setForm({ ...form, active: e.target.checked })}
          />
          Учётная запись активна
        </label>
        <div className="dated-fields">
          <label className="field">
            <span>
              {form.id ? "Новый пароль (необязательно)" : "Пароль пользователя"}
            </span>
            <input
              type="password"
              autoComplete="new-password"
              required={!form.id}
              minLength={12}
              maxLength={128}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
            <small>
              От 12 символов. При редактировании пустое поле сохраняет текущий
              пароль.
            </small>
          </label>
          <label className="field">
            <span>Повторите пароль</span>
            <input
              type="password"
              autoComplete="new-password"
              required={!!form.password}
              minLength={12}
              maxLength={128}
              value={repeat}
              onChange={(e) => onRepeat(e.target.value)}
            />
          </label>
        </div>
        {form.role === "admin" ? (
          <p>
            Администратору доступны школа, пример и управление пользователями.
          </p>
        ) : ["editor", "staff"].includes(form.role) ? (
          <Multi
            label="Разрешённые пространства"
            items={[
              { id: "school", name: "Школа" },
              { id: "demo", name: "Пример" },
            ]}
            values={form.workspaces}
            onChange={(v) => setForm({ ...form, workspaces: v })}
          />
        ) : (
          <section>
            <h4>Разрешённые расписания</h4>
            <p>
              Добавьте одну или несколько областей. Выбранные записи нужно
              включить кнопкой «Добавить область доступа».
            </p>
            <div className="dated-fields">
              <label className="field">
                <span>Пространство доступа</span>
                <select
                  value={grantSpace}
                  onChange={(e) =>
                    onGrantSpace(e.target.value as "school" | "demo")
                  }
                >
                  <option value="school">Школа</option>
                  <option value="demo">Пример</option>
                </select>
              </label>
              <label className="field">
                <span>Вид доступа</span>
                <select
                  value={grantView}
                  onChange={(e) => {
                    onGrantView(e.target.value as AccessView);
                    onTargets([]);
                  }}
                >
                  {allowedViews.map((v) => (
                    <option key={v} value={v}>
                      {viewNames[v]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {table ? (
              <Multi
                label="Записи для доступа"
                items={table}
                values={targets}
                onChange={onTargets}
              />
            ) : (
              <p>Загружаю справочник…</p>
            )}
            <button
              type="button"
              disabled={
                !targets.length ||
                targets.length > 200 ||
                (form.role === "student" && targets.length !== 1)
              }
              onClick={onAddGrant}
            >
              Добавить область доступа
            </button>
            {form.grants.map((g, i) => (
              <div className="access-grant" key={g.workspace + g.view}>
                <span>
                  {g.workspace === "school" ? "Школа" : "Пример"} ·{" "}
                  {viewNames[g.view]} · записей {g.entityIds.length}
                </span>
                <button type="button" onClick={() => onEditGrant(g)}>
                  Посмотреть и изменить
                </button>
                <button type="button" onClick={() => onRemoveGrant(i)}>
                  Удалить область
                </button>
              </div>
            ))}
          </section>
        )}
        <p className="muted">
          Изменение доступа завершает все сеансы этой записи. Последнего
          действующего администратора нельзя отключить.
        </p>
        {error && <p className="access-form-error error-banner">{error}</p>}
        <div className="dated-toolbar">
          <button type="button" onClick={onBack}>
            Закрыть форму
          </button>
          <button className="primary">Сохранить учётную запись</button>
        </div>
      </fieldset>
    </form>
  );
}
