// Slide-over drawers, confirm dialogs and toasts. Frosted backdrop, Esc to close, focus moved in.
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button, Icon, IconButton, cx } from "./ui";

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [open, onClose]);
}

function useFocusOnOpen(open: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const target = ref.current?.querySelector<HTMLElement>("[data-autofocus], input, select, textarea, button");
    (target ?? ref.current)?.focus();
    return () => previous?.focus?.();
  }, [open]);
  return ref;
}

export function Drawer({ open, onClose, title, subtitle, children, footer, width = "max-w-xl" }:
  { open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode; width?: string }) {
  useEscape(open, onClose);
  const ref = useFocusOnOpen(open);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 animate-fade-in bg-ink-900/30 backdrop-blur-sm" onClick={onClose} />
      <div ref={ref} tabIndex={-1} className={cx("relative flex h-full w-full animate-slide-in flex-col bg-white shadow-pop outline-none", width)}>
        <div className="flex items-start justify-between gap-4 border-b border-ink-100 px-6 py-4">
          <div>
            <h2 className="text-[17px] font-semibold text-ink-900">{title}</h2>
            {subtitle && <p className="text-[13px] text-ink-500">{subtitle}</p>}
          </div>
          <IconButton icon="close-line" label="Close" onClick={onClose} />
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-ink-100 bg-ink-50/60 px-6 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({ open, title, message, confirmLabel, cancelLabel = "Cancel", tone = "primary", busy, onConfirm, onClose }:
  { open: boolean; title: string; message: ReactNode; confirmLabel: string; cancelLabel?: string; tone?: "primary" | "danger"; busy?: boolean; onConfirm: () => void; onClose: () => void }) {
  useEscape(open, onClose);
  const ref = useFocusOnOpen(open);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center p-4" role="alertdialog" aria-modal="true">
      <div className="absolute inset-0 animate-fade-in bg-ink-900/30 backdrop-blur-sm" onClick={onClose} />
      <div ref={ref} tabIndex={-1} className="relative w-full max-w-md animate-pop-in rounded-xl bg-white p-6 shadow-pop outline-none">
        <h2 className="text-[17px] font-semibold text-ink-900">{title}</h2>
        <div className="mt-2 text-ink-600">{message}</div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} data-autofocus>{cancelLabel}</Button>
          <Button variant={tone} onClick={onConfirm} loading={busy}>{confirmLabel}</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------- toasts
type Toast = { id: number; tone: "success" | "error" | "info"; title: string; message?: string };
const ToastContext = createContext<(t: Omit<Toast, "id">) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all, { ...t, id }]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), t.tone === "error" ? 7000 : 4500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={cx("pointer-events-auto flex animate-pop-in gap-3 rounded-xl border bg-white p-3.5 shadow-pop",
              t.tone === "success" ? "border-emerald-200" : t.tone === "error" ? "border-red-200" : "border-brand-200")} role={t.tone === "error" ? "alert" : "status"}>
              <Icon name={t.tone === "success" ? "checkbox-circle-fill" : t.tone === "error" ? "error-warning-fill" : "information-fill"}
                className={cx("mt-0.5 text-xl", t.tone === "success" ? "text-emerald-600" : t.tone === "error" ? "text-red-600" : "text-brand-600")} />
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-ink-900">{t.title}</p>
                {t.message && <p className="text-[13px] text-ink-600">{t.message}</p>}
              </div>
              <button className="text-ink-400 hover:text-ink-700" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))} aria-label="Dismiss"><Icon name="close-line" /></button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
