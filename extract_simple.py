import re

with open('/tmp/job_logs_final.txt', 'r') as f:
    log = f.read()

idx = log.find('{"status":"diagnosed"')
brace_count = 0
in_string = False
escape = False
end_idx = -1
for i in range(idx, len(log)):
    c = log[i]
    if in_string:
        if escape:
            escape = False
        elif c == '\\':
            escape = True
        elif c == '"':
            in_string = False
    else:
        if c == '"':
            in_string = True
        elif c == '{':
            brace_count += 1
        elif c == '}':
            brace_count -= 1
            if brace_count == 0:
                end_idx = i + 1
                break

json_str = log[idx:end_idx]

searches = [
    ('game_switch.active_game', r'"active_game":\s*"([\w]+)"'),
    ('game_switch.phase', r'"phase":"(\w+)"'),
    ('game_switch.last_status', r'"last_status":"(\w+)"'),
    ('game_switch.last_error_code', r'"last_error_code":(\w+)'),
    ('game_switch.revision', r'"revision":(\d+)'),
    ('game_switch.updated_at', r'"updated_at":"([^"]+)"'),
    ('workers.expected', r'"expected":(\d+)'),
    ('semantic_decision.backend', r'"backend":"(\w+)"'),
    ('semantic_decision.requested_model', r'"requested_model":"(\w+)"'),
    ('stream.effective.size', r'"size":"([^"]+)"'),
    ('stream.effective.fps', r'"fps":([\d.]+)'),
    ('audio_gaps.gaps.count', r'"count":(\d+)'),
    ('audio_gaps.gaps.total_ms', r'"total_ms":(\d+)'),
    ('audio_gaps.gaps.max_ms', r'"max_ms":(\d+)'),
    ('tracked_drift.scan_complete', r'"scan_complete":(\d+)'),
    ('tracked_drift.drift_detected', r'"drift_detected":(\d+)'),
    ('stream_title_sync.record_status', r'"record_status":"(\w+)"'),
    ('stream_title_sync.age_sec', r'"age_sec":(\d+)'),
    ('stream_title_sync.skip_reason', r'"skip_reason":"(\w+)"'),
]

for name, pat in searches:
    m = re.search(pat, json_str, re.S)
    print(name, ':', m.group(1) if m else 'NOT FOUND')
