import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./GradeFeed.css";

type Change = "new" | "changed" | "removed";
type FeedItem = {
  seq: number;
  created: string;
  change: Change;
  signed: boolean;
  subject: string;
  date: string;
  start: string;
  teachers: string | string[];
  assessment: string;
  kind: "points" | "label";
  maxScore: number | null;
  weight: number;
  score: number | null;
  label: string | null;
  comment: string | null;
  previous: {
    score: number | null;
    label: string | null;
    maxScore: number;
    weight: number;
    assessment: string;
    commentChanged: boolean;
  } | null;
};
type Feed = {
  items: FeedItem[];
  nextBefore: number | null;
  unsigned: number;
  signedAt: string | null;
  latest: number | null;
  canSign: boolean;
  role: string;
};
type Signature = {
  name: string;
  role: string;
  signedAt: string | null;
  unsigned: number;
};
type Signatures = { items: Signature[]; events: boolean };

const roleLabels: Record<string, string> = {
  parent: "Родитель",
  student: "Ученик",
};
const number = (value: number, digits = 2) =>
  value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const stamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "";
const dayLabel = (day: string) =>
  new Date(day + "T00:00").toLocaleDateString("ru-RU");

function valueText(
  score: number | null,
  label: string | null,
  maxScore: number | null,
) {
  if (score === null) return label || "—";
  if (!maxScore) return number(score);
  const percent = Math.round((score / maxScore) * 1000) / 10;
  return `${number(score)} из ${number(maxScore)} (${number(percent, 1)}%)`;
}
function gradeText(item: FeedItem) {
  if (item.change === "removed") return "—";
  return item.kind === "label"
    ? item.label || "—"
    : valueText(item.score, item.label, item.maxScore);
}
function changeText(item: FeedItem) {
  const before = item.previous;
  if (item.change === "new" || !before) return "новая";
  const old = valueText(before.score, before.label, before.maxScore);
  if (item.change === "removed") return `удалена, была ${old}`;
  // Say exactly what changed, so the family signs what it can read.
  const parts = [];
  if (old !== gradeText(item)) parts.push(`было ${old}`);
  else if (before.maxScore !== item.maxScore)
    parts.push(`максимум был ${number(before.maxScore)}`);
  if (before.weight !== item.weight)
    parts.push(`вес был ${number(before.weight)}`);
  if (before.assessment !== item.assessment)
    parts.push(`называлась «${before.assessment}»`);
  if (before.commentChanged) parts.push("изменён комментарий");
  return ["изменена", ...parts].join(", ");
}

// Published grade changes for one pupil; parents sign them, pupils mark them as seen.
function FamilyFeed({
  workspace,
  studentId,
}: {
  workspace: string;
  studentId: string;
}) {
  const [feed, setFeed] = useState<Feed | null>(null),
    [hidden, setHidden] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const sequence = useRef(0);
  const pupil = feed?.role === "student";
  function fetchFeed(before?: number): Promise<Feed> {
    const params = new URLSearchParams({ studentId });
    if (before) params.set("before", String(before));
    return api(`journal/grades/feed?${params}`, workspace);
  }
  useEffect(() => {
    const token = ++sequence.current;
    setFeed(null);
    setHidden(false);
    setError("");
    setNotice("");
    setBusy(true);
    fetchFeed()
      .then((value) => {
        if (token === sequence.current) setFeed(value);
      })
      .catch((e: any) => {
        if (token !== sequence.current) return;
        // A former pupil of this account keeps the journal history but no feed.
        if (e.status === 403) setHidden(true);
        else setError(e.message);
      })
      .finally(() => {
        if (token === sequence.current) setBusy(false);
      });
    return () => {
      ++sequence.current;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, studentId]);
  async function more() {
    if (!feed?.nextBefore || busy) return;
    const token = sequence.current;
    setBusy(true);
    setError("");
    try {
      const value = await fetchFeed(feed.nextBefore);
      if (token !== sequence.current) return;
      setFeed(
        (old) =>
          old && {
            ...old,
            items: [...old.items, ...value.items],
            nextBefore: value.nextBefore,
            unsigned: value.unsigned,
          },
      );
    } catch (e: any) {
      if (token === sequence.current) setError(e.message);
    } finally {
      if (token === sequence.current) setBusy(false);
    }
  }
  async function sign() {
    // Only grades already on screen are signed: the newest one of the first page.
    if (!feed?.latest || busy) return;
    const token = ++sequence.current;
    setBusy(true);
    setError("");
    setNotice("");
    let result: "signed" | "newer" | "withdrawn" | "" = "";
    try {
      try {
        const signed = await api("journal/grades/sign", workspace, "POST", {
          studentId,
          throughSeq: feed.latest,
        });
        // Grades published after the list was loaded stay unsigned.
        result = signed.unsigned > 0 ? "newer" : "signed";
      } catch (e: any) {
        // The newest grades on screen were taken off publication meanwhile.
        if (
          e.status !== 409 ||
          !e.message.startsWith("Список оценок изменился")
        )
          throw e;
        result = "withdrawn";
      }
      if (token !== sequence.current) return;
      const value = await fetchFeed();
      if (token !== sequence.current) return;
      setFeed(value);
      setNotice(
        result === "withdrawn"
          ? "Список изменился: часть оценок снята с публикации. Проверьте список."
          : result === "newer"
            ? `Пока список был открыт, появились новые оценки. Проверьте их и ${pupil ? "отметьте" : "подпишите"} снова.`
            : pupil
              ? "Оценки отмечены как просмотренные."
              : "Оценки подписаны.",
      );
    } catch (e: any) {
      if (token !== sequence.current) return;
      setError(
        result === "signed" || result === "newer"
          ? `${pupil ? "Отметка" : "Подпись"} сохранена, но список не обновился: ${e.message}`
          : result === "withdrawn"
            ? `Список оценок изменился, но обновить его не удалось: ${e.message}`
            : e.message,
      );
    } finally {
      if (token === sequence.current) setBusy(false);
    }
  }
  if (hidden) return null;
  const unsigned = feed?.unsigned ?? 0;
  // Unsigned grades are the newest ones; all of them must be on screen before signing.
  // Once every page is loaded nothing at or below `latest` is left unseen, even if
  // grades were corrected or signed elsewhere between pages.
  const unseen = feed?.nextBefore
    ? unsigned - feed.items.filter((i) => !i.signed).length
    : 0;
  return (
    <section className="grade-feed" aria-label="Новые оценки">
      <header className="grade-feed-head">
        <h3>Новые оценки</h3>
        {feed && (
          <span
            className={`grade-feed-count${unsigned ? " grade-feed-count-open" : ""}`}
          >
            {unsigned
              ? `${pupil ? "Не просмотрено" : "Не подписано"}: ${unsigned}`
              : pupil
                ? "Все оценки просмотрены"
                : "Все оценки подписаны"}
          </span>
        )}
        {feed?.canSign && (
          <button
            type="button"
            className="primary"
            disabled={busy || !unsigned || !feed.latest || unseen > 0}
            onClick={() => void sign()}
          >
            {pupil ? "Просмотрено" : "Подписать оценки"}
          </button>
        )}
      </header>
      {unseen > 0 && (
        <p className="grade-feed-meta">
          Ещё {unseen} {pupil ? "непросмотренных" : "неподписанных"} ниже:
          откройте их кнопкой «Показать ещё», чтобы{" "}
          {pupil ? "отметить" : "подписать"}.
        </p>
      )}
      {feed?.signedAt && (
        <p className="grade-feed-meta">
          Последняя подпись: {stamp(feed.signedAt)}
        </p>
      )}
      {error && (
        <p role="alert" className="grade-feed-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && !feed && <p role="status">Загружаю новые оценки…</p>}
      {feed && !feed.items.length && (
        <p className="empty-state">Опубликованных оценок пока нет.</p>
      )}
      {feed && !!feed.items.length && (
        <ul className="grade-feed-list" aria-label="Изменения оценок">
          {feed.items.map((item) => {
            const teachers = Array.isArray(item.teachers)
              ? item.teachers.join(", ")
              : item.teachers;
            return (
              <li
                key={item.seq}
                className={`grade-feed-item grade-feed-${item.change}${item.signed ? "" : " grade-feed-unsigned"}`}
              >
                <div className="grade-feed-line">
                  <span>
                    {`${item.subject} · ${dayLabel(item.date)} · ${item.assessment}: `}
                    <strong className="grade-feed-value">
                      {gradeText(item)}
                    </strong>
                  </span>
                  <span className="grade-feed-change">{changeText(item)}</span>
                  {!item.signed && (
                    <span className="grade-feed-badge">
                      {pupil ? "не просмотрена" : "не подписана"}
                    </span>
                  )}
                </div>
                {item.comment && (
                  <p className="grade-feed-comment">
                    Комментарий учителя: {item.comment}
                  </p>
                )}
                <p className="grade-feed-meta">
                  {teachers ? `${teachers} · ` : ""}
                  {item.start ? `урок в ${item.start} · ` : ""}
                  опубликовано {stamp(item.created)}
                </p>
              </li>
            );
          })}
        </ul>
      )}
      {feed?.nextBefore && (
        <button type="button" disabled={busy} onClick={() => void more()}>
          Показать ещё
        </button>
      )}
    </section>
  );
}

// Who in the family signed the pupil's grades; only teachers of the pupil and admins see it.
function FamilySignatures({
  workspace,
  studentId,
}: {
  workspace: string;
  studentId: string;
}) {
  const [data, setData] = useState<Signatures | null>(null),
    [hidden, setHidden] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setData(null);
    setHidden(false);
    setError("");
    const params = new URLSearchParams({ studentId });
    api(`journal/grades/signatures?${params}`, workspace)
      .then((value: Signatures) => {
        if (alive) setData(value);
      })
      .catch((e: any) => {
        if (!alive) return;
        // A pupil outside the teacher's current lessons simply has no panel.
        if (e.status === 403) setHidden(true);
        else setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [workspace, studentId]);
  if (hidden) return null;
  return (
    <section className="grade-feed" aria-label="Подписи семьи">
      <header className="grade-feed-head">
        <h3>Подписи семьи</h3>
      </header>
      {error && (
        <p role="alert" className="grade-feed-error">
          {error}
        </p>
      )}
      {!data && !error && <p role="status">Загружаю подписи семьи…</p>}
      {data && !data.items.length && (
        <p className="empty-state">
          {data.events
            ? "У ученика нет учётных записей родителей или ученика, оценки подписать некому"
            : "У ученика нет учётных записей родителей или ученика"}
        </p>
      )}
      {data && !!data.items.length && (
        <div className="grade-feed-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Кто</th>
                <th scope="col">Роль</th>
                <th scope="col">Последняя подпись</th>
                <th scope="col">Не подписано</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((s, i) => (
                <tr key={`${s.role}-${s.name}-${i}`}>
                  <td>{s.name}</td>
                  <td>{roleLabels[s.role] || s.role}</td>
                  <td>{stamp(s.signedAt) || "—"}</td>
                  <td className={s.unsigned ? "grade-feed-open" : undefined}>
                    {s.unsigned}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function GradeFeed({
  workspace,
  studentId,
  staff,
}: {
  workspace: string;
  studentId: string;
  staff: boolean;
}) {
  return staff ? (
    <FamilySignatures workspace={workspace} studentId={studentId} />
  ) : (
    <FamilyFeed workspace={workspace} studentId={studentId} />
  );
}
