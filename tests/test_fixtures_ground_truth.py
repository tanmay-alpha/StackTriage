"""Parser + grouping output must match the incidents injected by scripts/make_fixtures.py."""
import pytest

from acceptance_report import check

NAMES = ["python_app", "node_express", "spring_boot", "pino_payments", "telemetry"]


@pytest.mark.parametrize("name", NAMES)
def test_fixture_matches_ground_truth(name):
    assert check(name) == []


def test_fixtures_are_committed_not_skipped():
    from pathlib import Path

    fix = Path(__file__).parent / "fixtures"
    for n in NAMES:
        assert (fix / "expected" / f"{n}.json").exists()
