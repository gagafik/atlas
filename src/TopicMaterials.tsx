import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api";
import Library from "./TeachingMaterialsLoader";
import { MaterialView } from "./LessonMaterials";
import {
  materialSnapshot,
  type MaterialDetail,
  type MaterialSnapshot,
} from "./teachingMaterials";
import type { PlanMaterialReference, PlanMaterialStatus } from "./curriculum";

function Picker({
  workspace,
  close,
  choose,
}: {
  workspace: string;
  close: () => void;
  choose: (item: MaterialSnapshot) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => {
      element.close();
    };
  }, []);
  return createPortal(
    <dialog
      className="topic-library-dialog"
      ref={dialog}
      onCancel={close}
      onSubmit={(e) => e.stopPropagation()}
      aria-label="Выбор материала для темы"
    >
      <button type="button" onClick={close}>
        Закрыть выбор для темы
      </button>
      <p>
        Выберите редакцию, открытую сотрудникам. До десяти материалов в теме.
      </p>
      <Library workspace={workspace} sharedOnly onChoose={choose} />
    </dialog>,
    document.body,
  );
}

export default function TopicMaterials({
  workspace,
  items,
  status,
  disabled,
  onChange,
}: {
  workspace: string;
  items: PlanMaterialReference[];
  status: Record<string, PlanMaterialStatus>;
  disabled: boolean;
  onChange: (items: PlanMaterialReference[]) => void;
}) {
  const [choosing, setChoosing] = useState(false),
    [preview, setPreview] = useState<MaterialSnapshot | null>(null);
  const [local, setLocal] = useState<Record<string, PlanMaterialStatus>>({}),
    [error, setError] = useState("");
  const sequence = useRef(0);
  useEffect(
    () => () => {
      sequence.current++;
    },
    [],
  );
  async function open(ref: PlanMaterialReference) {
    const id = ++sequence.current;
    setPreview(null);
    setError("");
    try {
      const detail: MaterialDetail = await api(
        `journal/library/${ref.id}?version=${ref.version}`,
        workspace,
      );
      if (sequence.current === id) {
        if (!detail.canUseInPlan)
          throw new Error(
            "Материал больше не открыт сотрудникам. Обновите план.",
          );
        setPreview(materialSnapshot(detail));
      }
    } catch (e) {
      if (sequence.current === id) setError((e as Error).message);
    }
  }
  return (
    <section className="topic-materials" aria-label="Материалы темы">
      <h5>Материалы темы</h5>
      {items.map((ref) => {
        const key = `${ref.id}:${ref.version}`,
          item = local[key] || status[key];
        return (
          <div className="curriculum-toolbar" key={ref.id}>
            <span>
              {item?.available
                ? item.title
                : "Материал недоступен для общего плана"}{" "}
              · версия {ref.version}
            </span>
            <button
              type="button"
              disabled={disabled || !item?.available}
              onClick={() => void open(ref)}
            >
              Просмотр материала
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                sequence.current++;
                setPreview(null);
                onChange(items.filter((r) => r.id !== ref.id));
              }}
            >
              Убрать материал из темы
            </button>
          </div>
        );
      })}
      {error && <p role="alert">{error}</p>}
      {preview && (
        <div>
          <MaterialView item={preview} workspace={workspace} />
          <button type="button" onClick={() => setPreview(null)}>
            Закрыть просмотр
          </button>
        </div>
      )}
      {!disabled && (
        <button type="button" onClick={() => setChoosing((v) => !v)}>
          {choosing ? "Закрыть выбор для темы" : "Добавить материал к теме"}
        </button>
      )}
      {choosing && !disabled && (
        <Picker
          workspace={workspace}
          close={() => setChoosing(false)}
          choose={(item) => {
            if (items.length >= 10 && !items.some((r) => r.id === item.id)) {
              setChoosing(false);
              setError("В теме может быть до десяти материалов.");
              return;
            }
            onChange([
              ...items.filter((r) => r.id !== item.id),
              { id: item.id, version: item.version },
            ]);
            setLocal((previous) => ({
              ...previous,
              [`${item.id}:${item.version}`]: {
                id: item.id,
                version: item.version,
                available: true,
                title: item.title,
              },
            }));
            setChoosing(false);
            setError("");
          }}
        />
      )}
    </section>
  );
}
