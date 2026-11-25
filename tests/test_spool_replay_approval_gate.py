"""The scheduled spool job must plan only; writes need explicit human approval."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_spool_replay_service_is_read_only():
    unit = (ROOT / "spool-replay.service").read_text(encoding="utf-8")
    exec_starts = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    assert exec_starts == [
        "ExecStart=/usr/bin/python3 -m spool_replay --dry-run --json"
    ]
    assert "require explicit human approval" in unit
    assert "--approve" not in unit.split("[Service]")[0]


def test_spool_replay_timer_schedules_planning_only():
    timer = (ROOT / "spool-replay.timer").read_text(encoding="utf-8")
    assert "read-only" in timer
    assert "human approval" in timer
    assert "Unit=spool-replay.service" in timer
