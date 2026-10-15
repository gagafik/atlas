import { useState } from "react";
import type { FormEvent } from "react";
import { addDays, type HandoverRun } from "./handoverHelpers";

type EditorProps = {
  options: any;
  run: HandoverRun;
  onSaved: (result?: { day: string }) => void;
  onCancel: () => void;
  onDirty: () => void;
  onError: (message: string) => void;
};
function Buttons({ onCancel, label }: { onCancel: () => void; label: string }) {
  return (
    <div className="handover-actions">
      <button className="handover-primary" type="submit">
        {label}
      </button>
      <button type="button" onClick={onCancel}>
        Отмена
      </button>
    </div>
  );
}
export function AdultProposalEditor(p: EditorProps) {
  const [draft, setDraft] = useState({
    studentId: p.options.students[0]?.id || "",
    kind: "parent",
    sponsorId: "",
    name: "",
    relationship: "",
    contact: "",
    startDate: p.options.today,
    endDate: addDays(p.options.today, 30),
    consentConfirmed: false,
    reason: "",
  });
  const change = (key: string, value: any) => {
    p.onDirty();
    setDraft({ ...draft, [key]: value });
  };
  const parents = p.options.parents.filter((x: any) =>
    x.studentIds.includes(draft.studentId),
  );
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await p.run("adults", draft);
      p.onSaved();
    } catch (e: any) {
      p.onError(e.message);
    }
  }
  return (
    <form className="handover-card" onSubmit={submit}>
      <h3>Кто может забирать ребёнка</h3>
      <p>
        Предложение сначала проверяет школа. До одобрения оформить выдачу этому
        взрослому нельзя.
      </p>
      <div className="handover-grid">
        <label>
          Ребёнок
          <select
            required
            value={draft.studentId}
            onChange={(e) => {
              p.onDirty();
              setDraft({ ...draft, studentId: e.target.value, sponsorId: "" });
            }}
          >
            {p.options.students.map((s: any) => (
              <option key={s.id} value={s.id}>
                {s.name} · {s.className}
              </option>
            ))}
          </select>
        </label>
        <label>
          Кто забирает
          <select
            value={draft.kind}
            onChange={(e) => change("kind", e.target.value)}
          >
            <option value="parent">Родитель</option>
            <option value="representative">Другой взрослый</option>
          </select>
        </label>
        {!p.options.parent && (
          <label>
            Родитель, дающий разрешение
            <select
              required
              value={draft.sponsorId}
              onChange={(e) => change("sponsorId", e.target.value)}
            >
              <option value="">Выберите родителя</option>
              {parents.map((x: any) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {draft.kind === "representative" && (
          <>
            <label>
              Полное имя взрослого
              <input
                required
                maxLength={160}
                value={draft.name}
                onChange={(e) => change("name", e.target.value)}
              />
            </label>
            <label>
              Кем приходится ребёнку
              <input
                required
                maxLength={100}
                value={draft.relationship}
                onChange={(e) => change("relationship", e.target.value)}
                placeholder="Например, бабушка"
              />
            </label>
          </>
        )}
        <label>
          Контакт для школы
          <input
            maxLength={100}
            value={draft.contact}
            onChange={(e) => change("contact", e.target.value)}
          />
        </label>
        <label>
          Разрешение с
          <input
            required
            type="date"
            value={draft.startDate}
            onChange={(e) => change("startDate", e.target.value)}
          />
        </label>
        <label>
          Разрешение по
          <input
            required
            type="date"
            min={draft.startDate}
            value={draft.endDate}
            onChange={(e) => change("endDate", e.target.value)}
          />
        </label>
      </div>
      {draft.kind === "parent" && (
        <p>
          Для родителя будет использовано имя из его школьной учётной записи.
        </p>
      )}
      <label>
        Основание или комментарий{!p.options.parent && " (обязательно)"}
        <textarea
          required={!p.options.parent}
          maxLength={1000}
          value={draft.reason}
          onChange={(e) => change("reason", e.target.value)}
        />
      </label>
      <label className="handover-check">
        <input
          type="checkbox"
          required
          checked={draft.consentConfirmed}
          onChange={(e) => change("consentConfirmed", e.target.checked)}
        />
        {p.options.parent
          ? "Подтверждаю согласие предложить этого взрослого для проверки школой"
          : "Согласие указанного родителя получено"}
      </label>
      <Buttons onCancel={p.onCancel} label="Отправить на проверку" />
    </form>
  );
}
export function PickupEditor(p: EditorProps & { adult: any }) {
  const [draft, setDraft] = useState({
    day:
      p.options.today > p.adult.detail.startDate
        ? p.options.today
        : p.adult.detail.startDate,
    expectedTime: "17:00",
    locationId: p.options.config.locations[0]?.id || "",
    note: "",
  });
  const change = (key: string, value: string) => {
    p.onDirty();
    setDraft({ ...draft, [key]: value });
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await p.run("pickups", {
        ...draft,
        adultId: p.adult.id,
        adultVersion: p.adult.version,
      });
      p.onSaved({ day: draft.day });
    } catch (e: any) {
      p.onError(e.message);
    }
  }
  return (
    <form className="handover-card" onSubmit={submit}>
      <h3>Заявка на уход</h3>
      <p>
        <strong>{p.adult.detail.studentName}</strong> ·{" "}
        {p.adult.detail.className}
      </p>
      <p>
        Забирает: <strong>{p.adult.detail.name}</strong> ·{" "}
        {p.adult.detail.relationship}
      </p>
      <div className="handover-grid">
        <label>
          Дата
          <input
            required
            type="date"
            min={p.options.today}
            max={addDays(p.options.today, p.options.config.advanceDays)}
            value={draft.day}
            onChange={(e) => change("day", e.target.value)}
          />
        </label>
        <label>
          Ожидаемое время
          <input
            required
            type="time"
            value={draft.expectedTime}
            onChange={(e) => change("expectedTime", e.target.value)}
          />
        </label>
        <label>
          Место встречи
          <select
            required
            value={draft.locationId}
            onChange={(e) => change("locationId", e.target.value)}
          >
            {p.options.config.locations.map((x: any) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Комментарий для школы
        <textarea
          maxLength={1000}
          value={draft.note}
          onChange={(e) => change("note", e.target.value)}
        />
      </label>
      <Buttons onCancel={p.onCancel} label="Оформить заявку" />
    </form>
  );
}
export function AdultDecisionEditor(
  p: EditorProps & { adult: any; action: string },
) {
  const [reason, setReason] = useState(""),
    [consentChecked, setConsent] = useState(false),
    [identityChecked, setIdentity] = useState(false);
  const approval = p.action === "approve";
  const title = approval
    ? "Одобрить взрослого"
    : p.action === "reject"
      ? "Не одобрять взрослого"
      : "Отозвать разрешение";
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await p.run(`adults/${p.adult.id}/decision`, {
        action: p.action,
        version: p.adult.version,
        reason,
        consentChecked,
        identityChecked,
      });
      p.onSaved();
    } catch (e: any) {
      p.onError(e.message);
    }
  }
  return (
    <form className="handover-card" onSubmit={submit} onChange={p.onDirty}>
      <h3>{title}</h3>
      <p>
        <strong>{p.adult.detail.name}</strong> · {p.adult.detail.relationship}
      </p>
      <p>
        Ребёнок: {p.adult.detail.studentName} · {p.adult.detail.className}
      </p>
      <p>
        Разрешение: {p.adult.detail.startDate} — {p.adult.detail.endDate}.
        Родитель: {p.adult.detail.sponsorName}.
      </p>
      {p.adult.detail.contact && <p>Контакт: {p.adult.detail.contact}</p>}
      {p.action === "revoke" && (
        <p>
          Все незавершённые заявки с этим взрослым будут отменены. Уже
          подтверждённые передачи сохранятся в истории.
        </p>
      )}
      {approval && (
        <>
          <p className="handover-policy">{p.options.config.policy}</p>
          <label className="handover-check">
            <input
              type="checkbox"
              required
              checked={consentChecked}
              onChange={(e) => setConsent(e.target.checked)}
            />
            Проверено согласие родителя
          </label>
          <label className="handover-check">
            <input
              type="checkbox"
              required
              checked={identityChecked}
              onChange={(e) => setIdentity(e.target.checked)}
            />
            Личность взрослого проверена по правилам школы
          </label>
        </>
      )}
      <label>
        Основание решения
        <textarea
          required
          maxLength={1000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      <Buttons onCancel={p.onCancel} label={title} />
    </form>
  );
}
export function PickupActionEditor(
  p: EditorProps & { pickup: any; action: string },
) {
  const [reason, setReason] = useState(""),
    [childReturned, setReturned] = useState(false);
  const title =
    p.action === "returned"
      ? "Зафиксировать возвращение"
      : p.action === "note"
        ? "Добавить примечание"
        : "Отменить заявку";
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await p.run(`pickups/${p.pickup.id}/action`, {
        action: p.action,
        version: p.pickup.version,
        reason,
        childReturned,
      });
      p.onSaved();
    } catch (e: any) {
      p.onError(e.message);
    }
  }
  return (
    <form className="handover-card" onSubmit={submit} onChange={p.onDirty}>
      <h3>{title}</h3>
      <p>
        {p.pickup.detail.studentName} · {p.pickup.day} ·{" "}
        {p.pickup.detail.adultName}
      </p>
      <label>
        Причина или пояснение
        <textarea
          required
          maxLength={1000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {p.action === "returned" && (
        <>
          <p>
            Запись о предыдущей передаче сохранится. После возвращения можно
            оформить новую заявку.
          </p>
          <label className="handover-check">
            <input
              required
              type="checkbox"
              checked={childReturned}
              onChange={(e) => setReturned(e.target.checked)}
            />
            Ребёнок действительно вернулся в школу
          </label>
        </>
      )}
      <Buttons onCancel={p.onCancel} label={title} />
    </form>
  );
}
export function TransferEditor(p: EditorProps & { pickup: any }) {
  const [draft, setDraft] = useState({
    version: p.pickup.version,
    reason: "",
    identityMethod: "known-person",
    identityChecked: false,
    childPresent: false,
  });
  const [review, setReview] = useState<any>(null),
    [handedOver, setHandedOver] = useState(false);
  const change = (key: string, value: any) => {
    p.onDirty();
    setReview(null);
    setHandedOver(false);
    setDraft({ ...draft, [key]: value });
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      if (!review) {
        setReview(await p.run(`pickups/${p.pickup.id}/review`, draft));
        setHandedOver(false);
      } else {
        await p.run(`pickups/${p.pickup.id}/complete`, {
          ...draft,
          reviewedAt: review.reviewedAt,
          previewHash: review.previewHash,
          handedOver,
        });
        p.onSaved();
      }
    } catch (e: any) {
      if ([403, 404, 409, 422].includes(e.status)) {
        setReview(null);
        setHandedOver(false);
      }
      p.onError(e.message);
    }
  }
  return (
    <form className="handover-card" onSubmit={submit}>
      <h3>Передача ребёнка взрослому</h3>
      <p>
        <strong>{p.pickup.detail.studentName}</strong> ·{" "}
        {p.pickup.detail.className}
      </p>
      <p>
        Забирает: <strong>{p.pickup.detail.adultName}</strong> ·{" "}
        {p.pickup.detail.relationship}
      </p>
      <p>Место: {p.pickup.detail.locationName}</p>
      <p className="handover-policy">{p.options.config.policy}</p>
      <label>
        Как проверена личность
        <select
          value={draft.identityMethod}
          onChange={(e) => change("identityMethod", e.target.value)}
        >
          <option value="known-person">Сотрудник лично знает взрослого</option>
          <option value="document-checked">Документ проверен лично</option>
          <option value="school-procedure">
            Другой способ по правилам школы
          </option>
        </select>
      </label>
      <label>
        Пояснение (без номера и копии документа)
        <textarea
          required={draft.identityMethod === "school-procedure"}
          maxLength={1000}
          value={draft.reason}
          onChange={(e) => change("reason", e.target.value)}
        />
      </label>
      <label className="handover-check">
        <input
          type="checkbox"
          required
          checked={draft.identityChecked}
          onChange={(e) => change("identityChecked", e.target.checked)}
        />
        Я лично проверил взрослого, который забирает ребёнка
      </label>
      <label className="handover-check">
        <input
          type="checkbox"
          required
          checked={draft.childPresent}
          onChange={(e) => change("childPresent", e.target.checked)}
        />
        Ребёнок находится рядом и готов к передаче
      </label>
      {review && (
        <section
          className="handover-review"
          aria-label="Проверка перед передачей"
        >
          <h4>Разрешение проверено</h4>
          <p>
            <strong>{review.studentName}</strong> →{" "}
            <strong>{review.adultName}</strong>
          </p>
          <p>
            {review.className} · {review.relationship} · {review.locationName}
          </p>
          <p>
            Подтвердите фактическую передачу в течение пяти минут. Если условия
            изменятся, потребуется новая проверка.
          </p>
          <label className="handover-check">
            <input
              required
              type="checkbox"
              checked={handedOver}
              onChange={(e) => {
                p.onDirty();
                setHandedOver(e.target.checked);
              }}
            />
            Ребёнок действительно передан этому взрослому
          </label>
        </section>
      )}
      <Buttons
        onCancel={p.onCancel}
        label={
          review
            ? "Подтвердить передачу ребёнка"
            : "Проверить разрешение перед передачей"
        }
      />
    </form>
  );
}
export function HandoverSettings(p: EditorProps) {
  const [draft, setDraft] = useState<any>(() => ({
    ...p.options.config,
    reason: "",
  }));
  const change = (key: string, value: any) => {
    p.onDirty();
    setDraft({ ...draft, [key]: value });
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await p.run("config", draft, "PUT");
      p.onSaved();
    } catch (e: any) {
      p.onError(e.message);
    }
  }
  return (
    <form className="handover-card" onSubmit={submit}>
      <h3>Правила школы и ответственные</h3>
      <p>
        Перед включением укажите порядок проверки согласия родителя, личности
        взрослого и передачи ребёнка.
      </p>
      <label className="handover-check">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => change("enabled", e.target.checked)}
        />
        Включить уход детей
      </label>
      <label className="handover-check">
        <input
          type="checkbox"
          checked={draft.parentRequests}
          onChange={(e) => change("parentRequests", e.target.checked)}
        />
        Родители могут оформлять заявки на уход
      </label>
      <label>
        За сколько дней разрешена запись
        <input
          required
          type="number"
          min={1}
          max={90}
          value={draft.advanceDays}
          onChange={(e) => change("advanceDays", Number(e.target.value))}
        />
      </label>
      <label>
        Порядок проверки и передачи
        <textarea
          required={draft.enabled}
          minLength={draft.enabled ? 20 : undefined}
          maxLength={5000}
          value={draft.policy}
          onChange={(e) => change("policy", e.target.value)}
        />
      </label>
      <h4>Места встречи</h4>
      {draft.locations.map((x: any, i: number) => (
        <div className="handover-toolbar" key={x.id}>
          <label>
            Название места {i + 1}
            <input
              required
              maxLength={120}
              value={x.name}
              onChange={(e) =>
                change(
                  "locations",
                  draft.locations.map((y: any, j: number) =>
                    j === i ? { ...y, name: e.target.value } : y,
                  ),
                )
              }
            />
          </label>
          <button
            type="button"
            disabled={draft.locations.length === 1}
            onClick={() =>
              change(
                "locations",
                draft.locations.filter((_: any, j: number) => i !== j),
              )
            }
          >
            Удалить место {i + 1}
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={draft.locations.length >= 20}
        onClick={() =>
          change("locations", [
            ...draft.locations,
            { id: crypto.randomUUID().replaceAll("-", ""), name: "" },
          ])
        }
      >
        Добавить место
      </button>
      <h4>Сотрудники</h4>
      <p>
        Ответственный проверяет взрослых, оформляет заявки и отмечает
        возвращение. Сотрудник выдачи проверяет и подтверждает передачу.
        Администраторы имеют все права. Пустой список классов означает все
        классы.
      </p>
      <div className="handover-staff">
        {p.options.staff.map((u: any) => {
          const d = draft.delegates.find((x: any) => x.userId === u.id);
          return (
            <div className="handover-card" key={u.id}>
              <label>
                {u.name}
                <select
                  value={d?.role || ""}
                  onChange={(e) =>
                    change("delegates", [
                      ...draft.delegates.filter((x: any) => x.userId !== u.id),
                      ...(e.target.value
                        ? [
                            {
                              userId: u.id,
                              role: e.target.value,
                              classIds: d?.classIds || [],
                            },
                          ]
                        : []),
                    ])
                  }
                >
                  <option value="">Без назначения</option>
                  <option value="manager">Ответственный</option>
                  <option value="issuer">Сотрудник выдачи</option>
                </select>
              </label>
              {d && (
                <details>
                  <summary>
                    Классы:{" "}
                    {d.classIds.length
                      ? d.classIds
                          .map(
                            (id: string) =>
                              p.options.classes.find((c: any) => c.id === id)
                                ?.name || id,
                          )
                          .join(", ")
                      : "все"}
                  </summary>
                  <div className="handover-class-grid">
                    {p.options.classes.map((c: any) => (
                      <label className="handover-check" key={c.id}>
                        <input
                          type="checkbox"
                          checked={d.classIds.includes(c.id)}
                          onChange={(e) =>
                            change(
                              "delegates",
                              draft.delegates.map((x: any) =>
                                x.userId === u.id
                                  ? {
                                      ...x,
                                      classIds: e.target.checked
                                        ? [...x.classIds, c.id]
                                        : x.classIds.filter(
                                            (id: string) => id !== c.id,
                                          ),
                                    }
                                  : x,
                              ),
                            )
                          }
                        />
                        {c.name}
                      </label>
                    ))}
                  </div>
                </details>
              )}
            </div>
          );
        })}
      </div>
      {draft.delegates
        .filter(
          (d: any) => !p.options.staff.some((u: any) => u.id === d.userId),
        )
        .map((d: any) => (
          <p key={d.userId}>
            Ранее назначенный сотрудник недоступен.{" "}
            <button
              type="button"
              onClick={() =>
                change(
                  "delegates",
                  draft.delegates.filter((x: any) => x.userId !== d.userId),
                )
              }
            >
              Убрать недействующее назначение
            </button>
          </p>
        ))}
      <label>
        Причина изменения
        <textarea
          required
          maxLength={1000}
          value={draft.reason}
          onChange={(e) => change("reason", e.target.value)}
        />
      </label>
      <Buttons onCancel={p.onCancel} label="Сохранить правила школы" />
    </form>
  );
}
