from pathlib import Path
p = Path('tests/test_hanjuku_month_audit.py')
t = p.read_text()
extra = '''


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("en_route", True),
        ("launched_unconfirmed", True),
        ("arrived", False),
        ("overdue", False),
        ("cancelled", False),
        ("closed", False),
    ],
)
def test_stuck_march_only_counts_live_march_statuses(status, expected):
    context = {
        "flow": {},
        "world": {
            "garrison": {"S": ["G", "H"]},
            "sorties": {"old": {"status": status, "age_ticks": 3001}},
        },
        "plan": {"orders": {"A:1:M1": "pending"}},
    }

    signals = audit._signals(
        context, {}, confirmed=0, departures=0, battle_count=0,
    )

    assert ("stuck_march" in signals) is expected


@pytest.mark.parametrize(("age_ticks", "expected"), [
    (3000, False), (3001, True), (True, False), ("3001", False), (None, False),
])
def test_stuck_march_keeps_the_existing_age_threshold(age_ticks, expected):
    context = {
        "flow": {},
        "world": {
            "garrison": {"S": ["G", "H"]},
            "sorties": {"live": {"status": "en_route", "age_ticks": age_ticks}},
        },
        "plan": {"orders": {"A:1:M1": "pending"}},
    }

    signals = audit._signals(
        context, {}, confirmed=0, departures=0, battle_count=0,
    )

    assert ("stuck_march" in signals) is expected
'''
if 'test_stuck_march_only_counts_live_march_statuses' not in t:
    p.write_text(t.rstrip() + extra + chr(10))
    print('tests appended')

v = Path('src/docich/hanjuku/version.py')
vt = v.read_text()
import re
vt = re.sub(r"BOT_VERSION = '.*'", "BOT_VERSION = 'hanjuku-chart-v1510-live-march-status'", vt)
v.write_text(vt)
print('version v1510')

d = Path('ops/vm_actions/collect_diagnostics.py')
dt = d.read_text()
if 'v1510-live-march-status' not in dt:
    dt = dt.replace('            "hanjuku-chart-v1509-card-at-hp1-audit",',
                    '            "hanjuku-chart-v1509-card-at-hp1-audit",' + chr(10) +
                    '            "hanjuku-chart-v1510-live-march-status",', 1)
    d.write_text(dt)
    print('allowlist added')
