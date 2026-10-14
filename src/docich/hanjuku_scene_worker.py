"""One bounded Hanjuku scene job through Soren's existing radio dispatcher.

This consumer is a separate process, hot-loaded by the command bot. It owns
no game input or shared service, and generates no fallback speech.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

from . import hanjuku_scene as scene
from .config import load_global, load_game
from .game_switch import atomic_write_json
from .hanjuku_run import append_log
from .retroarch_boundary import read_record
from .hanjuku_scene_process import enable_subreaper, OwnedSceneProcess, SceneProcessUnavailable

EVENTS = frozenset({'requested', 'skipped', 'generate_started', 'generate_succeeded',
                    'generate_failed', 'deliver_enqueued', 'deliver_failed'})
REASONS = frozenset({'none', 'disabled', 'no_facts', 'duplicate', 'cooldown', 'in_flight',
                     'stale', 'scene_changed', 'fence_lost', 'unavailable', 'invalid_output',
                     'generation_failed', 'timeout', 'queue_giveup', 'gate_giveup',
                     'rate_limit', 'backoff', 'delivery_failed', 'worker_error'})
ROLES = frozenset({'BATCH_COMMENTARY_AGENTS', 'RADIO_AGENTS', 'AI_COMMON_AGENTS'})
MAX_OUTPUT_BYTES = 8192
CARD_ROLE_MEANINGS = {
    'nonlethal_egg_risk': '削りで敵の卵発動条件に近づく危険があり、使用候補から外した判断',
    'damage_or_egg_risk_unclassified': '現在の損害または卵発動リスクを確定できず、使用を許可しなかった判断',
    'single_card_lethal': '現在の計算下限で将軍を倒せる条件と判定した。使用成功や勝利の実績ではない',
    'healing_not_a_kill': '回復目的の札として許可する判断。回復完了や撃破ではない',
    'no_autonomous_egg_trigger': '敵が自発的に卵を使う条件に該当しないという判定',
    'egg_drop_candidate_not_a_kill': '卵を落とす候補として許可する判断。卵落ちや撃破の結果ではない',
    'inspect_live_inventory_before_selection': '使用前に実際の手持ち札一覧を確認する必要があるという判断',
    'observed_control_chain_not_a_kill': '手持ち一覧に後続の攻撃札を確認し、準備用の札を候補にする判断。連携成功や撃破ではない',
    'control_has_no_observed_followup': '手持ち一覧に後続の攻撃札を確認できず、準備用の札を許可しなかった判断',
    'summon_already_observed': '既に召喚を観測した後の札評価。今回の札による召喚ではない',
}


class SceneError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if reason in REASONS else 'worker_error'
        super().__init__(self.reason)


def _fact_valid(value):
    if not isinstance(value, dict) or not re.fullmatch(r'f[1-3]', str(value.get('id', ''))):
        return False
    kind = value.get('kind')
    required = {
        'battle_start': {'ally', 'enemy', 'ally_hp', 'enemy_hp'},
        'battle_result': {'ally', 'enemy', 'outcome'},
        'castle_owned_observed': {'castle'}, 'castle_lost_observed': {'castle'},
        'soldier_refill_receipt': {'qty', 'soldiers_after'},
        'chikujou': {'castle', 'cost'},
        'egg_priority_replan': {'soldiers', 'policy_change'},
        'card_decision': {'decision_kind', 'card', 'allowed', 'role', 'target_kind'},
    }.get(kind)
    optional = ({'ally_hp', 'enemy_hp', 'cards_used'} if kind == 'battle_result' else
                {*scene.ESTIMATE_NUMBERS, 'lethal'} if kind == 'card_decision' else set())
    if required is None or not required <= value.keys() or set(value) - required - optional != {'id', 'kind'}:
        return False
    for key, item in value.items():
        if key in {'ally', 'enemy', 'castle', 'card'} and scene.name(item) is None:
            return False
        if key in {'ally_hp', 'enemy_hp', 'qty', 'soldiers_after', 'cost', 'soldiers', *scene.ESTIMATE_NUMBERS} and scene.number(item) is None:
            return False
        if key in {'allowed', 'lethal'} and type(item) is not bool:
            return False
        if key == 'cards_used' and (not isinstance(item, list) or len(item) > 4
                                    or any(scene.name(name) is None for name in item)):
            return False
    return (value.get('outcome', 'win') in {'win', 'loss'}
            and value.get('policy_change', 'egg_recovery_before_soldiers') == 'egg_recovery_before_soldiers'
            and (kind != 'egg_priority_replan' or value['soldiers'] >= 50)
            and (kind != 'soldier_refill_receipt' or value['qty'] > 0)
            and (kind != 'card_decision' or value['decision_kind'] in scene.CARD_DECISIONS
                 and value['role'] in scene.CARD_ROLES and value['target_kind'] in scene.TARGET_KINDS))


def checked_request(snapshot):
    request = snapshot.get('request')
    if (not isinstance(request, dict) or scene.number(request.get('seq'), 10**9) is None
            or request['seq'] < 1 or scene.stamp(request.get('at')) is None
            or scene.stamp(request.get('expires_at')) is None
            or not 0 < request['expires_at'] - request['at'] <= 20
            or request.get('scope') not in {'scene', 'history'}
            or any(not isinstance(request.get(k), str) or not re.fullmatch(r'[a-f0-9]{64}', request[k])
                   for k in ('event_key', 'scene_id', 'epoch_id'))
            or not isinstance(request.get('facts'), list) or not 1 <= len(request['facts']) <= scene.MAX_FACTS
            or any(not _fact_valid(value) for value in request['facts'])
            or len({value['id'] for value in request['facts']}) != len(request['facts'])):
        raise SceneError('no_facts')
    if request['scope'] == 'history' and any(value['kind'] not in scene.HISTORY_KINDS for value in request['facts']):
        raise SceneError('no_facts')
    return request


def current(g, runtime, expected=None, *, now=None):
    """Recheck the current run and semantic scene before/after generation and delivery."""
    from .agent.fence import read_canonical

    now = time.time() if now is None else now
    try:
        snapshot = read_record(Path(runtime) / scene.SCENE_FILE, limit=32768)
        ident = scene.identity(snapshot)
        canonical = read_canonical(g.state_dir)
        run = read_record(Path(runtime) / 'hanjuku_run.json', limit=256 * 1024)
        if (type(snapshot.get('schema')) is not int or snapshot['schema'] != 1
                or snapshot.get('version') != scene.VERSION
                or Path(runtime).resolve() != (Path(g.state_dir) / 'runtimes' / ident['runtime_id']).resolve()
                or Path(runtime).is_symlink() or Path(runtime).parent.is_symlink()
                or canonical.get('phase') != 'ready'
                or not scene.same_identity(canonical.get('active'), ident)
                or not scene.same_identity(run, ident)
                or run.get('playing') is not True or run.get('terminal_reason') or run.get('terminal_candidate')):
            raise SceneError('fence_lost')
        if snapshot.get('enabled') is not True:
            raise SceneError('disabled')
        at = scene.stamp(snapshot.get('observed_at'))
        if at is None or not 0 <= now - at <= 5:
            raise SceneError('stale')
        request = checked_request(snapshot)
        if not request['at'] <= now < request['expires_at']:
            raise SceneError('stale')
        if (request['scope'] == 'history' and request['epoch_id'] != scene.epoch_id(snapshot.get('scene') or {})
                or request['scope'] == 'scene' and request['scene_id'] != snapshot.get('scene_id')):
            raise SceneError('scene_changed')
        if expected is not None and (not scene.same_identity(expected, ident)
                or expected['request']['event_key'] != request['event_key']
                or (request['scope'] == 'scene' and expected['scene_id'] != snapshot.get('scene_id'))):
            raise SceneError('scene_changed')
        return snapshot
    except SceneError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise SceneError('unavailable') from None


def build_prompt(snapshot, recent=()):
    request = checked_request(snapshot)
    # No raw OCR, internal free-form reasons, model output, paths or identities.
    return '\n'.join([
        'SFC「半熟英雄」の短い日本語実況を1文か2文、120文字以内で書いてください。',
        '事実を自然につないでください。挨拶、定型の前置き、同じ言い回しの反復は不要です。',
        '入力の観測事実だけを使います。一般知識からHP、使用結果、勝因を補ってはいけません。',
        '観測事実は直前の履歴です。「いま」「現在」「これから」と断定せず、過去形で説明してください。',
        'battle_startのHPは開戦時、battle_resultのHPは戦闘終了時です。現在HPではありません。',
        'HPの数字ごとに将軍名とHPを省略せず、開戦時か終了時かも明記します。',
        '勝敗を話せるのはbattle_resultだけです。勝った側または負けた側の名前を主語にしてください。',
        '勝利は城の占領を意味しません。敗北は死亡を意味しません。死亡や全滅を断定しないでください。',
        'cards_usedだけが消費確認済みです。選択や予定から命中・損害・召喚阻止を断定しないでください。',
        'egg_priority_replanは兵士数の表示を受けて卵回復を優先する方針へ変えた事実です。回復完了ではありません。',
        'card_decisionは評価時の方針判断で、使用・命中・勝利ではありません。数値を話すなら「計算」「見積もり」を明記します。',
        'raw_damage_minは敵兵士の吸収前、damage_lower_boundは吸収後の損害下限、remaining_hp_upperは残りHP上限です。',
        'lethal=falseは計算下限で撃破を保証できない意味です。「倒せない」と断定しません。trueでも命中や勝利の実績ではありません。',
        '数字は半角算用数字、将軍名・城名は記録どおり。分からないことは言いません。話す材料がなければtextを空にします。',
        '兵士の数字は「兵士を○人補充」「総数は○人」、築城は「城名の築城費は○G」のように項目を区別します。',
        '計算の数字ごとにカード名と「吸収前のダメージ下限」「敵兵士のHP上限」「ダメージ下限」「残りHP上限」を明記します。',
        'JSONオブジェクトだけを返してください: {"text":"短い実況","fact_ids":["f1"]}',
        'fact_idsは実際に使った観測事実のidだけ。Markdownやコードフェンスは不要です。',
        '## 現場面（履歴との関係を判断する文脈。新しい事実の推測には使わない）',
        json.dumps(snapshot.get('scene') or {}, ensure_ascii=False),
        '## 観測事実', json.dumps(request['facts'], ensure_ascii=False),
        '## 今回使われた判断ラベルの意味（全て方針であり実行結果ではない）',
        json.dumps({fact['role']: CARD_ROLE_MEANINGS[fact['role']] for fact in request['facts']
                    if fact['kind'] == 'card_decision'}, ensure_ascii=False),
        '## 避ける直近の言い回し', json.dumps(list(recent)[-3:], ensure_ascii=False),
    ])


def parse_output(output, snapshot):
    if not isinstance(output, str) or len(output.encode()) > MAX_OUTPUT_BYTES:
        raise SceneError('invalid_output')
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        raise SceneError('invalid_output') from None
    facts = checked_request(snapshot)['facts']
    if not isinstance(value, dict) or set(value) != {'text', 'fact_ids'}:
        raise SceneError('invalid_output')
    text = value['text']
    ids = value['fact_ids']
    if (not isinstance(text, str) or not text.strip() or len(text.strip()) > scene.MAX_TEXT
            or not isinstance(ids, list) or not 1 <= len(ids) <= len(facts)
            or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)
            or not set(ids) <= {fact['id'] for fact in facts}
            or re.search(r'[\x00-\x1f<>`{}]|https?://', text)
            or not re.search(r'[ぁ-ゖァ-ヺ一-龯]', text)):
        raise SceneError('invalid_output')
    used = [fact for fact in facts if fact['id'] in ids]
    from .hanjuku_scene_grounding import claims_match
    if not claims_match(text, used):
        raise SceneError('invalid_output')
    numbers = {str(item) for fact in used for item in fact.values() if type(item) is int}
    if any(value not in numbers for value in re.findall(r'[0-9]+', unicodedata.normalize('NFKC', text))):
        raise SceneError('invalid_output')
    kinds = {fact['kind'] for fact in used}
    if (re.search(r'死亡|戦死|亡くな|全滅|一撃|瞬殺|いま|今まさに|現在|これから|必ず|確実に', text)
            or re.search(r'[〇一二三四五六七八九十百千万]+(?:人|体|点|回|枚|HP|ダメージ|G|割)', text)
            or ('castle_owned_observed' not in kinds and re.search(r'占領|奪取|奪還|制圧', text))
            or ('egg_priority_replan' in kinds and re.search(r'回復(?:しました|した|完了|済み)|回復させ', text))):
        raise SceneError('invalid_output')
    if 'card_decision' in kinds:
        if (re.search(r'命中|倒した|撃破した|使(?:い|っ|う|え)|使用(?!候補|前|判断|可否)|消費'
                      r'|卵(?:を)?落と|ダメージ(?:を)?(?:与え|入れ|出し|受け)', text)
                or any(fact.get('lethal') is False for fact in used)
                and re.search(r'倒せない|倒し切れない', text)
                or re.search(r'[0-9]', text) and not re.search(r'計算|見積|予測|下限|上限', text)):
            raise SceneError('invalid_output')
    names = {item for fact in used for key, item in fact.items()
             if key in {'ally', 'enemy', 'castle', 'card'} and isinstance(item, str)}
    names.update(item for fact in used for item in fact.get('cards_used', []))
    names.update({'エッグ', 'モンスター', 'ダメージ', 'ゴールド', 'メニュー', 'チャンス',
                  'ピンチ', 'バトル', 'レベル', 'タイミング', 'コスト', 'チャート', 'ボス'})
    if any(word not in names for word in re.findall(r'[ァ-ヺ][ァ-ヺー・]+', text)):
        raise SceneError('invalid_output')
    return text.strip()


def generate(g, runtime, snapshot, cfg, prompt):
    """Reference-run only the existing shell chain, with a private bounded job."""
    from .trading.soren_output import resolve_soren_root

    root = resolve_soren_root(g)
    if not (root / 'eloop_lib.sh').is_file():
        raise SceneError('unavailable')
    remaining = min(cfg['generation_timeout_s'], snapshot['request']['expires_at'] - time.time() - 2)
    if remaining < 1:
        raise SceneError('stale')
    with tempfile.TemporaryDirectory(prefix='.scene-', dir=runtime) as directory:
        work = Path(directory)
        prompt_path, output_path, last, failure, meta = [work / name for name in
                                                      ('prompt.txt', 'output.json', 'agent', 'failure', 'meta.json')]
        prompt_path.write_text(prompt, encoding='utf-8')
        env = dict(os.environ)
        # scene_commentary.enabled is the reviewed per-game opt-in. The helper
        # still honors Soren's explore/stop/pause and existing model policy.
        env['DOCICH_ALLOW_REAL_AI'] = '1'
        args = ['bash', str(Path(g.repo_root) / 'scripts/hanjuku_scene_generate.sh'),
                str(root), str(prompt_path), str(output_path), str(last), str(failure),
                str(meta), str(max(1, int(remaining)))]
        try:
            proc = OwnedSceneProcess(args, cwd=str(root), env=env)
            try:
                try:
                    rc = proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    raise SceneError('timeout') from None
            finally:
                proc.close()
        except SceneProcessUnavailable:
            raise SceneError('unavailable') from None
        if rc != 0:
            reason = (failure.read_text(encoding='utf-8').strip() if failure.is_file()
                      and failure.stat().st_size <= 128 else '')
            raise SceneError(reason if reason in REASONS else 'generation_failed')
        if not output_path.is_file() or output_path.stat().st_size > MAX_OUTPUT_BYTES:
            raise SceneError('invalid_output')
        try:
            role = json.loads(meta.read_text(encoding='utf-8')).get('role')
        except (OSError, ValueError, TypeError):
            role = None
        return output_path.read_text(encoding='utf-8'), role if role in ROLES else None


def record(runtime, state, event, *, reason='none', now=None, **metrics):
    event = event if event in EVENTS else 'skipped'
    reason = reason if reason in REASONS else 'worker_error'
    now = time.time() if now is None else now
    counters = state.setdefault('counters', {})
    for key in {event, reason} - {'none'}:
        counters[key] = min((scene.number(counters.get(key), 10**9) or 0) + 1, 10**9)
    state.update(schema=1, version=scene.VERSION, at=now, status=event, reason=reason)
    atomic_write_json(Path(runtime) / scene.WORKER_FILE, state)
    payload = {'schema': 1, **{key: state.get(key) for key in scene.KEYS}, 'at': now,
               'event': event, 'reason': reason, 'seq': state.get('last_request_seq')}
    if state.get('role') in ROLES:
        payload['role'] = state['role']
    for key in ('latency_ms', 'char_count'):
        if scene.number(metrics.get(key), 10**9) is not None:
            payload[key] = metrics[key]
    append_log(Path(runtime), scene.LOG_NAME, payload)


def consume(g, runtime, *, cfg=None, generate_fn=None, deliver_fn=None):
    """Consume at most one event. The caller holds the generation's worker lock."""
    cfg = cfg or scene.config(g.repo_root)
    raw = read_record(Path(runtime) / scene.SCENE_FILE, limit=32768) or {}
    try:
        ident = scene.identity(raw)
    except ValueError:
        return 'unavailable'
    state = read_record(Path(runtime) / scene.WORKER_FILE, limit=32768) or {}
    if not scene.same_identity(state, ident):
        state = {**ident}
    if not cfg['enabled']:
        record(runtime, state, 'skipped', reason='disabled')
        return 'disabled'
    try:
        snapshot = current(g, runtime)
        request = snapshot['request']
        now = time.time()
        if request['seq'] <= (scene.number(state.get('last_request_seq'), 10**9) or 0):
            return 'duplicate'
        last_at = scene.stamp(state.get('last_attempt_at'))
        if last_at is not None and now - last_at < cfg['cooldown_s']:
            return 'cooldown'
        state['last_request_seq'] = request['seq']
        if request['event_key'] in state.get('seen_keys', []):
            record(runtime, state, 'skipped', reason='duplicate')
            return 'duplicate'
        # Claim before starting a provider. A crashed or failed attempt never
        # regenerates the same scene, and each attempt consumes the rate limit.
        state['last_attempt_at'] = now
        state['role'] = None  # A previous job's role is not this attempt's evidence.
        state['seen_keys'] = [*(state.get('seen_keys') or []), request['event_key']][-scene.RECENT:]
        record(runtime, state, 'requested')
        record(runtime, state, 'generate_started')
        started = time.monotonic()
        output, role = (generate_fn or generate)(g, runtime, snapshot, cfg,
                                               build_prompt(snapshot, state.get('recent_texts') or []))
        state['role'] = role if role in ROLES else None
        current(g, runtime, snapshot)
        text = parse_output(output, snapshot)
        if text in state.get('recent_texts', []):
            raise SceneError('duplicate')
        record(runtime, state, 'generate_succeeded', latency_ms=int((time.monotonic() - started) * 1000),
               char_count=len(text))
        # Re-read live config too: disabling narration during a provider call
        # must suppress its completed answer before the shared audio queue.
        if not scene.config(g.repo_root)['enabled']:
            raise SceneError('disabled')
        current(g, runtime, snapshot)
        if deliver_fn is None:
            from .hanjuku_narration import deliver_scene as deliver_fn
        status = deliver_fn(g, load_game(g, 'hanjuku-hero'), Path(runtime), snapshot, text)
        if status != 'enqueued':
            record(runtime, state, 'deliver_failed', reason='delivery_failed')
            return 'delivery_failed'
        state['recent_texts'] = [*(state.get('recent_texts') or []), text][-8:]
        record(runtime, state, 'deliver_enqueued', char_count=len(text))
        return 'enqueued'
    except SceneError as exc:
        event = 'skipped' if exc.reason in {'stale', 'scene_changed', 'fence_lost', 'disabled', 'duplicate', 'no_facts'} else 'generate_failed'
        record(runtime, state, event, reason=exc.reason)
        return exc.reason
    except Exception:
        record(runtime, state, 'generate_failed', reason='worker_error')
        return 'worker_error'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--lock-fd', type=int, required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    fd = args.lock_fd
    try:
        g = load_global(repo)
        runtime = args.runtime
        if runtime.is_symlink() or runtime.parent.is_symlink() or runtime.resolve().parent != (g.state_dir / 'runtimes').resolve():
            return 2
        expected = (runtime / scene.LOCK_FILE).stat(follow_symlinks=False)
        actual = os.fstat(fd)
        if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode)
                or (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino)):
            return 2
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def stop(_signal, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        def unavailable(*_args):
            raise SceneError('unavailable')
        try:
            # This short-lived, validated worker owns no shared child process.
            # Never enable subreaping in the input bot or corner monitor.
            enable_subreaper()
            provider = None
        except SceneProcessUnavailable:
            provider = unavailable
        consume(g, runtime, generate_fn=provider)
        return 0
    except (OSError, ValueError, KeyboardInterrupt):
        return 2
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
