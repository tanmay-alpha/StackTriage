"""Backup, restore and integrity checks using SQLite's online backup API."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .engine import MIGRATIONS


class MaintenanceError(Exception):
    pass


def _ro_uri(p: Path) -> str:
    return p.resolve().as_uri() + "?mode=ro"  # as_uri percent-encodes '?', '#', '%' in paths


def check(path: str | Path) -> list[str]:
    """Return integrity problems; an empty list means the database is sound."""
    p = Path(path)
    if not p.exists():
        raise MaintenanceError(f"{p} does not exist")
    con = sqlite3.connect(_ro_uri(p), uri=True)
    problems: list[str] = []
    try:
        problems = [r[0] for r in con.execute("PRAGMA integrity_check") if r[0] != "ok"]
        problems += [f"foreign key violation in {r[0]} (row {r[1]})" for r in con.execute("PRAGMA foreign_key_check")]
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            problems.append(f"schema v{version} is newer than supported v{len(MIGRATIONS)}")
    except sqlite3.DatabaseError as e:
        problems.append(f"not a readable SQLite database: {e}")
    finally:
        con.close()
    return problems


def _copy(src: Path, dst: Path) -> None:
    s = sqlite3.connect(_ro_uri(src), uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def backup(db_path: str | Path, out_path: str | Path, *, overwrite: bool = False) -> None:
    """Consistent online copy (safe while the app runs), verified before returning."""
    src, out = Path(db_path), Path(out_path)
    if not src.exists():
        raise MaintenanceError(f"{src} does not exist")
    if out.exists() and not overwrite:
        raise MaintenanceError(f"{out} already exists (use --force to overwrite)")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".partial")
    tmp.unlink(missing_ok=True)
    _copy(src, tmp)
    problems = check(tmp)
    if problems:
        tmp.unlink(missing_ok=True)
        raise MaintenanceError("backup failed verification: " + "; ".join(problems[:3]))
    tmp.replace(out)


def restore(backup_path: str | Path, db_path: str | Path) -> Path | None:
    """Replace the database with a verified backup. Stop the app first.

    The previous database (if any) is kept next to it as `<name>.before-restore`.
    Returns the path of that safety copy.
    """
    bak, dst = Path(backup_path), Path(db_path)
    problems = check(bak)
    if problems:
        raise MaintenanceError("backup is not restorable: " + "; ".join(problems[:3]))
    safety: Path | None = None
    if dst.exists():
        safety = dst.with_name(dst.name + ".before-restore")
        _copy(dst, safety)
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        dst.with_name(dst.name + suffix).unlink(missing_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".restoring")
    tmp.unlink(missing_ok=True)
    _copy(bak, tmp)
    tmp.replace(dst)
    return safety
