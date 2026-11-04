import { useEffect, useId, useRef, useState } from "react";
import { Search, SquarePen, X } from "lucide-react";
import { launcherGroups, type LauncherAction } from "./launcherActions";
import { stepIndex } from "./navigation";

const TITLE = "Начните своё действие здесь";

/** The button of the top bar and the panel it opens: a search field and the actions in EduPage's categories. */
export default function Launcher({
  actions,
  onChoose,
}: {
  actions: LauncherAction[];
  onChoose: (action: LauncherAction) => void;
}) {
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState(""),
    [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null),
    button = useRef<HTMLButtonElement>(null),
    field = useRef<HTMLInputElement>(null),
    list = useRef<HTMLDivElement>(null),
    // Raised when the keys (or typing) move the choice: the option is brought into view; the pointer's own move is not.
    follow = useRef(false);
  const id = useId();
  const groups = launcherGroups(actions, query);
  // The options as they stand on screen, a popular one twice (in «Популярные» and in its own category).
  const options = groups.flatMap((group) => group.actions);
  const optionId = (index: number) => `${id}-option-${index}`;
  function close(back: boolean) {
    setOpen(false);
    setQuery("");
    setActive(0);
    if (back) button.current?.focus();
  }
  useEffect(() => {
    if (open) field.current?.focus();
  }, [open]);
  // The list scrolls inside the panel: the option the keys reached must not stay below its edge, where Enter would open it unseen.
  useEffect(() => {
    if (!follow.current) return;
    follow.current = false;
    const box = list.current,
      row = document.getElementById(optionId(active));
    if (!box || !row) return;
    if (active === 0) box.scrollTop = 0;
    else {
      const rect = row.getBoundingClientRect(),
        area = box.getBoundingClientRect();
      if (rect.top < area.top) box.scrollTop -= area.top - rect.top;
      else if (rect.bottom > area.bottom)
        box.scrollTop += rect.bottom - area.bottom;
    }
  }, [active, query]);
  useEffect(() => {
    if (!open) return;
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(true);
    };
    const outside = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("keydown", keys);
    document.addEventListener("mousedown", outside);
    return () => {
      document.removeEventListener("keydown", keys);
      document.removeEventListener("mousedown", outside);
    };
  }, [open]);
  function choose(action: LauncherAction | undefined) {
    if (!action) return;
    close(true);
    onChoose(action);
  }
  let index = -1;
  return (
    <div className="launcher" ref={box}>
      <button
        ref={button}
        className="icon-button launcher-button"
        title={TITLE}
        aria-label={TITLE}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => (open ? close(false) : setOpen(true))}
      >
        <SquarePen size={18} />
      </button>
      {open && (
        <div className="launcher-panel" role="dialog" aria-label={TITLE}>
          <div className="launcher-head">
            <label className="launcher-search">
              <Search size={16} aria-hidden="true" />
              <input
                ref={field}
                type="text"
                role="combobox"
                aria-label={TITLE}
                aria-expanded="true"
                aria-controls={`${id}-list`}
                aria-activedescendant={
                  options.length ? optionId(active) : undefined
                }
                placeholder={TITLE}
                autoComplete="off"
                value={query}
                onChange={(event) => {
                  follow.current = true;
                  setQuery(event.target.value);
                  setActive(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    choose(options[active]);
                    return;
                  }
                  if (event.key !== "ArrowDown" && event.key !== "ArrowUp")
                    return;
                  event.preventDefault();
                  const to = stepIndex(active, options.length, event.key);
                  if (to !== null) {
                    follow.current = true;
                    setActive(to);
                  }
                }}
              />
            </label>
            <button
              className="icon-button launcher-close"
              title="Закрыть"
              aria-label="Закрыть"
              onClick={() => close(true)}
            >
              <X size={18} />
            </button>
          </div>
          <div
            className="launcher-list"
            ref={list}
            id={`${id}-list`}
            role="listbox"
            aria-label="Действия"
          >
            {!options.length && (
              <p className="launcher-empty" role="status">
                Такого действия нет. Попробуйте другое слово.
              </p>
            )}
            {groups.map((group) => (
              <div
                key={group.id}
                role="group"
                aria-labelledby={`${id}-group-${group.id}`}
              >
                <h3 id={`${id}-group-${group.id}`}>{group.label}</h3>
                {group.actions.map((action) => {
                  const mine = ++index;
                  return (
                    <div
                      key={action.id}
                      id={optionId(mine)}
                      role="option"
                      aria-selected={mine === active}
                      className="launcher-option"
                      // The field keeps the focus while the pointer chooses.
                      onMouseDown={(event) => event.preventDefault()}
                      // Only a move of the pointer chooses: a list that scrolls under a resting pointer must not take the choice from the keys.
                      onMouseMove={() => active !== mine && setActive(mine)}
                      onClick={() => choose(action)}
                    >
                      {action.label}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
