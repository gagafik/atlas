import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Named, ReportLayout } from "./finalGradesTypes";
import { useBusy } from "./useBusy";

// Subjects in the saved order; the ones the layout does not mention yet follow by name.
const ordered = (layout: ReportLayout) => {
  const rank = new Map(layout.order.map((id, i) => [id, i]));
  return [...layout.subjects].sort(
    (a, b) =>
      (rank.get(a.id) ?? 1e6) - (rank.get(b.id) ?? 1e6) ||
      a.name.localeCompare(b.name, "ru", { numeric: true }),
  );
};

// «Предметы в табеле»: which subjects go on the report cards of a class and in what order.
export default function ReportLayoutDialog({
  workspace,
  periodId,
  classId,
  className,
  readOnly,
  onSaved,
  onClose,
}: {
  workspace: string;
  periodId: string;
  classId: string;
  className: string;
  // An archived school year: the order can be read, not changed.
  readOnly: boolean;
  // The saved layout was written: the class grid reads it again.
  onSaved: () => void;
  onClose: () => void;
}) {
  const { busy, error, notice, setError, setNotice, run } = useBusy();
  const [layout, setLayout] = useState<ReportLayout | null>(null),
    [items, setItems] = useState<Named[]>([]),
    [hidden, setHidden] = useState<string[]>([]);
  const dialog = useRef<HTMLDialogElement>(null);
  const path = `final-grades/report-layouts/${periodId}/${classId}`;
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  function show(r: ReportLayout) {
    setLayout(r);
    setItems(ordered(r));
    setHidden(r.hidden);
  }
  useEffect(() => {
    let current = true;
    api(path, workspace)
      .then((r: ReportLayout) => current && show(r))
      .catch(
        (e) =>
          current &&
          setError(
            e instanceof Error ? e.message : "Не удалось загрузить предметы.",
          ),
      );
    return () => {
      current = false;
    };
  }, [path, workspace]);

  function move(i: number, by: number) {
    const j = i + by;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    setItems(next);
  }
  return (
    <dialog
      className="finals-dialog"
      ref={dialog}
      aria-labelledby="finals-layout-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2 id="finals-layout-title">Предметы в табеле · {className}</h2>
        <button onClick={onClose}>Закрыть</button>
      </header>
      <p>
        Выберите предметы, которые попадут в табель этого класса, и их порядок.
        Скрытый предмет не показывается семье и не входит в средний балл;
        опубликованные итоги при этом не удаляются.
      </p>
      {readOnly && (
        <p role="status">
          Это архивный учебный год: порядок можно смотреть, но не менять.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!layout && !error && <p role="status">Загрузка…</p>}
      {layout && !items.length && (
        <p>В ведомости нет предметов этого класса.</p>
      )}
      {layout && (
        <>
          <ul className="finals-subject-list">
            {items.map((s, i) => (
              <li key={s.id}>
                <label>
                  <input
                    type="checkbox"
                    disabled={readOnly}
                    checked={!hidden.includes(s.id)}
                    onChange={(e) =>
                      setHidden(
                        e.target.checked
                          ? hidden.filter((h) => h !== s.id)
                          : [...hidden, s.id],
                      )
                    }
                  />{" "}
                  В табель: {s.name}
                </label>
                <span className="finals-subject-move">
                  <button
                    disabled={readOnly || i === 0}
                    aria-label={`Поднять: ${s.name}`}
                    onClick={() => move(i, -1)}
                  >
                    Выше
                  </button>
                  <button
                    disabled={readOnly || i === items.length - 1}
                    aria-label={`Опустить: ${s.name}`}
                    onClick={() => move(i, 1)}
                  >
                    Ниже
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <p>
            {layout.version ? "Настроено для класса." : "Порядок по умолчанию."}
          </p>
          <button
            disabled={busy || readOnly || !items.length}
            onClick={() =>
              void run(async () => {
                await api(path, workspace, "PUT", {
                  version: layout.version,
                  order: items.map((s) => s.id),
                  hidden: hidden.filter((h) => items.some((s) => s.id === h)),
                });
                show(await api(path, workspace));
                onSaved();
                setNotice("Порядок и состав предметов сохранены.");
              })
            }
          >
            Сохранить предметы
          </button>
        </>
      )}
    </dialog>
  );
}
