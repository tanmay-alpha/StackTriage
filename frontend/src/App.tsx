import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { verifyKey } from "./api/client";
import type { Overview } from "./api/types";
import { api } from "./api/client";
import { Button, Dialog, EmptyState, ErrorState, LinkButton, LoadingBlock } from "./components/ui";
import { recallImport } from "./lib/lastImport";
import { useAsync } from "./lib/useAsync";
import { AppProviders, useAuth, useTheme, useTime, useToast } from "./state/app";
import { ImportView } from "./views/ImportView";
import { IncidentsView } from "./views/IncidentsView";
import { InvestigationView } from "./views/InvestigationView";
import { OverviewView } from "./views/OverviewView";

function SunMoon({ dark }: { dark: boolean }) {
  return dark ? (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false">
      <circle cx="10" cy="10" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10 2v2.2M10 15.8V18M2 10h2.2M15.8 10H18M4.3 4.3l1.6 1.6M14.1 14.1l1.6 1.6M15.7 4.3l-1.6 1.6M5.9 14.1l-1.6 1.6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  ) : (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M16.5 12.2A7 7 0 0 1 7.8 3.5a7 7 0 1 0 8.7 8.7z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

function Header() {
  const { theme, toggle } = useTheme();
  const { fmt, setTz } = useTime();
  const auth = useAuth();
  const [menu, setMenu] = useState(false);
  const loc = useLocation();
  useEffect(() => setMenu(false), [loc.pathname]);
  const onIncidents = loc.pathname.startsWith("/imports/") || loc.pathname === "/incidents";
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link to="/" className="brand" aria-label="StackTriage home">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true" focusable="false">
            <rect width="32" height="32" rx="6" fill="currentColor" />
            <path d="M7 21h4v4H7zM14 13h4v12h-4zM21 7h4v18h-4z" fill="var(--brand-ink)" />
          </svg>
          <span>StackTriage</span>
        </Link>
        <button type="button" className="btn btn-ghost btn-sm nav-toggle" aria-expanded={menu} aria-controls="site-nav" onClick={() => setMenu((m) => !m)}>
          Menu
        </button>
        <nav id="site-nav" className={`nav${menu ? " nav-open" : ""}`} aria-label="Primary">
          <NavLink to="/" end className="nav-link">
            Overview
          </NavLink>
          <NavLink to="/import" className="nav-link">
            Import
          </NavLink>
          <NavLink to="/incidents" className={`nav-link${onIncidents ? " active" : ""}`}>
            Incidents
          </NavLink>
        </nav>
        <div className="topbar-tools">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setTz(fmt.tz === "utc" ? "local" : "utc")} title="Switch between UTC and your local time zone" aria-label={`Times shown in ${fmt.label}. Switch to ${fmt.tz === "utc" ? "local time" : "UTC"}`}>
            <span className="tz-chip">{fmt.label}</span>
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={toggle} aria-pressed={theme === "dark"} title="Toggle dark theme">
            <SunMoon dark={theme === "dark"} />
            <span className="tool-label">Dark theme</span>
          </button>
          {auth.authRequired ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={auth.openDialog} title={auth.hasKey ? "API key is set for this browser session" : "Enter API key"}>
              API key{auth.hasKey ? "" : " needed"}
            </button>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function ApiKeyDialog() {
  const auth = useAuth();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (auth.dialogOpen) {
      setValue("");
      setError(null);
      window.setTimeout(() => input.current?.focus(), 30);
    }
  }, [auth.dialogOpen]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const key = value.trim();
    if (!key) {
      setError("Enter the API key configured on the server.");
      input.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (await verifyKey(key)) auth.saveKey(key);
      else {
        setError("That key was not accepted by the server.");
        input.current?.focus();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not verify the key.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={auth.dialogOpen} onClose={auth.closeDialog} title="API key required">
      <form onSubmit={submit} className="stack-md" noValidate>
        <p className="muted">This StackTriage server requires an API key. It is kept in this browser tab&rsquo;s session storage only and sent as a bearer token.</p>
        <div className="field">
          <label className="field-label" htmlFor="api-key-input">
            API key
          </label>
          <input id="api-key-input" ref={input} className="input mono" type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? "api-key-error" : undefined} />
          {error ? (
            <p id="api-key-error" className="field-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="dialog-foot">
          {auth.hasKey ? (
            <Button variant="ghost" onClick={() => auth.clearKey()}>
              Forget stored key
            </Button>
          ) : null}
          <Button onClick={auth.closeDialog}>Cancel</Button>
          <Button type="submit" variant="primary" busy={busy}>
            Unlock
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function Toasts() {
  const { toasts, dismiss } = useToast();
  return (
    <div className="toasts" role="region" aria-label="Notifications">
      <div aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
            <span>{t.message}</span>
            <button type="button" className="toast-x" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
              <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
                <path d="M2 2l8 8M10 2 2 10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Move focus to <main> on client-side navigation so keyboard and screen-reader users land on new content. */
function RouteFocus() {
  const loc = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.getElementById("main")?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [loc.pathname]);
  return null;
}

function IncidentsRedirect() {
  const { data, error, loading, reload } = useAsync<Overview>("overview", (signal) => api.get<Overview>("/overview", undefined, signal));
  const remembered = recallImport();
  if (loading) return <LoadingBlock label="Loading incidents" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const id = remembered ?? data?.latest_import?.id;
  if (id !== undefined && id !== null) return <Navigate to={`/imports/${id}/incidents`} replace />;
  return (
    <>
      <h1 className="page-title">Incidents</h1>
      <EmptyState title="No imports yet" action={<LinkButton to="/import" variant="primary">Import a log file</LinkButton>}>
        Incidents are grouped from an imported log. Import one to start triage.
      </EmptyState>
    </>
  );
}

function NotFound() {
  return (
    <>
      <h1 className="page-title">Page not found</h1>
      <EmptyState title="That page does not exist" action={<LinkButton to="/">Go to Overview</LinkButton>}>
        Check the address, or head back to the overview.
      </EmptyState>
    </>
  );
}

export function App() {
  return (
    <BrowserRouter useTransitions={false}>
      <AppProviders>
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        <Header />
        <main id="main" tabIndex={-1} className="main">
          <Routes>
            <Route path="/" element={<OverviewView />} />
            <Route path="/import" element={<ImportView />} />
            <Route path="/incidents" element={<IncidentsRedirect />} />
            <Route path="/imports/:importId/incidents" element={<IncidentsView />} />
            <Route path="/imports/:importId/incidents/:fingerprint" element={<InvestigationView />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
        <footer className="footer">
          <span>StackTriage</span>
          <span className="muted">Statistical findings are leads to check, not causes.</span>
        </footer>
        <RouteFocus />
        <Toasts />
        <ApiKeyDialog />
      </AppProviders>
    </BrowserRouter>
  );
}
