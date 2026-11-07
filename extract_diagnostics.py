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

def grep(name, pattern):
    m = re.search(pattern, json_str)
    if m:
        print(f'{name}: {m.group(0).strip()}')
    else:
        print(f'{name}: NOT FOUND')

print('=== FULL KEY FINDINGS ===')
print()
grep('status', r'"status":"(\w+)"')
grep('sha', r'"sha":"([0-9a-f]{40})"')
print()

# host_metrics
m = re.search(r'"host_metrics":\{.*?\n *"status":"(\w+)"', json_str, re.S)
print('host_metrics.status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"boots":(\d+)', json_str)
print('boots:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"current_boot":"([0-9a-f]{10})"', json_str)
print('current_boot:', m.group(1) if m else 'NOT FOUND')

# CPU samples
cpus = re.findall(r'"cpu_busy_pct":([\d.]+)', json_str)
loads = re.findall(r'"load1":([\d.]+)', json_str)
print('cpu_busy_pct: min=%.1f%% max=%.1f%%' % (min(float(x) for x in cpus), max(float(x) for x in cpus)))
print('load1: min=%.2f max=%.2f' % (min(float(x) for x in loads), max(float(x) for x in loads)))
print()

# cpu_profile
m = re.search(r'"cpu_profile":\{.*?\n *"status":"(\w+)"', json_str, re.S)
print('cpu_profile.status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"age_sec":(\d+)', json_str)
print('cpu_profile.age_sec:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"cpu_busy_pct_p50":([\d.]+)', json_str)
print('cpu_busy_pct_p50:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"cpu_busy_pct_p95":([\d.]+)', json_str)
print('cpu_busy_pct_p95:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"cpu_top":"(.*?)"', json_str)
print('cpu_top:', m.group(1) if m else 'NOT FOUND')
print()

# game_switch
m = re.search(r'"game_switch":\{.*?\n *"active_game":"([^"]+)"', json_str, re.S)
print('game_switch.active_game:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"game_switch":\{.*?\n *"phase":"(\w+)"', json_str, re.S)
print('game_switch.phase:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"game_switch":\{.*?\n *"last_status":"(\w+)"', json_str, re.S)
print('game_switch.last_status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"game_switch":\{.*?\n *"last_error_code":(\w+)', json_str, re.S)
print('game_switch.last_error_code:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"game_switch":\{.*?\n *"revision":(\d+)', json_str, re.S)
print('game_switch.revision:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"game_switch":\{.*?\n *"updated_at":"([^"]+)"', json_str, re.S)
print('game_switch.updated_at:', m.group(1) if m else 'NOT FOUND')
print()

# corner_rotation
m = re.search(r'"auto_restore_status":"(\w+)"', json_str)
print('corner_rotation.auto_restore_status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"auto_restore_corner":"(\w+)"', json_str)
print('corner_rotation.auto_restore_corner:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"auto_restore_attempt_limit":(\d+)', json_str)
print('corner_rotation.auto_restore_attempt_limit:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"auto_restore_attempts":(\d+)', json_str)
print('corner_rotation.auto_restore_attempts:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"manual_pending_owner":"(\w+)"', json_str)
print('corner_rotation.manual_pending_owner:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"manual_pending_owner_status":"(\w+)"', json_str)
print('corner_rotation.manual_pending_owner_status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"pending":(\w+)', json_str)
print('corner_rotation.pending:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"manual_pending":(\w+)', json_str)
print('corner_rotation.manual_pending:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"slot":(\d+)', json_str)
print('corner_rotation.slot:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"status":"(\w+)"', json_str)
print('corner_rotation.status:', m.group(1) if m else 'NOT FOUND')
print()

# workers
m = re.search(r'"workers":\{.*?\n *"expected":(\d+)', json_str, re.S)
print('workers.expected:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"running":(\d+)', json_str)
print('workers.running:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"stopped":(\[.*?\])', json_str, re.S)
print('workers.stopped:', m.group(1)[:200] if m else 'NOT FOUND')
print()

# encoder
m = re.search(r'"encoder":\{.*?\n *"source":"(\w+)"', json_str, re.S)
print('encoder.source:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"encoder":\{.*?\n *"state":"(\w+)"', json_str, re.S)
print('encoder.state:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"fps":([\d.]+)', json_str)
print('encoder.fps:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"frames_sent":(\d+)', json_str)
print('encoder.frames_sent:', m.group(1) if m else 'NOT FOUND')
print()

# stream
m = re.search(r'"status":"(\w+)"', json_str)
print('stream.status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"effective":\{.*?\n *"size":"([^"]+)"', json_str, re.S)
print('stream.effective.size:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"effective":\{.*?\n *"fps":([\d.]+)', json_str, re.S)
print('stream.effective.fps:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"profile_default":\{.*?\n *"fps":([\d.]+)', json_str, re.S)
print('stream.profile_default.fps:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"drift":(\w+)', json_str)
print('stream.drift:', m.group(1) if m else 'NOT FOUND')
print()

# runtime_presentation
m = re.search(r'"runtime_presentation":\{.*?\n *"present":(\w+)', json_str, re.S)
print('runtime_presentation.present:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"runtime_presentation":\{.*?\n *"active_runtime":(\w+)', json_str, re.S)
print('runtime_presentation.active_runtime:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"runtime_presentation":\{.*?\n *"reason":"(\w+)"', json_str, re.S)
print('runtime_presentation.reason:', m.group(1) if m else 'NOT FOUND')
print()

# audio_gaps
m = re.search(r'"age_sec":(\d+)', json_str)
print('audio_gaps.age_sec:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"count":(\d+)', json_str)
print('audio_gaps.gaps.count:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"total_ms":(\d+)', json_str)
print('audio_gaps.gaps.total_ms:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"max_ms":(\d+)', json_str)
print('audio_gaps.gaps.max_ms:', m.group(1) if m else 'NOT FOUND')
print()

# stream_title_sync
m = re.search(r'"record_status":"(\w+)"', json_str)
print('stream_title_sync.record_status:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"age_sec":(\d+)', json_str)
print('stream_title_sync.age_sec:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"skip_reason":"(\w+)"', json_str)
print('stream_title_sync.skip_reason:', m.group(1) if m else 'NOT FOUND')
print()

# twitch_oauth
m = re.search(r'"action":"(\w+)"', json_str)
print('twitch_oauth.action:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"expires_at":"([^"]+)"', json_str)
print('twitch_oauth.expires_at:', m.group(1) if m else 'NOT FOUND')
print()

# webui
m = re.search(r'"main_pid":(\d+)', json_str)
print('webui.main_pid:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"n_restarts":(\d+)', json_str)
print('webui.n_restarts:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"served_port":(\d+)', json_str)
print('webui.served_port:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"served_reachable":(\w+)', json_str)
print('webui.served_reachable:', m.group(1) if m else 'NOT FOUND')
print()

# semantic_decision
m = re.search(r'"route":"(\w+)"', json_str)
print('semantic_decision.route:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"backend":"(\w+)"', json_str)
print('semantic_decision.backend:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"requested_model":"(\w+)"', json_str)
print('semantic_decision.requested_model:', m.group(1) if m else 'NOT FOUND')
print()

# improvement
m = re.search(r'"phase":"(\w+)"', json_str)
print('improvement.phase:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"backing_off":(\w+)', json_str)
print('improvement.backing_off:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"blocked_by":\[(.*?)\]', json_str, re.S)
print('improvement.blocked_by:', m.group(1)[:200] if m else 'NOT FOUND')
m = re.search(r'"retry_pending":(\w+)', json_str)
print('improvement.retry_pending:', m.group(1) if m else 'NOT FOUND')
print()

# queues
m = re.search(r'"stale_locks":(\d+)', json_str)
print('queues.stale_locks:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"queue_giveups_15m":(\d+)', json_str)
print('queues.queue_giveups_15m:', m.group(1) if m else 'NOT FOUND')
print()

# tracked_drift
m = re.search(r'"scan_complete":(\d+)', json_str)
print('tracked_drift.scan_complete:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"drift_detected":(\d+)', json_str)
print('tracked_drift.drift_detected:', m.group(1) if m else 'NOT FOUND')
print()

# manual_pending_evidence
m = re.search(r'"owner_read_unknown":(\d+)', json_str)
print('corner_rotation.manual_pending_evidence.owner_read_unknown:', m.group(1) if m else 'NOT FOUND')
m = re.search(r'"terminal_owner_absent":(\w+)', json_str)
print('corner_rotation.terminal_owner_absent:', m.group(1) if m else 'NOT FOUND')
