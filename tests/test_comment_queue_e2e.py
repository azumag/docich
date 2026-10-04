"""Offline cross-repository queue contract. No credentials, HTTP or real AI.

Real Soren fetch/generate_comment_response/route envelope/ack/dedup functions
run against copied allowlisted files. Only generation and unrelated context,
voice/advice helpers are fixtures. JEV transport is injected into real docich.
CI pins the companion tree; local runs opt in with DOCICH_SOREN_TEST_ROOT.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOREN = os.environ.get("DOCICH_SOREN_TEST_ROOT")

ROUTER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from docich.comment_classifier.reply_route import classify_file
base = Path.cwd()
calls = []
def transport(request, config, env):
    calls.append(request)
    if (base / 'phase').read_text().strip() in {'fail', 'deliveryfail'} and len(calls) == 2:
        return {'status': 'timeout'}
    answers = {}
    for key, question in request['questions'].items():
        choice = 'chitchat' if key.startswith('c') else 'api_only'
        labels = list(question['criteria'])
        answers[key] = {'type': 'choice', 'choice': choice, 'confidence': .95,
            'probabilities': {label: .95 if label == choice else .05/(len(labels)-1) for label in labels}}
    return {'status': 'ok', 'data': {'model': request['model'],
        'usage': {'input_tokens': 100, 'output_tokens': 0}, 'answers': answers}}
# Isolated state per routing attempt deliberately removes provider cooldown
# from this queue test. Cooldown behavior is tested separately; no clock sleep.
env = {'COMMENT_CLASSIFIER_BACKEND': 'jev', 'DOCICH_REPLY_ROUTING_ENABLED': '1',
       'COMMENT_CLASSIFIER_JEV_LOG_ENABLED': '0', 'TYPESAFE_API_KEY': 'SYNTHETIC_KEY',
       'DOCICH_ALLOW_REAL_AI': '1',
       'COMMENT_CLASSIFIER_JEV_STATE_DIR': str(base / ('gate-' + os.environ['CYCLE']))}
result = classify_file(sys.argv[1], env=env, transport=transport,
                       researcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError('research')))
with (base / 'calls.jsonl').open('a') as f:
    f.write(json.dumps({'cycle': os.environ['CYCLE'], 'requests': calls, 'result': result}) + '\n')
print(json.dumps(result))
'''

HARNESS = r'''
source broadcast/comment.sh
log(){ printf '%s\n' "$*" >&2; }
# No real context, voice, diagnosis, advice, provider or network dependencies.
for helper in _radio_past_topics_block _build_comment_game_context \
 _build_comment_celebration_history_context _build_recent_spoken_comment_context \
 _build_comment_viewer_memory_context _build_comment_ops_context \
 _build_comment_followup_hints _extract_structured_advice_from_comments \
 _read_advice_context_tail _queue_stream_bug_reports_from_classification \
 _ingest_stream_bug_reports_redacted _append_structured_strategy_advice_at_intake \
 _stage_comment_viewer_memory _remember_comment_reply_text \
 _broadcast_mark_expected_mode _extract_named_block; do
 eval "$helper(){ :; }"
done
_comment_needs_thumbnail_context(){ return 1; }
_broadcast_host_mode(){ printf main; }
_peak_priority_agent_list(){ printf '%s' "$1"; }
_sanitize_comment_prompt_context(){ cat; }
_format_comment_batch_context(){ cat; }
_comment_guard_japanese_text(){ [ "$(cat phase)" != deliveryfail ] || return 1; printf '%s' "$1"; }
_clean_comment_talk(){ printf '%s' "$1"; }
_sanitize_onair_text(){ cat; }
_normalize_radio_tone(){ cat; }
_comment_replace_country_references(){ cat; }
_is_valid_comment_talk(){ [ -n "$1" ]; }
_comment_store_generation_meta(){ return 0; }
_build_category_prompt(){ return 1; }
_my_pid(){ printf '%s' "$$"; }
disown(){ :; }
# Deterministic generation fixture, one response per successfully routed batch.
ai_generate_list(){
 printf '%s\n' "$CYCLE" >> generated.log
 printf 'fixture reply cycle %s\n' "$CYCLE"
 printf 'fixture' > "$6"
}
COMMENT_QUEUE_DIR=tmp/.comment_queue
COMMENT_GEN_STATE_FILE=tmp/state/generation
COMMENT_BATCH_HISTORY_FILE=tmp/state/batch_history
COMMENT_BATCH_INFLIGHT_FILE=tmp/state/inflight
COMMENT_BATCH_DEDUP_TTL=900
COMMENT_PROCESSED_LINES_FILE=tmp/state/processed
COMMENT_PROCESSED_LINES_TTL=900
COMMENT_PROCESSED_LINES_MAX=4000
COMMENT_ADVICE_FILE=tmp/advice
mkdir -p "$COMMENT_QUEUE_DIR" tmp/state
for phase in "$@"; do
 CYCLE=$(( ${CYCLE:-0} + 1 )); export CYCLE
 printf '%s' "$phase" >phase
 generate_comment_response "$PLATFORM"
 wait
 python3 - <<'STATE'
import json, os
from pathlib import Path
p=Path('tmp/.'+os.environ['PLATFORM']+'_chat/pending.log')
with open('states.jsonl','a') as f:
 f.write(json.dumps({'pending':p.read_text().splitlines() if p.exists() else [],
 'queue':len(list(Path('tmp/.comment_queue').glob('comment_*.txt'))),
 'generated':Path('generated.log').read_text().splitlines() if Path('generated.log').exists() else []})+'\n')
STATE
done
'''

@pytest.mark.parametrize("platform", ["twitch", "youtube", "kick"])
@pytest.mark.parametrize("first_count,fail", [(9, False), (10, False), (10, True), (10, "delivery")])
def test_repeated_real_fetch_route_queue_ack(tmp_path, platform, first_count, fail):
    if not SOREN:
        if os.environ.get("DOCICH_REQUIRE_QUEUE_E2E") == "1":
            pytest.fail("DOCICH_SOREN_TEST_ROOT must name the pinned companion checkout")
        pytest.skip("set DOCICH_SOREN_TEST_ROOT for offline companion E2E")
    companion = Path(SOREN)
    # Copy only these public source files, never .env/.git/host configuration.
    files = ["broadcast/comment.sh", "lib/comment_reply_route.py", "lib/comment_viewer_memory.py",
             "lib/curl_secure.sh", "twitch_chat.sh", "youtube_chat.sh", "kick_chat.sh",
             "prompts/comment_template.md", "prompts/comment_persona_main.md"]
    for name in files:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(companion / name, dest)
    (tmp_path / "lib/raid_research.py").write_text("# Unrelated public raid context fixture; no network.\n")
    # Generic prompt fallback; context/generation is out of this test's scope.
    router = tmp_path / "fixture-router"
    router.write_text(ROUTER); router.chmod(0o755)
    chat = tmp_path / f"tmp/.{platform}_chat"
    chat.mkdir(parents=True)
    def rows(start, count):
        return ''.join(f'id=msg-{n}\tuser-id=uid-{n}\tlogin=viewer{n}\tdisplay=viewer{n}\tflags=\tviewer{n}: こんにちは {n}\n'
                       for n in range(start, start + count))
    # The first fetch is limited to 10. For the 9-row case a second fixture
    # append is performed between rounds below; other cases have 20 pending.
    initial_count = 20 if first_count == 10 else 9
    (chat / "raw.log").write_text(rows(0, initial_count))
    (chat / "last_offset").write_text("0\n")
    deny_bin = tmp_path / "deny-bin"
    deny_bin.mkdir()
    for command in ("curl", "wget", "codex", "opencode", "gh", "docker", "node"):
        executable = deny_bin / command
        executable.write_text('#!/bin/bash\nprintf "%s\n" "$0" >> "$HOME/../forbidden-calls"\nexit 99\n')
        executable.chmod(0o755)
    (tmp_path / "empty-home").mkdir()
    # Clean environment: no inherited token/proxy/provider variables.
    env = {"PATH": str(deny_bin) + os.pathsep + os.environ["PATH"], "HOME": str(tmp_path / "empty-home"),
           "PYTHONPATH": str(ROOT / "src"), "PLATFORM": platform,
           "ELOOP_LIB_DIR": str(tmp_path), "CHAT_INGEST_OVERLAY_NOTIFY": "0",
           "DOCICH_REPLY_ROUTING_ENABLED": "1", "COMMENT_AGENTS": "local:fixture",
           "DOCICH_COMMENT_REPLY_ROUTER": str(router), "COMMENT_GENERATE_MAX_RETRIES": "1"}
    def run(phases, cycle="0"):
        proc = subprocess.run(["bash", "-c", HARNESS, "fixture", *phases], cwd=tmp_path,
                              env={**env, "CYCLE": cycle}, capture_output=True, text=True, timeout=45)
        assert proc.returncode == 0, proc.stderr
        assert 'command not found' not in proc.stderr, proc.stderr
    if first_count == 9:
        run(["ok"])
        with (chat / "raw.log").open('a') as f: f.write(rows(9, 10))
        run(["ok", "ok"], "1")
    else:
        run(["deliveryfail", "ok", "ok", "ok"] if fail == "delivery" else ["fail", "ok", "ok", "ok"] if fail else ["ok", "ok", "ok"])
    states = [json.loads(l) for l in (tmp_path / "states.jsonl").read_text().splitlines()]
    calls = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()]
    if fail == 'delivery':
        # Failed delivery keeps pending; retry reuses terminal cache, no JEV call.
        assert [len(state['pending']) for state in states] == [20, 10, 0, 0]
        assert [state['queue'] for state in states] == [0, 1, 2, 2]
        assert [len(state['generated']) for state in states] == [0, 0, 1, 1]
        assert len(calls) == 2
        assert [call['cycle'] for call in calls] == ['1', '3']
        assert calls[0]['result']['routing']['status'] == 'hold'
        assert calls[1]['result']['routing']['status'] == 'ready'
        assert not list((tmp_path/'tmp/state/comment_route_cache').glob('*.json'))
        assert not (tmp_path/'forbidden-calls').exists()
        return
    if fail:
        # The failed classification now emits one bounded terminal explanation,
        # acknowledges exactly its ten rows, and never retries their JEV request.
        assert [len(state['pending']) for state in states] == [10, 0, 0, 0]
        assert [state['queue'] for state in states] == [1, 2, 2, 2]
        assert [len(state['generated']) for state in states] == [0, 1, 1, 1]
        assert len(calls) == 2
        assert calls[0]['result']['routing']['status'] == 'hold'
        assert calls[1]['result']['routing']['status'] == 'ready'
        assert [len(r['state']['comments']) for r in calls[0]['requests']] == [8, 2]
        assert [row.split('\t')[0] for row in states[0]['pending']] == [f'id=msg-{n}' for n in range(10, 20)]
        assert not (tmp_path / 'forbidden-calls').exists()
        return
    assert [len(state['pending']) for state in states] == [10 if first_count == 10 else 0, 0, 0]
    if first_count == 10:
        assert [row.split('\t')[0] for row in states[0]['pending']] == [f'id=msg-{n}' for n in range(10, 20)]
    assert not (tmp_path / 'forbidden-calls').exists()
    assert [state['queue'] for state in states] == [1, 2, 2]
    assert [len(state['generated']) for state in states] == [1, 2, 2]
    assert [[len(r['state']['comments']) for r in call['requests']] for call in calls] == [[8, first_count-8], [8, 2]]
    assert all(call['result']['routing']['status'] == 'ready' for call in calls)
    assert [len(call['result']['rows']) for call in calls] == [first_count, 10]
    assert [[row['comment'] for row in call['result']['rows']] for call in calls] == [
        [f'こんにちは {n}' for n in range(first_count)],
        [f'こんにちは {n}' for n in range(first_count, first_count + 10)]]
    for call in calls:
        for request in call['requests']:
            assert set(request['state']) == {'comments'}
            assert all(set(item) == {'index', 'text'} for item in request['state']['comments'])
