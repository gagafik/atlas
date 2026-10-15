import type {
  ApplicationData,
  Campaign,
  HistoryItem,
  Question,
} from "./admissions";
import { admissionLabels } from "./admissions";
import { displayTime } from "./meetings";

export function Questions({
  questions,
  data,
  onChange,
  disabled = false,
}: {
  questions: Question[];
  data: ApplicationData;
  onChange: (value: ApplicationData) => void;
  disabled?: boolean;
}) {
  return (
    <>
      {questions.map((q) => {
        const value = data.answers[q.id];
        const set = (value: ApplicationData["answers"][string]) =>
          onChange({ ...data, answers: { ...data.answers, [q.id]: value } });
        return (
          <fieldset key={q.id} disabled={disabled}>
            <legend>
              {q.title}
              {q.required ? " *" : ""}
            </legend>
            {q.kind === "text" && (
              <textarea
                aria-label={q.title}
                maxLength={4000}
                value={typeof value === "string" ? value : ""}
                onChange={(e) => set(e.target.value)}
              />
            )}
            {q.kind === "single" && (
              <select
                aria-label={q.title}
                value={typeof value === "string" ? value : ""}
                onChange={(e) => set(e.target.value)}
              >
                <option value="">Выберите ответ</option>
                {q.choices.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            )}
            {q.kind === "multiple" &&
              q.choices.map((v) => (
                <label className="admission-check" key={v}>
                  <input
                    type="checkbox"
                    checked={Array.isArray(value) && value.includes(v)}
                    onChange={(e) =>
                      set(
                        e.target.checked
                          ? [...(Array.isArray(value) ? value : []), v]
                          : (Array.isArray(value) ? value : []).filter(
                              (x) => x !== v,
                            ),
                      )
                    }
                  />
                  {v}
                </label>
              ))}
            {q.kind === "consent" && (
              <label className="admission-check">
                <input
                  type="checkbox"
                  checked={value === true}
                  onChange={(e) => set(e.target.checked)}
                />
                Подтверждаю
              </label>
            )}
            {["date", "number"].includes(q.kind) && (
              <input
                aria-label={q.title}
                type={q.kind}
                value={
                  typeof value === "string" || typeof value === "number"
                    ? value
                    : ""
                }
                onChange={(e) =>
                  set(
                    e.target.value === ""
                      ? null
                      : q.kind === "number"
                        ? Number(e.target.value)
                        : e.target.value,
                  )
                }
              />
            )}
          </fieldset>
        );
      })}
    </>
  );
}

export function ApplicantFields({
  data,
  campaign,
  onChange,
  disabled,
}: {
  data: ApplicationData;
  campaign: Campaign;
  onChange: (value: ApplicationData) => void;
  disabled: boolean;
}) {
  return (
    <fieldset disabled={disabled} className="admission-fields">
      <legend>Анкета ребёнка</legend>
      <label>
        ФИО ребёнка *
        <input
          value={data.childName}
          maxLength={160}
          onChange={(e) => onChange({ ...data, childName: e.target.value })}
        />
      </label>
      <div className="admission-pair">
        <label>
          Дата рождения *
          <input
            type="date"
            value={data.birthDate || ""}
            onChange={(e) =>
              onChange({ ...data, birthDate: e.target.value || null })
            }
          />
        </label>
        <label>
          Параллель *
          <select
            value={data.grade ?? ""}
            onChange={(e) =>
              onChange({
                ...data,
                grade: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          >
            <option value="">Выберите параллель</option>
            {campaign.data.grades.map((g) => (
              <option key={g} value={g}>
                {g === 0 ? "Подготовительный класс" : g + " класс"}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        ФИО законного представителя *
        <input
          value={data.guardianName}
          maxLength={160}
          onChange={(e) => onChange({ ...data, guardianName: e.target.value })}
        />
      </label>
      <div className="admission-pair">
        <label>
          Email *
          <input
            type="email"
            value={data.email}
            maxLength={254}
            onChange={(e) => onChange({ ...data, email: e.target.value })}
          />
        </label>
        <label>
          Телефон *
          <input
            type="tel"
            value={data.phone}
            maxLength={80}
            onChange={(e) => onChange({ ...data, phone: e.target.value })}
          />
        </label>
      </div>
      <label>
        Предыдущая школа
        <input
          value={data.previousSchool}
          maxLength={300}
          onChange={(e) =>
            onChange({ ...data, previousSchool: e.target.value })
          }
        />
      </label>
      <Questions
        questions={campaign.data.questions}
        data={data}
        onChange={onChange}
        disabled={disabled}
      />
      <details>
        <summary>Условия обработки сведений</summary>
        <p className="admission-pre">{campaign.data.privacyNotice}</p>
      </details>
      <label className="admission-check">
        <input
          type="checkbox"
          checked={data.consent}
          onChange={(e) => onChange({ ...data, consent: e.target.checked })}
        />
        Я ознакомился с условиями и согласен на обработку сведений для
        рассмотрения заявки *
      </label>
    </fieldset>
  );
}

export function SubmittedData({
  data,
  campaign,
  fileUrl,
}: {
  data: ApplicationData;
  campaign: Campaign;
  fileUrl: (id: string) => string;
}) {
  return (
    <div className="admission-submission">
      <dl>
        {[
          ["Ребёнок", data.childName],
          ["Дата рождения", data.birthDate],
          ["Параллель", data.grade],
          ["Представитель", data.guardianName],
          ["Email", data.email],
          ["Телефон", data.phone],
          ["Предыдущая школа", data.previousSchool],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value ?? "—"}</dd>
          </div>
        ))}
        {campaign.data.questions.map((q) => (
          <div key={q.id}>
            <dt>{q.title}</dt>
            <dd>
              {Array.isArray(data.answers[q.id])
                ? (data.answers[q.id] as string[]).join(", ")
                : typeof data.answers[q.id] === "boolean"
                  ? data.answers[q.id]
                    ? "Да"
                    : "Нет"
                  : String(data.answers[q.id] ?? "—")}
            </dd>
          </div>
        ))}
      </dl>
      <ul>
        {data.files.map((f, i) => (
          <li key={f.id}>
            <a href={fileUrl(f.id)} download>
              {campaign.data.documents.find((d) => d.id === f.kind)?.title ||
                "Документ"}{" "}
              · файл {i + 1}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AdmissionHistory({
  items,
  campaign,
  fileUrl,
}: {
  items: HistoryItem[];
  campaign: Campaign;
  fileUrl: (id: string) => string;
}) {
  return (
    <ol className="admission-history">
      {items.map((h) => (
        <li key={h.id}>
          <strong>{admissionLabels[h.action] || h.action}</strong>
          <small>
            {displayTime(h.created)} · {h.actor}
          </small>
          {h.message && <p className="admission-pre">{h.message}</p>}
          {h.data && (
            <details>
              <summary>Отправленная анкета</summary>
              <SubmittedData
                data={h.data}
                campaign={campaign}
                fileUrl={fileUrl}
              />
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}
