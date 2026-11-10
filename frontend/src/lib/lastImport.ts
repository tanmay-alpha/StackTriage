const KEY = "stacktriage.lastImport";

export function rememberImport(id: number): void {
  try {
    sessionStorage.setItem(KEY, String(id));
  } catch {
    /* storage unavailable */
  }
}

export function recallImport(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function forgetImport(id: number): void {
  try {
    if (sessionStorage.getItem(KEY) === String(id)) sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}
