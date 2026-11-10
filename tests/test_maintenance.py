import sqlite3

import pytest
from fastapi.testclient import TestClient

from stacktriage.api import Settings, create_app
from stacktriage.cli import main
from stacktriage.db import maintenance as m


@pytest.fixture()
def populated(tmp_path):
    db = tmp_path / "live.db"
    c = TestClient(create_app(Settings(db_path=str(db))))
    from pathlib import Path

    data = (Path(__file__).parent / "fixtures" / "spring_boot.log").read_bytes()
    assert c.post("/api/imports", params={"filename": "s.log"}, content=data).status_code == 201
    return db, c


def count_events(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("select count(*) from events").fetchone()[0]
    finally:
        con.close()


def test_backup_while_open_then_restore_roundtrip(populated, tmp_path):
    db, c = populated
    out = tmp_path / "bk" / "b.db"
    m.backup(db, out)  # app still holds the database open
    assert m.check(out) == [] and count_events(out) == 340
    with pytest.raises(m.MaintenanceError):
        m.backup(db, out)  # refuses to overwrite silently
    c.delete("/api/imports/1")
    c.close()
    c.app.state.engine.dispose()  # restore requires the server to be stopped
    assert count_events(db) == 0
    safety = m.restore(out, db)
    assert count_events(db) == 340 and safety is not None and count_events(safety) == 0


def test_corrupt_backup_is_refused_and_live_db_untouched(populated, tmp_path):
    db, _ = populated
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4000)
    assert m.check(bad)
    with pytest.raises(m.MaintenanceError):
        m.restore(bad, db)
    assert count_events(db) == 340
    junk = tmp_path / "junk.db"
    junk.write_text("not a database")
    with pytest.raises(m.MaintenanceError):
        m.restore(junk, db)


def test_cli_backup_check_restore(populated, tmp_path, capsys):
    db, _ = populated
    out = tmp_path / "cli.db"
    assert main(["backup", str(out), "--db", str(db)]) == 0
    assert main(["check", "--db", str(out)]) == 0
    assert main(["restore", str(out), "--db", str(tmp_path / "fresh.db")]) == 0
    assert count_events(tmp_path / "fresh.db") == 340
    assert main(["check", "--db", str(tmp_path / "missing.db")]) == 1
