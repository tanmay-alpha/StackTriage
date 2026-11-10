import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { fetchHealth, getApiKey, onAuthChange, onUnauthorized, setApiKey } from "../api/client";
import { makeTimeFmt } from "../lib/format";
import type { TimeFmt, TzMode } from "../lib/format";

/* ---------------------------------------------------------------- theme */
export type Theme = "light" | "dark";
const THEME_KEY = "stacktriage.theme";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

interface ThemeCtx {
  theme: Theme;
  toggle: () => void;
}
const ThemeContext = createContext<ThemeCtx | null>(null);

function ThemeProvider({ children }: { children: ReactNode }) {
  const [explicit, setExplicit] = useState<Theme | null>(storedTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setSystem(mq.matches ? "dark" : "light");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const theme = explicit ?? system;
  useEffect(() => {
    if (explicit) document.documentElement.dataset.theme = explicit;
    else delete document.documentElement.dataset.theme;
  }, [explicit]);
  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setExplicit(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
  }, [theme]);
  const value = useMemo(() => ({ theme, toggle }), [theme, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeCtx {
  const c = useContext(ThemeContext);
  if (!c) throw new Error("ThemeProvider missing");
  return c;
}

/* ------------------------------------------------------------- timezone */
const TZ_KEY = "stacktriage.tz";
interface TzCtx {
  fmt: TimeFmt;
  setTz: (t: TzMode) => void;
}
const TzContext = createContext<TzCtx | null>(null);

function TzProvider({ children }: { children: ReactNode }) {
  const [tz, setTzState] = useState<TzMode>(() => {
    try {
      return localStorage.getItem(TZ_KEY) === "local" ? "local" : "utc";
    } catch {
      return "utc";
    }
  });
  const setTz = useCallback((t: TzMode) => {
    setTzState(t);
    try {
      localStorage.setItem(TZ_KEY, t);
    } catch {
      /* ignore */
    }
  }, []);
  const value = useMemo(() => ({ fmt: makeTimeFmt(tz), setTz }), [tz, setTz]);
  return <TzContext.Provider value={value}>{children}</TzContext.Provider>;
}

export function useTime(): TzCtx {
  const c = useContext(TzContext);
  if (!c) throw new Error("TzProvider missing");
  return c;
}

/* ---------------------------------------------------------------- toasts */
export interface ToastItem {
  id: number;
  kind: "info" | "error";
  message: string;
}
interface ToastCtx {
  toasts: ToastItem[];
  push: (message: string, kind?: "info" | "error") => void;
  dismiss: (id: number) => void;
}
const ToastContext = createContext<ToastCtx | null>(null);

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (message: string, kind: "info" | "error" = "info") => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, kind, message }]);
      window.setTimeout(() => dismiss(id), kind === "error" ? 9000 : 4500);
    },
    [dismiss],
  );
  const value = useMemo(() => ({ toasts, push, dismiss }), [toasts, push, dismiss]);
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

export function useToast(): ToastCtx {
  const c = useContext(ToastContext);
  if (!c) throw new Error("ToastProvider missing");
  return c;
}

/* ------------------------------------------------------------------ auth */
interface AuthCtx {
  authRequired: boolean;
  hasKey: boolean;
  /** increments whenever the key changes; include in data-loading deps */
  version: number;
  dialogOpen: boolean;
  openDialog: () => void;
  closeDialog: () => void;
  saveKey: (key: string) => void;
  clearKey: () => void;
}
const AuthContext = createContext<AuthCtx | null>(null);

function AuthProvider({ children }: { children: ReactNode }) {
  const [authRequired, setAuthRequired] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [version, setVersion] = useState(0);
  const hasKey = useSyncExternalStore(onAuthChange, () => getApiKey() !== null);

  useEffect(() => {
    let alive = true;
    fetchHealth()
      .then((h) => {
        if (!alive) return;
        setAuthRequired(h.auth_required);
        if (h.auth_required && getApiKey() === null) setDialogOpen(true);
      })
      .catch(() => {
        /* views surface connectivity errors themselves */
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(
    () =>
      onUnauthorized(() => {
        setAuthRequired(true);
        setDialogOpen(true);
      }),
    [],
  );
  useEffect(() => onAuthChange(() => setVersion((v) => v + 1)), []);

  const value = useMemo<AuthCtx>(
    () => ({
      authRequired,
      hasKey,
      version,
      dialogOpen,
      openDialog: () => setDialogOpen(true),
      closeDialog: () => setDialogOpen(false),
      saveKey: (key: string) => {
        setApiKey(key);
        setDialogOpen(false);
      },
      clearKey: () => {
        setApiKey(null);
        setDialogOpen(true);
      },
    }),
    [authRequired, hasKey, version, dialogOpen],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(AuthContext);
  if (!c) throw new Error("AuthProvider missing");
  return c;
}

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <TzProvider>
        <ToastProvider>
          <AuthProvider>{children}</AuthProvider>
        </ToastProvider>
      </TzProvider>
    </ThemeProvider>
  );
}
