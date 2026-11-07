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

# Game switch
for key in ['active_game', 'phase', 'last_status', 'last_error_code', 'revision', 'updated_at', 'present', 'readable']:
    pat = r'"game_switch":\{.*?\n *"' + key + r':([^,}]+)'
    m = re.search(pat, json_str, re.S)
    print('game_switch.' + key + ':', m.group(1) if m else 'NOT FOUND')

print()

# Expected workers
m = re.search(r'"workers":\{.*?\n *"expected":(\d+)', json_str, re.S)
print('workers.expected:', m.group(1) if m else 'NOT FOUND')

# semantic_decision
for key in ['account', 'route', 'backend', 'requested_model']:
    pat = r'"semantic_decision":\{.*?\n *"' + key + r':([^,}]+)'
    m = re.search(pat, json_str, re.S)
    print('semantic_decision.' + key + ':', m.group(1) if m else 'NOT FOUND')

print()

# Stream
m = re.search(r'"effective":\{.*?\n *"size":"([^"]+)"', json_str, re.S)
print('stream.effective.size:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"effective":\{.*?\n *"fps":([\d.]+)', json_str, re.S)
print('stream.effective.fps:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"profile_default":\{.*?\n *"fps":([\d.]+)', json_str, re.S)
print('stream.profile_default.fps:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"drift":(\w+)', json_str)
print('stream.drift:', m.group(1) if m else 'NOT FOUND')

print()

# audio_gaps
m = re.search(r'"gaps":\{.*?\n *"count":(\d+)', json_str, re.S)
print('audio_gaps.gaps.count:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"gaps":\{.*?\n *"total_ms":(\d+)', json_str, re.S)
print('audio_gaps.gaps.total_ms:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"gaps":\{.*?\n *"max_ms":(\d+)', json_str, re.S)
print('audio_gaps.gaps.max_ms:', m.group(1) if m else 'NOT FOUND')

# Tracked drift
m = re.search(r'"tracked_drift":\{.*?\n *"scan_complete":(\d+)', json_str, re.S)
print('tracked_drift.scan_complete:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"tracked_drift":\{.*?\n *"drift_detected":(\d+)', json_str, re.S)
print('tracked_drift.drift_detected:', m.group(1) if m else 'NOT FOUND')

# Stream title sync
m = re.search(r'"stream_title_sync":\{.*?\n *"record_status":"(\w+)"', json_str, re.S)
print('stream_title_sync.record_status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"stream_title_sync":\{.*?\n *"age_sec":(\d+)', json_str, re.S)
print('stream_title_sync.age_sec:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"stream_title_sync":\{.*?\n *"skip_reason":"(\w+)"', json_str, re.S)
print('stream_title_sync.skip_reason:', m.group(1) if m else 'NOT FOUND')
