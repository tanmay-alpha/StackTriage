"""Import comparison: statistics, edge cases, ground-truth fixtures and API behaviour."""
import csv
import io
import json
from pathlib import Path

import pytest
from sqlalchemy import event

from stacktriage import compare as cmp
from stacktriage.compare import GroupStats, ImportStats

from test_api import KEY, client, make_client, upload  # noqa: F401  (client is a pytest fixture)

FIX = Path(__file__).parent / "fixtures"
TRUTH = json.loads((FIX / "expected" / "compare.json").read_text(encoding="utf-8"))
MIN = 60_000


def imp(id=1, events=1000, minutes=60.0, untimed=0, start=0, services=None, fmt="python", truncated=False,
        timestamps=True) -> ImportStats:
    return ImportStats(id=id, format=fmt, event_count=events, untimed_events=untimed,
                       first_ms=start if timestamps else None,
                       last_ms=start + int(minutes * MIN) if timestamps else None,
                       services=services or {"api": events}, truncated=truncated)


def grp(fp="f1", count=10, untimed=0, title="Boom") -> GroupStats:
    return GroupStats(fingerprint=fp, title=title, service="api", exc_type=None, frame=None, max_level=40,
                      count=count, untimed_count=untimed, first_ms=0, last_ms=MIN)


def only(result: cmp.Comparison, fp: str = "f1") -> cmp.Item:
    return next(i for i in result.items if i.fingerprint == fp)


# ------------------------------------------------------------------ statistics
@pytest.mark.parametrize(("x", "n", "p", "expected"), [
    (0, 5, 0.5, 0.0625), (3, 10, 0.5, 0.34375), (10, 10, 0.5, 0.001953125), (5, 10, 0.5, 1.0), (0, 0, 0.3, 1.0),
])
def test_exact_binomial_matches_known_values(x, n, p, expected):
    assert cmp.binom_two_sided(x, n, p) == pytest.approx(expected, rel=1e-9)


def test_exact_binomial_agrees_with_scipy_on_asymmetric_null():
    stats = pytest.importorskip("scipy.stats")
    for x, n, p in [(150, 162, 0.67), (20, 100, 0.67), (40, 46, 0.668), (3, 5, 0.67), (60, 90, 0.668)]:
        assert cmp.binom_two_sided(x, n, p) == pytest.approx(stats.binomtest(x, n, p).pvalue, rel=1e-6)


def test_benjamini_hochberg_adjustment():
    # textbook example: m=5
    q = cmp.bh_adjust([0.01, 0.04, 0.03, 0.005, 0.5])
    assert q == pytest.approx([0.025, 0.05, 0.05, 0.025, 0.5])
    assert cmp.bh_adjust([]) == []


# ------------------------------------------------------------- normalization
def test_larger_capture_is_not_an_increase():
    """Same error rate in a capture with 3x the traffic and 3x the length: no change on either basis."""
    r = cmp.compare(imp(1, 1000, 60), imp(2, 3000, 180, start=200 * MIN), [grp(count=20)], [grp(count=60)])
    it = only(r)
    assert it.category == "recurring" and it.change == "no_clear_change"
    assert it.rate_ratio == pytest.approx(1.0) and it.percent_change == pytest.approx(0.0)
    assert it.target.per_1k_events == pytest.approx(it.baseline.per_1k_events)
    assert it.target.per_minute == pytest.approx(it.baseline.per_minute) and not it.bases_disagree


def test_traffic_growth_flags_disagreeing_bases():
    """Errors double only because traffic doubled: no change per 1,000 events, but per minute it doubles."""
    b, t = imp(1, 1000, 60), imp(2, 2000, 60, start=120 * MIN)
    ev = only(cmp.compare(b, t, [grp(count=100)], [grp(count=200)], basis="events"))
    tm = only(cmp.compare(b, t, [grp(count=100)], [grp(count=200)], basis="time"))
    assert ev.change == "no_clear_change" and ev.bases_disagree
    assert tm.change == "increased" and tm.rate_ratio == pytest.approx(2.0) and tm.bases_disagree


def test_unequal_durations_use_coverage_and_warn():
    b, t = imp(1, 1200, 60), imp(2, 200, 10, start=120 * MIN)
    r = cmp.compare(b, t, [grp(count=60)], [grp(count=10)], basis="time")
    it = only(r)
    assert it.baseline.per_minute == pytest.approx(1.0) and it.target.per_minute == pytest.approx(1.0)
    assert it.change == "no_clear_change"
    assert "duration_mismatch" in {w.code for w in r.warnings}


def test_significant_increase_and_decrease():
    b, t = imp(1, 1000, 60), imp(2, 1000, 60, start=120 * MIN)
    r = cmp.compare(b, t, [grp("up", 10), grp("down", 100)], [grp("up", 60), grp("down", 20)])
    assert only(r, "up").change == "increased" and only(r, "up").evidence == "significant"
    assert only(r, "down").change == "decreased" and only(r, "down").percent_change == pytest.approx(-80.0)


def test_small_fold_change_is_not_reported_even_when_significant():
    """Large counts make a 1.2x change statistically significant; it stays below the 1.5x effect threshold."""
    b, t = imp(1, 100_000, 60), imp(2, 100_000, 60, start=120 * MIN)
    it = only(cmp.compare(b, t, [grp(count=10_000)], [grp(count=12_000)]))
    assert it.q_value < 1e-6 and it.evidence == "significant" and it.change == "no_clear_change"


def test_low_frequency_change_is_insufficient_data():
    b, t = imp(1, 1000, 60), imp(2, 1000, 60, start=120 * MIN)
    r = cmp.compare(b, t, [grp("rare", 1)], [grp("rare", 4), grp("once", 1)])
    assert only(r, "rare").change == "insufficient_data" and only(r, "rare").evidence == "low_sample"
    once = only(r, "once")
    assert once.category == "new" and once.evidence == "low_sample" and once.change is None


def test_many_groups_are_corrected_for_multiple_testing():
    """Each group alone gives p ~ 0.02, which BH across 40 equal tests does not accept as a discovery."""
    b, t = imp(1, 1000, 60), imp(2, 1000, 60, start=120 * MIN)
    base = [grp(f"g{i}", 4) for i in range(40)]
    target = [grp(f"g{i}", 14) for i in range(40)]
    r = cmp.compare(b, t, base, target)
    assert all(0.01 < i.p_value < 0.05 for i in r.items)
    assert all(i.q_value == pytest.approx(i.p_value) for i in r.items)  # all equal p: BH leaves them unchanged
    single = only(cmp.compare(b, t, base[:1], target[:1]), "g0")
    assert single.change == "increased"  # same counts alone are a discovery
    mixed = cmp.compare(b, t, base + [grp("z", 50)], target + [grp("z", 50)])
    assert only(mixed, "g0").q_value > only(mixed, "g0").p_value  # an extra null test raises the adjusted values


# --------------------------------------------------------- missing timestamps
def test_missing_timestamps_disable_time_basis_without_breaking_event_rates():
    b = imp(1, 500, timestamps=False, untimed=500)
    t = imp(2, 500, 60, start=120 * MIN)
    r = cmp.compare(b, t, [grp(count=10, untimed=10)], [grp(count=30)])
    it = only(r)
    # per-minute rates are per side: the timestamped target keeps its own rate, but the two cannot be compared
    assert not r.time_comparable and it.baseline.per_minute is None and it.target.per_minute == pytest.approx(0.5)
    assert it.change == "increased"  # still decided on the events basis
    codes = {w.code for w in r.warnings}
    assert {"time_basis_unavailable", "baseline_missing_timestamps"} <= codes
    with pytest.raises(cmp.BasisUnavailable):
        cmp.compare(b, t, [], [], basis="time")


@pytest.mark.parametrize("kw", [{"minutes": 0.5}, {"untimed": 200}, {"events": 1, "minutes": 60}])
def test_time_coverage_requirements(kw):
    """Under a minute of coverage, more than 10% untimed events or a single timed event: no per-minute rates."""
    assert not imp(**kw).time_valid


def test_zero_timed_occurrences_on_time_basis_are_not_tested():
    b, t = imp(1, 1000, 60, untimed=50), imp(2, 1000, 60, start=120 * MIN, untimed=50)
    it = only(cmp.compare(b, t, [grp(count=5, untimed=5)], [grp(count=5, untimed=5)], basis="time"))
    assert it.p_value is None and it.rate_ratio is None and it.change == "insufficient_data"


# --------------------------------------------------------------- compatibility
def test_compatibility_warnings():
    b = imp(1, 50, 60, services={"api": 50}, fmt="python", truncated=True)
    t = imp(2, 1000, 60, start=30 * MIN, services={"worker": 1000}, fmt="java")
    codes = {w.code for w in cmp.compare(b, t, [grp("a")], [grp("b")]).warnings}
    assert {"format_mismatch", "no_shared_services", "captures_overlap", "baseline_truncated",
            "baseline_low_volume", "no_shared_fingerprints"} <= codes
    swapped = {w.code for w in cmp.compare(t, b, [], []).warnings}
    assert "target_before_baseline" in swapped


# ------------------------------------------------------------ fixtures via API
def _pair(c):
    b, t = upload(c, "deploy_before.log").json(), upload(c, "deploy_after.log").json()
    return b["id"], t["id"]


def _all(c, b, t, **params):
    r = c.get(f"/api/imports/{b}/compare/{t}", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_fixture_pair_matches_declared_ground_truth(client):
    b, t = _pair(client)
    data = _all(client, b, t)
    assert data["baseline"]["event_count"] == TRUTH["baseline"]["total_events"]
    assert data["target"]["event_count"] == TRUTH["target"]["total_events"]
    got = {((i["baseline"] or {}).get("count", 0), (i["target"] or {}).get("count", 0)): i for i in data["items"]}
    assert len(got) == len(TRUTH["groups"]) == data["total"]
    for g in TRUTH["groups"]:
        item = got[(g["baseline_count"], g["target_count"])]
        assert (item["category"], item["change"]) == (g["category"], g["change"]), g["id"]
    assert data["totals"] == {"new": 2, "disappeared": 2, "recurring": 5, "increased": 2, "decreased": 1,
                              "no_clear_change": 1, "insufficient_data": 1, "resolved_observed": 0}
    assert data["warnings"] == [] and data["time_comparable"] is True


def test_shifted_line_number_splits_a_fingerprint(client):
    """Documented limitation: the same failure at a moved line is reported as disappeared + new, never merged."""
    b, t = _pair(client)
    tax = [i for i in _all(client, b, t)["items"] if i["title"].startswith("LookupError")]
    assert sorted(i["category"] for i in tax) == ["disappeared", "new"]
    assert {i["frame"] for i in tax} == {"tax.py:rate_for:88", "tax.py:rate_for:91"}


def test_messages_differing_only_in_numbers_share_a_fingerprint(client):
    """Normalization merges by design: variable numbers are masked, so these recur rather than appear new."""
    a = b"2026-03-05 10:00:00,000 - app - ERROR - Order 1001 failed for user 17\n" * 1
    z = b"2026-03-05 12:00:00,000 - app - ERROR - Order 9 failed for user 99999\n"
    ia = client.post("/api/imports", params={"filename": "a.log"}, content=a).json()["id"]
    iz = client.post("/api/imports", params={"filename": "z.log"}, content=z).json()["id"]
    items = _all(client, ia, iz)["items"]
    assert [i["category"] for i in items] == ["recurring"] and items[0]["change"] == "insufficient_data"


def test_priority_sort_lists_worsening_first(client):
    b, t = _pair(client)
    items = _all(client, b, t)["items"]
    first = [(i["category"], i["change"]) for i in items[:4]]
    assert all(c == "new" or ch == "increased" for c, ch in first)
    assert items[-1]["evidence"] != "significant"


def test_category_filter_and_pagination_keep_totals(client):
    b, t = _pair(client)
    full = _all(client, b, t)
    new = client.get(f"/api/imports/{b}/compare/{t}", params={"category": "new"}).json()
    assert new["total"] == 2 and {i["category"] for i in new["items"]} == {"new"} and new["totals"] == full["totals"]
    inc = client.get(f"/api/imports/{b}/compare/{t}", params={"category": "increased", "limit": 1, "offset": 1}).json()
    assert inc["total"] == 2 and len(inc["items"]) == 1 and inc["items"][0]["change"] == "increased"
    for i in full["items"]:
        assert i["investigate_import_id"] == (t if i["target"] else b)


def test_resolved_but_observed_flag(client):
    b, t = _pair(client)
    items = _all(client, b, t)["items"]
    inventory = next(i for i in items if i["title"].startswith("ConnectionError"))
    legacy = next(i for i in items if i["title"].startswith("Deprecated endpoint"))
    for fp in (inventory["fingerprint"], legacy["fingerprint"]):
        assert client.put(f"/api/incidents/{fp}/status", json={"status": "resolved"}).status_code == 200
    data = _all(client, b, t)
    flagged = [i for i in data["items"] if i["resolved_observed"]]
    assert [i["fingerprint"] for i in flagged] == [inventory["fingerprint"]]  # absent-from-target group is not flagged
    assert flagged[0]["status"] == "resolved" and flagged[0]["status_updated_at"] is not None
    assert data["totals"]["resolved_observed"] == 1
    only_flag = _all(client, b, t, category="resolved_observed")
    assert [i["fingerprint"] for i in only_flag["items"]] == [inventory["fingerprint"]]


def test_time_basis_via_api(client):
    b, t = _pair(client)
    pool = next(i for i in _all(client, b, t, basis="time")["items"] if i["title"].startswith("queue.Empty"))
    assert pool["change"] == "increased" and pool["bases_disagree"] is True


# ------------------------------------------------------------- validation/auth
def test_invalid_comparisons(client):
    b, t = _pair(client)
    assert client.get(f"/api/imports/{b}/compare/{b}").status_code == 422
    assert client.get(f"/api/imports/{b}/compare/999").status_code == 404
    assert client.get(f"/api/imports/0/compare/{t}").status_code == 422
    assert client.get(f"/api/imports/{b}/compare/{t}", params={"basis": "hours"}).status_code == 422
    assert client.get(f"/api/imports/{b}/compare/{t}", params={"category": "fixed"}).status_code == 422
    assert client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "xlsx"}).status_code == 422
    untimed = client.post("/api/imports", params={"filename": "u.jsonl"},
                          content=b'{"level":"error","message":"no time here"}\n').json()["id"]
    r = client.get(f"/api/imports/{b}/compare/{untimed}", params={"basis": "time"})
    assert r.status_code == 422 and "timestamp coverage" in r.json()["detail"]
    assert client.get(f"/api/imports/{b}/compare/{untimed}").status_code == 200


def test_comparison_requires_the_shared_key():
    c = make_client(api_key=KEY)
    h = {"X-API-Key": KEY}
    ids = [c.post("/api/imports", params={"filename": f}, content=(FIX / f).read_bytes(), headers=h).json()["id"]
           for f in ("deploy_before.log", "deploy_after.log")]
    for path in (f"/api/imports/{ids[0]}/compare/{ids[1]}", f"/api/imports/{ids[0]}/compare/{ids[1]}/export"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers={"X-API-Key": "w" * 24}).status_code == 401
        assert c.get(path, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


# --------------------------------------------------------------------- export
def test_export_formats_and_injection_safety(client):
    evil = b"2026-03-05 10:00:00,000 - app - ERROR - =HYPERLINK(\"http://x\") | `pipe` <b>\n" * 12
    later = b"2026-03-05 12:00:00,000 - app - ERROR - =HYPERLINK(\"http://x\") | `pipe` <b>\n" * 3
    b = client.post("/api/imports", params={"filename": "a=1.log"}, content=evil).json()["id"]
    t = client.post("/api/imports", params={"filename": "b.log"}, content=later).json()["id"]
    r = client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="stacktriage-compare-{b}-vs-{t}.csv"'
    rows = list(csv.reader(io.StringIO(r.text)))
    title = rows[0].index("title")
    assert rows[1][title].startswith("'=") and rows[1][rows[0].index("category")] == "recurring"
    md = client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "md"}).text
    assert "\\|" in md and "\\`pipe\\`" in md and "<b>" not in md and "not proven fixed" in md
    js = client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "json"}).json()
    assert js["items"][0]["title"].startswith("=HYPERLINK") and js["basis"] == "events" and "parameters" in js


# ------------------------------------------------------------------ read-only
def test_comparison_and_export_never_write(client):
    b, t = _pair(client)
    fp = _all(client, b, t)["items"][0]["fingerprint"]
    client.put(f"/api/incidents/{fp}/status", json={"status": "investigating"})
    client.post(f"/api/incidents/{fp}/notes", json={"body": "keep me"})
    writes: list[str] = []

    def spy(conn, cursor, statement, *a):  # noqa: ANN001
        if statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP"}:
            writes.append(statement)

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", spy)
    try:
        for params in ({}, {"basis": "time"}, {"category": "resolved_observed"}):
            assert client.get(f"/api/imports/{b}/compare/{t}", params=params).status_code == 200
        for fmt in ("csv", "json", "md"):
            assert client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": fmt}).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert writes == []
    first = _all(client, b, t)["items"][0]
    d = client.get(f"/api/imports/{first['investigate_import_id']}/incidents/{fp}").json()
    assert d["status"] == "investigating" and [n["body"] for n in d["notes"]] == ["keep me"]


# ------------------------------------------------------- review regressions
def test_increase_from_zero_timed_occurrences_on_time_basis():
    """Baseline occurrences all lack timestamps: 0 -> 20 per minute is an increase, not 'no clear change'."""
    b, t = imp(1, 1000, 60, untimed=50), imp(2, 1000, 60, start=120 * MIN)
    it = only(cmp.compare(b, t, [grp(count=5, untimed=5)], [grp(count=20)], basis="time"))
    assert it.rate_ratio is None and it.evidence == "significant" and it.change == "increased"


def test_bases_disagree_compares_full_verdicts():
    """Same fold direction on both bases but only one verdict passes the test: still a disagreement."""
    b, t = imp(1, 1000, 60), imp(2, 1000, 1, start=120 * MIN)
    ev = only(cmp.compare(b, t, [grp(count=10)], [grp(count=15)], basis="events"))
    tm = only(cmp.compare(b, t, [grp(count=10)], [grp(count=15)], basis="time"))
    assert (ev.change, tm.change) == ("no_clear_change", "increased")
    assert ev.bases_disagree and tm.bases_disagree


def test_truncated_service_summaries_are_not_compared():
    b = imp(1, services={f"svc{i}": 10 for i in range(50)})
    t = imp(2, start=120 * MIN, services={f"svc{i}": 10 for i in range(1, 51)})
    codes = {w.code for w in cmp.compare(b, t, [], []).warnings}
    assert "service_list_truncated" in codes and not codes & {"service_mismatch", "no_shared_services"}


def test_overflow_bucket_is_excluded_and_reported(client):
    from stacktriage.db.models import Group

    b, t = _pair(client)
    with client.app.state.factory() as s:  # simulate both imports having hit the group limit
        for import_id, n in ((b, 20), (t, 100)):
            s.add(Group(import_id=import_id, fingerprint="overflow", title="(additional error types)", service="*",
                        exc_type=None, kind="overflow", frame=None, max_level=40, count=n, untimed_count=0,
                        first_seen_ms=None, last_seen_ms=None, sample_event_id="x", sample_message="",
                        sample_stack_trace=None, level_counts={}, activity={}, windows=[]))
        s.commit()
    data = _all(client, b, t)
    assert "overflow" not in {i["fingerprint"] for i in data["items"]} and data["total"] == 9
    assert {"baseline_groups_capped", "target_groups_capped"} <= {w["code"] for w in data["warnings"]}


def test_untimed_count_comes_from_stored_events_not_parser_diagnostics():
    """With an event cap, the parser counts one untimed record that is then discarded: it must not count."""
    c = make_client(max_events=10)
    timed = [{"timestamp": f"2026-03-05T10:{m:02d}:00Z", "level": "error", "message": "boom"} for m in range(9)]
    body = "\n".join(json.dumps(r) for r in [*timed, {"level": "error", "message": "boom"},
                                              {"level": "error", "message": "boom"}]) + "\n"
    b = c.post("/api/imports", params={"filename": "capped.jsonl"}, content=body.encode()).json()["id"]
    later = "\n".join(json.dumps({**r, "timestamp": r["timestamp"].replace("T10", "T12")}) for r in timed) + "\n"
    t = c.post("/api/imports", params={"filename": "later.jsonl"}, content=later.encode()).json()["id"]
    r = c.get(f"/api/imports/{b}/compare/{t}", params={"basis": "time"})
    assert r.status_code == 200, r.text
    assert r.json()["baseline"]["untimed_events"] == 1 and r.json()["baseline"]["timed_events"] == 9
