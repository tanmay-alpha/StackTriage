import { useCallback, useEffect, useId, useRef } from "react";
import type { ButtonHTMLAttributes, KeyboardEvent, ReactNode, SyntheticEvent } from "react";
import { Link } from "react-router-dom";
import type { Status } from "../api/types";
import { classificationLabel } from "../lib/format";

/* ---------------------------------------------------------------- icons */
function Glyph({ children }: { children: ReactNode }) {
  return (
    <svg className="glyph" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

export function LevelIcon({ level }: { level: string }) {
  switch (level.toLowerCase()) {
    case "critical":
      return (
        <Glyph>
          <path d="M6 .8 11.2 6 6 11.2.8 6z" fill="currentColor" />
          <path d="M6 3.2v3.4M6 8v.9" stroke="var(--glyph-ink)" strokeWidth="1.3" strokeLinecap="round" />
        </Glyph>
      );
    case "error":
      return (
        <Glyph>
          <circle cx="6" cy="6" r="5.2" fill="currentColor" />
          <path d="M4 4l4 4M8 4 4 8" stroke="var(--glyph-ink)" strokeWidth="1.3" strokeLinecap="round" />
        </Glyph>
      );
    case "warning":
      return (
        <Glyph>
          <path d="M6 1 11.4 10.6H.6z" fill="currentColor" />
          <path d="M6 4.6v2.9M6 8.9v.1" stroke="var(--glyph-ink)" strokeWidth="1.2" strokeLinecap="round" />
        </Glyph>
      );
    case "info":
      return (
        <Glyph>
          <circle cx="6" cy="6" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M6 5.3v3M6 3.4v.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </Glyph>
      );
    case "debug":
      return (
        <Glyph>
          <rect x="2.5" y="2.5" width="7" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" />
        </Glyph>
      );
    default:
      return (
        <Glyph>
          <circle cx="6" cy="6" r="1.6" fill="currentColor" />
        </Glyph>
      );
  }
}

/* --------------------------------------------------------------- badges */
export function LevelBadge({ level }: { level: string }) {
  const l = level.toLowerCase();
  return (
    <span className={`badge lvl lvl-${l}`}>
      <LevelIcon level={l} />
      {level.toUpperCase()}
    </span>
  );
}

export const STATUS_LABEL: Record<Status, string> = {
  open: "Open",
  investigating: "Investigating",
  resolved: "Resolved",
  ignored: "Ignored",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge st st-${status}`}>{STATUS_LABEL[status]}</span>;
}

export function ActivityBadge({ state }: { state: "ongoing" | "ended" | "unknown" }) {
  const label = state === "ongoing" ? "Ongoing" : state === "ended" ? "Ended" : "Unknown";
  return (
    <span className={`badge act act-${state}`}>
      <span className="act-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export function ClassificationBadge({ value }: { value: string }) {
  return (
    <span className="badge cls">
      <span className="badge-k">Type</span>
      {classificationLabel(value)}
    </span>
  );
}

export function SeverityBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge sev sev-none">Severity: none</span>;
  return (
    <span className={`badge sev sev-${value}`}>
      <span className="badge-k">Severity</span>
      {value}
    </span>
  );
}

/* --------------------------------------------------------------- button */
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "md" | "sm";
  busy?: boolean;
};

export function Button({ variant = "secondary", size = "md", busy, className, children, disabled, type = "button", ...rest }: ButtonProps) {
  return (
    <button type={type} className={`btn btn-${variant} btn-${size}${className ? ` ${className}` : ""}`} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
      {busy ? <Spinner small /> : null}
      {children}
    </button>
  );
}

export function LinkButton({ to, children, variant = "secondary", size = "md" }: { to: string; children: ReactNode; variant?: "primary" | "secondary" | "ghost"; size?: "md" | "sm" }) {
  return (
    <Link to={to} className={`btn btn-${variant} btn-${size}`}>
      {children}
    </Link>
  );
}

/* --------------------------------------------------------- loading etc. */
export function Spinner({ small, label }: { small?: boolean; label?: string }) {
  return (
    <span className={`spinner${small ? " spinner-sm" : ""}`} role={label ? "status" : undefined} aria-label={label}>
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeDasharray="42 60" />
      </svg>
    </span>
  );
}

export function Skeleton({ h = 16, w = "100%", className }: { h?: number; w?: number | string; className?: string }) {
  return <span className={`skeleton${className ? ` ${className}` : ""}`} style={{ height: h, width: w }} aria-hidden="true" />;
}

export function LoadingBlock({ label = "Loading", rows = 4 }: { label?: string; rows?: number }) {
  return (
    <div className="loading-block" role="status" aria-live="polite" aria-label={label}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} h={i === 0 ? 20 : 14} w={i === 0 ? "40%" : `${92 - i * 9}%`} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state state-empty">
      <svg className="state-art" viewBox="0 0 48 48" width="40" height="40" aria-hidden="true" focusable="false">
        <rect x="6" y="9" width="36" height="30" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M12 18h14M12 24h24M12 30h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <h2 className="state-title">{title}</h2>
      {children ? <p className="state-body">{children}</p> : null}
      {action ? <div className="state-action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", error, onRetry, action }: { title?: string; error: Error; onRetry?: () => void; action?: ReactNode }) {
  return (
    <div className="state state-error" role="alert">
      <svg className="state-art" viewBox="0 0 48 48" width="40" height="40" aria-hidden="true" focusable="false">
        <circle cx="24" cy="24" r="18" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M24 14v13M24 32v2" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
      <h2 className="state-title">{title}</h2>
      <p className="state-body">{error.message}</p>
      <div className="state-action">
        {onRetry ? <Button onClick={onRetry}>Retry</Button> : null}
        {action}
      </div>
    </div>
  );
}

export function Callout({ kind = "info", title, children }: { kind?: "info" | "warning" | "error" | "success"; title?: string; children: ReactNode }) {
  return (
    <div className={`callout callout-${kind}`} role={kind === "error" ? "alert" : "note"}>
      {title ? <strong className="callout-title">{title}</strong> : null}
      <div className="callout-body">{children}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- panel */
export function Panel({ title, actions, children, className, id, headingLevel = 2 }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string; headingLevel?: 2 | 3 }) {
  const hid = useId();
  const H = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className={`panel${className ? ` ${className}` : ""}`} aria-labelledby={title ? hid : undefined} id={id}>
      {title || actions ? (
        <header className="panel-head">
          {title ? (
            <H className="panel-title" id={hid}>
              {title}
            </H>
          ) : null}
          {actions ? <div className="panel-actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="panel-body">{children}</div>
    </section>
  );
}

/* ----------------------------------------------------------------- field */
export function Field({ label, hint, children, htmlFor, className }: { label: string; hint?: string; children: ReactNode; htmlFor?: string; className?: string }) {
  return (
    <div className={`field${className ? ` ${className}` : ""}`}>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ tabs */
export interface TabDef {
  id: string;
  label: ReactNode;
  panel: ReactNode;
}

export function Tabs({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (id: string) => void; label: string }) {
  const base = useId();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const idx = tabs.findIndex((t) => t.id === active);
    let next = -1;
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next >= 0) {
      e.preventDefault();
      const t = tabs[next];
      if (t) {
        onChange(t.id);
        refs.current[next]?.focus();
      }
    }
  };
  return (
    <div className="tabs">
      <div className="tablist" role="tablist" aria-label={label} onKeyDown={onKey}>
        {tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            type="button"
            id={`${base}-tab-${t.id}`}
            aria-selected={t.id === active}
            aria-controls={`${base}-panel-${t.id}`}
            tabIndex={t.id === active ? 0 : -1}
            className="tab"
            onClick={() => onChange(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tabs.map((t) => (
        <div key={t.id} role="tabpanel" id={`${base}-panel-${t.id}`} aria-labelledby={`${base}-tab-${t.id}`} hidden={t.id !== active} className="tabpanel">
          {t.id === active ? t.panel : null}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- dialog */
export function Dialog({ open, onClose, title, children, footer, dismissible = true, labelledById }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; dismissible?: boolean; labelledById?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  const onCancel = useCallback(
    (e: SyntheticEvent) => {
      e.preventDefault();
      if (dismissible) onClose();
    },
    [dismissible, onClose],
  );
  const trapTab = useCallback((e: KeyboardEvent<HTMLDialogElement>) => {
    if (e.key !== "Tab") return;
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter((el) => !el.hasAttribute("disabled") && el.offsetParent !== null);
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !e.currentTarget.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !e.currentTarget.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }, []);
  return (
    <dialog ref={ref} className="dialog" onKeyDown={trapTab} aria-labelledby={labelledById ?? titleId} onCancel={onCancel} onClose={() => open && dismissible && onClose()} onClick={(e) => { if (e.target === e.currentTarget && dismissible) onClose(); }}>
      {open ? (
        <div className="dialog-inner">
          <h2 className="dialog-title" id={titleId}>
            {title}
          </h2>
          <div className="dialog-body">{children}</div>
          {footer ? <div className="dialog-foot">{footer}</div> : null}
        </div>
      ) : null}
    </dialog>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel, busy }: { open: boolean; title: string; body: ReactNode; confirmLabel: string; onConfirm: () => void; onCancel: () => void; busy?: boolean }) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="danger" onClick={onConfirm} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {body}
    </Dialog>
  );
}

/* ------------------------------------------------------------ pagination */
export function Pagination({ total, limit, offset, onOffset, noun = "items" }: { total: number; limit: number; offset: number; onOffset: (o: number) => void; noun?: string }) {
  if (total === 0) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pagination" aria-label={`${noun} pagination`}>
      <span className="pagination-info" aria-live="polite">
        {from.toLocaleString("en-US")}&ndash;{to.toLocaleString("en-US")} of {total.toLocaleString("en-US")}
      </span>
      <div className="pagination-btns">
        <Button size="sm" disabled={offset <= 0} onClick={() => onOffset(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button size="sm" disabled={offset + limit >= total} onClick={() => onOffset(offset + limit)}>
          Next
        </Button>
      </div>
    </nav>
  );
}

/* ----------------------------------------------------------- misc helpers */
export function SrOnly({ children }: { children: ReactNode }) {
  return <span className="sr-only">{children}</span>;
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={`mono${className ? ` ${className}` : ""}`}>{children}</span>;
}
