"""Asynchronous LLM worker for adjusted Hanjuku charts (#1085 L1).

Whichever long-lived process observes the runtime (agent or corner monitor)
calls ``consider`` after each observation. Under a non-blocking file lock it
claims the latest ``hanjuku_chart_adjust_request.json`` and starts at most one
daemon thread (which keeps the lock until the answer is saved, so agent and
corner observers never generate the same request twice in parallel) that asks the configured AI chain for a complete order list.
The answer is accepted only through ``hanjuku_chart_adjust.save`` (strict
validation against measured chart facts). The bot keeps running a JEV interim
sortie (no hold) until a valid answer lands; nothing here sends input.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import threading
import time

from . import hanjuku_chart as chart
from . import hanjuku_chart_adjust as adjust
from .game_switch import atomic_write_json
from .hanjuku_run import append_log
from .retroarch_boundary import read_record

STATE = 'hanjuku_chart_worker.json'
LOCK = 'hanjuku_chart_worker.lock'
LABEL = 'RADIO:hanjuku-chart-adjust'
DECISION_TAIL_BYTES = 131072
RESULT_DECISIONS = frozenset({'order_launched', 'order_failed', 'order_retry', 'battle_result',
                              'chart_adjust_applied', 'chart_interim_order'})
_busy = threading.Event()


def _recent_results(runtime_dir: Path, limit=24) -> list[dict]:
    path = runtime_dir / 'hanjuku_decisions.jsonl'
    if not path.exists() or path.is_symlink():
        return []
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - DECISION_TAIL_BYTES))
        lines = stream.read().decode('utf-8', 'ignore').splitlines()
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('decision') in RESULT_DECISIONS:
            out.append({k: item.get(k) for k in ('decision', 'chart_step', 'general', 'ally', 'castle',
                                                  'target', 'enemy', 'outcome', 'side')
                        if item.get(k) is not None})
    return out[-limit:]


def build_prompt(request: dict, results: list[dict]) -> str:
    chapter = request.get('chapter') or 1
    base = [{k: (list(v) if isinstance(v, tuple) else v) for k, v in o.items()}
            for o in chart.orders(chapter)]
    example = {'orders': [{'step': 'J1', 'general': 'ココット', 'source': 'ほんじょう',
                           'target': 'スペンソニア', 'cards': ['ダイチスイム'], 'after': None,
                           'note': '理由を短く'}],
               'purchases': {'month': [1, 8], 'cards': [['イッテツーン', 3]], 'soldiers': 20,
                             'generals': 0, 'note': ''},
               'reason': '全体方針を短く'}
    return '\n'.join([
        'あなたはSFC「半熟英雄」を決定的botに遊ばせるためのチャート作成者です。',
        'botは基準チャートの指示を使い切り、出撃できる指示がありません。',
        '現在の状況から、基準チャートとは独立した「完全な指示列」をJSONで作ってください。',
        '',
        '## 制約',
        f'- 城名は次のいずれか: {json.dumps(sorted(chart.castles(chapter)), ensure_ascii=False)}',
        f'- 切り札名は次のいずれか: {json.dumps(sorted(adjust.CARD_NAMES), ensure_ascii=False)}',
        f'- 指示は1〜{adjust.MAX_ORDERS}件。step は英数字・_・- の12文字以内の一意な名前（例 J1, J2）。'
        '基準チャートのstep名は禁止。',
        f'- cards は1指示あたり最大{adjust.MAX_CARDS_PER_ORDER}枚。在庫は保証されないので必要な時だけ。',
        '- after は null / ["captured", 城名] / ["all_captured"] のいずれか。',
        '- source は将軍を出す自軍の城。target は攻める城。general は将軍名。',
        '- purchases は任意。month は [年, 月]（これから来る月初）。generals は新規登用人数（現状は記録のみ）。',
        '- 出力はJSONオブジェクト1つだけ。説明文やコードフェンスは不要。',
        '',
        '## 基準チャート（参考。書き換え不可）',
        json.dumps(base, ensure_ascii=False),
        '',
        '## 現在の状況',
        json.dumps({k: request.get(k) for k in ('chapter', 'off_chart_reason', 'captured',
                                                 'orders', 'blocked', 'gold', 'month')},
                   ensure_ascii=False),
        '',
        '## 直近の実績',
        json.dumps(results, ensure_ascii=False),
        '',
        '## 出力例（形式のみ）',
        json.dumps(example, ensure_ascii=False),
    ])


def parse_output(text: str, request: dict, agent: str) -> dict:
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no json object')
    doc = json.loads(text[start:end + 1])
    if not isinstance(doc, dict):
        raise ValueError('not an object')
    # Identity fields come from the request, never from the model.
    doc.update(schema=adjust.SCHEMA, chapter=request['chapter'], request_id=request['request_id'],
               source=f'llm:{agent}'[:40], generated_at=time.time())
    if isinstance(doc.get('reason'), str):
        doc['reason'] = doc['reason'][:200]
    for order in doc.get('orders') or ():
        if isinstance(order, dict) and isinstance(order.get('note'), str):
            order['note'] = order['note'][:adjust.MAX_NOTE]
    if isinstance(doc.get('purchases'), dict) and isinstance(doc['purchases'].get('note'), str):
        doc['purchases']['note'] = doc['purchases']['note'][:adjust.MAX_NOTE]
    return doc


def _default_generate(g, cfg, prompt: str) -> tuple[str, str]:
    from .ai_generate import run_prompt
    # A non-empty agents chain in the game config is the billed-run consent.
    env = {**os.environ, 'DOCICH_ALLOW_REAL_AI': '1'}
    result = run_prompt(g, label=LABEL, agents=cfg['agents'], prompt_text=prompt,
                        timeout=cfg['timeout_s'], timeout_sec=float(cfg['timeout_s'] + 30), env=env)
    if result.returncode != 0 or not result.output.strip():
        raise RuntimeError(f"rc-{result.returncode}:{result.failure_kind or 'empty'}")
    return result.output, result.last_agent


def _run(g, runtime_dir: Path, request: dict, cfg, generate, lock_fd=None):
    """Generate once. ``lock_fd`` (the claimed worker lock) is released only
    after the answer is saved and logged, so no other observer process can
    start a second generation for this runtime meanwhile."""
    status, detail = 'saved', None
    started = time.monotonic()
    try:
        prompt = build_prompt(request, _recent_results(runtime_dir))
        output, agent = generate(g, cfg, prompt)
        current = read_record(runtime_dir / adjust.REQUEST_FILE) or {}
        if current.get('request_id') != request['request_id']:
            status = 'superseded'
        else:
            adjust.save(runtime_dir, parse_output(output, request, agent or 'unknown'))
    except ValueError:
        status = 'invalid_output'
    except RuntimeError as exc:
        # Failure kinds are dispatcher classifications, never provider text.
        status, detail = 'generate_failed', str(exc)[:80]
    except Exception as exc:
        status, detail = 'worker_error', type(exc).__name__
    finally:
        _busy.clear()
    try:
        append_log(runtime_dir, adjust.HISTORY_LOG, {
            'event': 'adjust_worker', 'at': time.time(), 'status': status, 'detail': detail,
            'request_id': request.get('request_id'),
            'elapsed_s': round(time.monotonic() - started, 3),
            **{k: request.get(k) for k in ('game', 'runtime_id', 'generation', 'lease_id')}})
    except Exception:
        pass
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def consider(g, game, runtime_dir: Path, *, terminal=False, generate=None, background=True):
    """Claim the latest request and start at most one generation. Never blocks input."""
    cfg = adjust.game_settings(game)
    if not cfg['enabled'] or terminal:
        return None
    runtime_dir = Path(runtime_dir)
    request = read_record(runtime_dir / adjust.REQUEST_FILE)
    if not request or not isinstance(request.get('request_id'), str):
        return None
    lock_path = runtime_dir / LOCK
    if lock_path.is_symlink():
        return None
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    owned = True                 # this frame owns fd until handed to a generation
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        if _busy.is_set():
            return None
        adjusted = adjust.load(runtime_dir)
        if adjusted and adjusted['request_id'] == request['request_id']:
            return None
        state = read_record(runtime_dir / STATE) or {}
        attempts = state.get('attempts', 0) if state.get('request_id') == request['request_id'] else 0
        if attempts >= cfg['max_attempts']:
            return None
        atomic_write_json(runtime_dir / STATE, {'request_id': request['request_id'],
                                                'attempts': attempts + 1, 'at': time.time()})
        _busy.set()
        generate = generate or _default_generate
        owned = False
        if not background:
            _run(g, runtime_dir, request, cfg, generate, fd)
            return request['request_id']
        try:
            # The thread inherits the locked fd and releases it when done.
            threading.Thread(target=_run, args=(g, runtime_dir, request, cfg, generate, fd),
                             daemon=True, name='hanjuku-chart-adjust').start()
        except Exception:
            owned = True
            _busy.clear()
            return None
        return request['request_id']
    finally:
        if owned:
            os.close(fd)
