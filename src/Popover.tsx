import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

// A small modal sheet: Escape and the button close it, Tab stays inside, and focus goes back to what opened it.
export default function Popover({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const [opener] = useState(() => document.activeElement as HTMLElement | null);
  const dialog = useRef<HTMLDivElement | null>(null);
  const closeButton = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    closeButton.current?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  const keep = (e: ReactKeyboardEvent) => {
    if (e.key !== "Tab" || !dialog.current) return;
    const items = [...dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (!items.length) return;
    const first = items[0],
      last = items[items.length - 1],
      active = document.activeElement;
    if (e.shiftKey && (active === first || !dialog.current.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (
      !e.shiftKey &&
      (active === last || !dialog.current.contains(active))
    ) {
      e.preventDefault();
      first.focus();
    }
  };
  return (
    <div className="modal-backdrop pupil-popover-layer" onClick={onClose}>
      <div
        ref={dialog}
        className="pupil-popover"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={keep}
      >
        <header>
          <h3>{title}</h3>
          <button
            ref={closeButton}
            type="button"
            className="secondary"
            onClick={onClose}
          >
            Закрыть
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
