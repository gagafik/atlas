import { useEffect, useState } from "react";
import MessageFeed from "./MessageFeed";
import { iconOf } from "./Shell";
import type { NavItem } from "./navigation";
import { bellState, minutesNow, todayBells } from "./startPage";
import type { State } from "./types";

/** «Начало» of the administration: the bells of today with the time left, the latest announcements and the tiles of the main modules. */
export default function SchoolStart({
  state,
  workspace,
  tiles,
  onTile,
}: {
  state: State;
  workspace: string;
  tiles: NavItem[];
  onTile: (item: NavItem) => void;
}) {
  const [now, setNow] = useState(() => new Date());
  // The countdown moves by the minute; half a minute keeps it from lagging behind the bell.
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(timer);
  }, []);
  const bells = todayBells(state, now.toLocaleDateString("sv-SE"));
  const stage = bellState(bells, minutesNow(now));
  const current = stage?.phase === "lesson" ? stage.index : -1;
  const status = !stage
    ? ""
    : stage.phase === "lesson"
      ? `урок ${bells[stage.index].label} · до звонка ${stage.minutes} мин`
      : stage.phase === "break"
        ? `перемена · до звонка ${stage.minutes} мин`
        : stage.phase === "before"
          ? `до первого урока ${stage.minutes} мин`
          : "уроки на сегодня закончились";
  return (
    <>
      <section className="school-day" aria-label="Расписание сегодня">
        <header>
          <h2>
            Расписание сегодня{" "}
            {now.toLocaleDateString("ru-RU", {
              day: "2-digit",
              month: "2-digit",
            })}
          </h2>
          {status && <strong role="status">{status}</strong>}
        </header>
        {bells.length ? (
          <ol>
            {bells.map((bell, index) => (
              <li
                key={index}
                className={index === current ? "current" : ""}
                aria-current={index === current ? "true" : undefined}
              >
                <strong>{bell.label}</strong>
                <small>
                  {bell.start}–{bell.end}
                </small>
              </li>
            ))}
          </ol>
        ) : (
          <p className="info-line">
            Сегодня уроков нет: выходной, праздник или каникулы.
          </p>
        )}
      </section>
      <div className="start-grid">
        <section className="start-feed">
          <h2>Уведомления</h2>
          <MessageFeed key={workspace} workspace={workspace} />
        </section>
        <div className="start-tiles">
          {tiles.map((tile) => {
            const Icon = iconOf(tile.icon);
            return (
              <button key={tile.id} onClick={() => onTile(tile)}>
                <Icon size={26} />
                <span>{tile.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}
