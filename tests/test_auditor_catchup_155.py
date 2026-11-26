"""#155 AUD-023: the Persistent auditor timer must request a real catch-up date."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_auditor_service_uses_catchup_not_latest():
    unit = (ROOT / "trade-log-auditor.service").read_text(encoding="utf-8")
    assert "--date catchup" in unit
    assert "--date latest" not in unit
    assert "missed session" in unit
    assert "duplicate" in unit
    assert "partial" in unit


def test_auditor_timer_documents_persistent_catchup():
    timer = (ROOT / "trade-log-auditor.timer").read_text(encoding="utf-8")
    assert "Persistent=true" in timer
    assert "--date catchup" in timer
    assert "duplicate/partial" in timer
