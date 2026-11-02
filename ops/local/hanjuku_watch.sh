#!/usr/bin/env bash
# Read-only one-shot status for the Hanjuku Hero corner on the production VM.
# Prints: canonical switch, resume reservation, rotation, and (when a hanjuku
# runtime is active) chapter/phase/input freshness. Safe to run any time.
set -u
H='ubuntu@soren-prod-vnic.tail85a080.ts.net'
# The Python body is fed on stdin (quoted heredoc), never embedded in a quoted
# shell argument: its own single quotes used to end the argument early and
# fail with "name 'situation_held' is not defined".
ssh -o BatchMode=yes -o ConnectTimeout=10 "$H" 'python3 -' 2>&1 <<'EOF'
import json, time, os, glob, datetime
root = "/home/ubuntu/docich/run-soren-live"
now = time.time()

def j(p):
    try:
        return json.load(open(p))
    except Exception:
        return None

gs = j(root + "/game_switch.json") or {}
active = gs.get("active") or {}
print("SWITCH phase=%s op=%s active=%s gen=%s updated=%s" % (
    gs.get("phase"), gs.get("operation"), active.get("game"),
    active.get("generation"), gs.get("updated_at")))

req = j(root + "/hanjuku-resume/hanjuku_resume.json") or {}
print("RESUME status=%s gen=%s runtime=%s start_mode=%s" % (
    req.get("status"), req.get("generation"), req.get("runtime_id"),
    req.get("start_mode")))

cr = j(root + "/corner_rotation.json") or {}
pend = cr.get("pending") or cr.get("manual_pending") or {}
print("ROTATION status=%s pending=%s queued_manual=%s next_due=%s" % (
    cr.get("status"),
    (pend.get("corner"), pend.get("phase")),
    (cr.get("queued_manual") or {}).get("corner"),
    datetime.datetime.fromtimestamp(cr.get("next_due_at") or 0).strftime("%H:%M")))

# newest hanjuku runtime with a run record
runs = sorted(glob.glob(root + "/runtimes/g*/hanjuku_run.json"),
              key=os.path.getmtime)
if runs:
    p = runs[-1]
    hr = j(p) or {}
    rt = os.path.basename(os.path.dirname(p))
    prog = j(os.path.join(os.path.dirname(p), "hanjuku_progress.json")) or {}
    age = now - (hr.get("observed_at") or 0)
    in_age = (hr.get("observed_monotonic") or 0) - (hr.get("last_input_monotonic") or 0)
    print("HANJUKU rt=%s cleared=%s chapter=%s phase=%s obs_age=%.0fs input_age=%.0fs actions=%s" % (
        rt, prog.get("cleared"), hr.get("chapter") or "?", hr.get("phase"),
        age, in_age, hr.get("actions_sent")))
    # recent stall signals
    dec = os.path.join(os.path.dirname(p), "hanjuku_decisions.jsonl")
    try:
        tail = open(dec).readlines()[-60:]
        held = [json.loads(x) for x in tail if 'situation_held' in x]
        if held:
            print("LAST_HELD reason=%s step=%s t=%s" % (
                str(held[-1].get("reason"))[:80], held[-1].get("chart_step"),
                datetime.datetime.fromtimestamp(held[-1].get("at", 0)).strftime("%H:%M:%S")))
    except Exception as e:
        print("decisions read: %s" % e)
EOF
