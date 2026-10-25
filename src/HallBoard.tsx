import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  KIND_LABEL,
  dayHeading,
  describe,
  fitParts,
  pageSize,
  paginate,
  schoolClock,
  type HallPayload,
} from "./hallScreen";
import "./HallDisplay.css";

interface Slide {
  title: string;
  text: string;
}

/** The board itself: shared by the TV page and the administrator's preview. Every value is rendered as text. */
export default function HallBoard({
  data,
  clock,
  offline,
  lastUpdated,
  fixed,
  tools,
}: {
  data: HallPayload;
  clock: number;
  offline: boolean;
  lastUpdated?: number;
  fixed?: boolean;
  tools?: ReactNode;
}) {
  const board = useRef<HTMLElement>(null);
  const view = useRef<HTMLDivElement>(null);
  const ruler = useRef<HTMLElement>(null);
  const [size, setSize] = useState(10);
  const [page, setPage] = useState(0);
  const [layout, setLayout] = useState(0);
  const [fit, setFit] = useState<{ key: string; slides: Slide[] } | null>(null);
  useEffect(() => {
    const measure = () => {
      const el = board.current;
      if (!el) return;
      setLayout((n) => n + 1);
      // A phone shows the whole list; only a wall display pages.
      if (!fixed && window.innerWidth <= 600) return setSize(1000);
      const font = parseFloat(getComputedStyle(el).fontSize) || 40;
      setSize(pageSize(el.clientHeight || window.innerHeight, font / 40));
    };
    measure();
    window.addEventListener("resize", measure);
    const watcher =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    if (board.current) watcher?.observe(board.current);
    return () => {
      window.removeEventListener("resize", measure);
      watcher?.disconnect();
    };
  }, [fixed]);
  useEffect(() => {
    // Late fonts change how the text wraps.
    const fonts = typeof document === "undefined" ? undefined : document.fonts;
    if (!fonts) return;
    const again = () => setLayout((n) => n + 1);
    void fonts.ready.then(again);
    fonts.addEventListener?.("loadingdone", again);
    return () => fonts.removeEventListener?.("loadingdone", again);
  }, []);
  const notes = data.announcements;
  const key = JSON.stringify(notes);
  useLayoutEffect(() => {
    const box = view.current,
      rule = ruler.current,
      title = rule?.querySelector("h3"),
      text = rule?.querySelector("p");
    // A phone lets the panel grow; elsewhere the panel is cut off, so long texts are split into parts that fit it.
    if (
      !box ||
      !title ||
      !text ||
      box.clientHeight < 40 ||
      (!fixed && window.innerWidth <= 600)
    )
      return setFit(null);
    const slides: Slide[] = [];
    for (const note of notes) {
      title.textContent = note.title;
      const fits = (part: string) => {
        text.textContent = part;
        return rule!.scrollHeight <= box.clientHeight;
      };
      for (const part of fitParts(note.text, fits))
        slides.push({ title: note.title, text: part });
    }
    title.textContent = text.textContent = "";
    setFit({ key, slides });
  }, [key, layout, fixed]);
  useEffect(() => {
    const timer = setInterval(
      () => setPage((n) => n + 1),
      Math.max(8, data.pageSeconds) * 1000,
    );
    return () => clearInterval(timer);
  }, [data.pageSeconds]);
  // Days share the height of the screen: each heading costs about a row.
  const rows =
    data.days.length > 1
      ? Math.max(3, Math.floor((size - data.days.length) / data.days.length))
      : size;
  const now = schoolClock(clock, data.utcOffsetMinutes);
  const slides: Slide[] =
    fit && fit.key === key
      ? fit.slides
      : notes.map((n) => ({ title: n.title, text: n.text }));
  const note = slides.length ? slides[page % slides.length] : null;
  return (
    <main
      ref={board}
      className={"hall-board" + (fixed ? " hall-fixed" : "")}
      aria-label="Информационный экран"
    >
      <header className="hall-head">
        <div className="hall-title">
          <strong>{data.school}</strong>
          <span>{data.screen}</span>
        </div>
        {offline && (
          <p className="hall-offline" role="status">
            Нет связи ·{" "}
            {lastUpdated
              ? `данные на ${schoolClock(lastUpdated, data.utcOffsetMinutes).time}`
              : "данных нет"}
          </p>
        )}
        <div className="hall-clock">
          <time>{now.time}</time>
          <span>{now.date}</span>
        </div>
      </header>
      <div className={"hall-body" + (note ? " hall-with-notes" : "")}>
        <section className="hall-changes">
          {data.days.map((day, index) => {
            const pages = paginate(day.items, rows);
            const current = pages.length ? pages[page % pages.length] : [];
            return (
              <div className="hall-day" key={day.date}>
                <h2>
                  {dayHeading(day, index === 0)}
                  {pages.length > 1 && (
                    <small>
                      Страница {(page % pages.length) + 1} из {pages.length}
                    </small>
                  )}
                </h2>
                {current.length === 0 ? (
                  <p className="hall-empty">Изменений нет</p>
                ) : (
                  <ul>
                    {current.map((item, i) => (
                      <li className="hall-row" key={i}>
                        <span className="hall-time">
                          {item.period && <b>{item.period}</b>}
                          {item.start}–{item.end}
                        </span>
                        <span className="hall-class">{item.classes}</span>
                        <span className="hall-subject">{item.subject}</span>
                        <span className={"hall-kind hall-kind-" + item.kind}>
                          {KIND_LABEL[item.kind] || "Изменение"}
                        </span>
                        <span className="hall-what">{describe(item)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {day.more > 0 && (
                  <p className="hall-more">…и ещё {day.more} изменений</p>
                )}
              </div>
            );
          })}
        </section>
        {note && (
          <aside className="hall-notes" aria-label="Объявления">
            <h2>
              Объявления
              {slides.length > 1 && (
                <small>
                  {(page % slides.length) + 1} из {slides.length}
                </small>
              )}
            </h2>
            <div className="hall-note-view" ref={view}>
              <article key={page % slides.length}>
                <h3>{note.title}</h3>
                {note.text && <p>{note.text}</p>}
              </article>
              <article className="hall-probe" aria-hidden="true" ref={ruler}>
                <h3 />
                <p />
              </article>
            </div>
          </aside>
        )}
      </div>
      {tools}
    </main>
  );
}
