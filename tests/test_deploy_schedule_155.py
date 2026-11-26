"""#155 AUD-021: the services deploy cron and VM log must state UTC and ET."""

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-services.yml"
ET = ZoneInfo("America/New_York")


def test_cron_and_annotations_are_explicit():
    text = WF.read_text(encoding="utf-8")
    cron = re.search(r"^\s*-\s*cron:\s*'([^']+)'", text, re.MULTILINE)
    assert cron and cron.group(1) == "55 3 * * 2-6"
    for expected in (
        "03:55 UTC", "23:55 EDT", "22:55 EST", "DST", "one day ahead",
        "America/New_York", "Assert deploy schedule",
    ):
        assert expected in text, expected


def test_vm_log_prints_both_clocks():
    text = WF.read_text(encoding="utf-8")
    assert "date -u '+Now on VM:" in text
    assert "TZ=America/New_York date '+Now on VM:" in text


def test_utc_cron_converts_to_both_et_times():
    summer = datetime(2026, 7, 7, 3, 55, tzinfo=timezone.utc).astimezone(ET)
    winter = datetime(2026, 1, 6, 3, 55, tzinfo=timezone.utc).astimezone(ET)
    assert summer.strftime("%Y-%m-%d %H:%M %Z") == "2026-07-06 23:55 EDT"
    assert winter.strftime("%Y-%m-%d %H:%M %Z") == "2026-01-05 22:55 EST"
