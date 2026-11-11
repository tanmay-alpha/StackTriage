"""Focused API integration checks for the synthetic demonstration script."""
import io
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

import demo
from stacktriage.api import Settings, create_app


@pytest.fixture()
def client(monkeypatch):
    app = create_app(Settings(db_path=":memory:", api_key="synthetic-demo-key-1234"))
    with TestClient(app, raise_server_exceptions=False) as client:
        def open_request(request, timeout):
            response = client.request(request.get_method(), request.full_url,
                                      content=request.data, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "request failed", {}, None)
            return io.BytesIO(response.content)

        monkeypatch.setattr(demo, "urlopen", open_request)
        monkeypatch.setenv("STACKTRIAGE_API_KEY", "synthetic-demo-key-1234")
        yield client
    app.state.engine.dispose()


def test_demo_is_repeatable_and_preserves_review_state(client, capsys):
    assert demo.main(["--url", "http://testserver/"]) == 0
    imports = client.get("/api/imports", headers={"X-API-Key": "synthetic-demo-key-1234"}).json()
    assert imports["total"] == 2
    spring = next(item for item in imports["items"] if item["filename"] == "spring_boot.log")
    headers = {"X-API-Key": "synthetic-demo-key-1234"}
    incidents = client.get(f"/api/imports/{spring['id']}/incidents", headers=headers).json()
    fingerprint = incidents["items"][0]["fingerprint"]
    status_path = f"/api/incidents/{fingerprint}/status"
    assert client.put(status_path, json={"status": "investigating"}, headers=headers).status_code == 200
    assert demo.main(["--url", "http://testserver"]) == 0
    assert client.get("/api/imports", headers=headers).json()["total"] == 2
    detail = client.get(f"/api/imports/{spring['id']}/incidents/{fingerprint}", headers=headers).json()
    assert detail["status"] == "investigating"
    output = capsys.readouterr().out
    assert output.count("duplicate verified") == 4
    assert "340 events, 4 groups" in output
    assert "550 events, 2 groups" in output


def test_demo_reports_missing_key_without_uploading(client, monkeypatch, capsys):
    monkeypatch.delenv("STACKTRIAGE_API_KEY")
    assert demo.main(["--url", "http://testserver"]) == 1
    assert "HTTP 401" in capsys.readouterr().err
    assert client.get("/api/imports", headers={"X-API-Key": "synthetic-demo-key-1234"}).json()["total"] == 0


def test_demo_rejects_fixture_count_mismatch(monkeypatch):
    monkeypatch.setattr(demo, "request_json", lambda *args: {
        "id": 1, "format": "java", "event_count": 0, "incident_count": 4, "duplicate": False,
    })
    with pytest.raises(RuntimeError, match="expected.*received"):
        demo.run("http://testserver", None)


def test_demo_rejects_broken_duplicate_detection(monkeypatch):
    monkeypatch.setattr(demo, "request_json", lambda *args: {
        "id": 1, "format": "java", "event_count": 340, "incident_count": 4, "duplicate": False,
    })
    with pytest.raises(RuntimeError, match="duplicate detection"):
        demo.run("http://testserver", None)
