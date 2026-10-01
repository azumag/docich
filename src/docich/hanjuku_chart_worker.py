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
from . import hanjuku_reference as reference
from .hanjuku_egg_reference import general_max_hp
from .game_switch import atomic_write_json
from .hanjuku_run import append_log
from .retroarch_boundary import read_record

STATE = 'hanjuku_chart_worker.json'
LOCK = 'hanjuku_chart_worker.lock'
LABEL = 'RADIO:hanjuku-chart-adjust'
DECISION_TAIL_BYTES = 131072
RESULT_DECISIONS = frozenset({'order_launched', 'order_failed', 'order_retry', 'battle_start',
                              'battle_result', 'chart_adjust_applied', 'chart_interim_order'})
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
            entry = {k: item.get(k) for k in ('decision', 'chart_step', 'general', 'ally', 'castle',
                                              'target', 'enemy', 'outcome', 'side',
                                              'enemy_hp', 'ally_hp')
                     if item.get(k) is not None}
            # The 卵落 rule needs max HP, which a wounded opening reading is
            # not: add the fixed char.csv HP for names it knows.
            for side in ('ally', 'enemy'):
                name = entry.get(side)
                if side == 'ally' and name == chart.HERO:
                    name = 'しゅじんこう'
                max_hp = general_max_hp(name)
                if max_hp is not None:
                    entry[f'{side}_max_hp'] = max_hp
            if entry:
                out.append(entry)
    return out[-limit:]


def build_prompt(request: dict, results: list[dict]) -> str:
    chapter = request.get('chapter') or 1
    base = [{k: (list(v) if isinstance(v, tuple) else v) for k, v in o.items()}
            for o in chart.all_orders(chapter)]
    purchases = [{**plan, 'month': list(plan['month']),
                  'cards': [list(card) for card in plan['cards']],
                  'priority': [list(card) for card in plan.get('priority') or ()]}
                 for plan in chart.purchases(chapter)]
    measured = sorted(chart.castles(chapter))
    allowed_castles = measured or sorted(chart.CASTLE_NAMES.get(chapter, ()))
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
        f'- 城名は次のいずれか: {json.dumps(allowed_castles, ensure_ascii=False)}',
        f'- 切り札名は次のいずれか: {json.dumps(sorted(adjust.CARD_NAMES), ensure_ascii=False)}',
        f'- 指示は1〜{adjust.MAX_ORDERS}件。step は英数字・_・- の12文字以内の一意な名前（例 J1, J2）。'
        '基準チャートのstep名は禁止。',
        f'- cards は1指示あたり最大{adjust.MAX_CARDS_PER_ORDER}枚。在庫は保証されないので必要な時だけ。',
        '- 携行する cards は「現在の状況」の card_stock で在庫が1以上と実測された札だけにすること。'
        'card_stock に無い札・在庫0の札は、そのプランの purchases で買う札を除いて cards に含めない'
        '（card_stock が空または無い場合は携行在庫が不明なので cards は空にする）。'
        '章に存在しない切り札を計画すると、出撃時に選べず保留と破棄になる。',
        '- キャトルミューは火星人イベント限定で店では買えない。実在庫があれば1枚を優先活用する。'
        '通常将軍を一撃で倒し、EMへ224ダメージと石化、ボスへ90ダメージ。ID28なので2枚携行は避ける。',
        '- 基本戦術: 携行する切り札のID合計が48以上だと敵将軍がエッグを使う。cards のID合計は47以下にすること'
        '（ID: ' + '、'.join(f'{n}={i}' for n, i in sorted(reference.ALL_CARD_IDS.items(), key=lambda kv: kv[1])
                             if n in adjust.CARD_NAMES) + '）。'
        '推奨の組: ' + ' / '.join('+'.join(s) for s in reference.RECOMMENDED_CARD_SETS
                                 if all(c in adjust.CARD_NAMES for c in s)) + '。'
        '48以上になる分はbotが実行時に外す。',
        '- 基本戦術: 敵将軍が卵持ちのとき、切り札の卵落値 > (敵・味方将軍の最大HP合計 mod 16) なら'
        '敵は卵を落とし、以後召喚を使えなくなる。卵落値: '
        + '、'.join(f'{n}={v}' for n, v in sorted(reference.EGG_DROP_VALUES.items(),
                                                  key=lambda kv: (-kv[1], kv[0]))) + '。'
        '将軍を倒し切れない相手や召喚が脅威の場面では、会戦する将軍の最大HP合計から余りを計算し、'
        '卵落値が上回る札を携行するとよい。開戦時HPは負傷していることがあるため使わず、'
        '「直近の実績」の battle_start にある ally_max_hp / enemy_max_hp（固定最大HPの判明分）で計算すること。',
        '- 基本戦術: 城レベルが高いほど防衛側のエッグモンスターの防御・速さと防衛将軍の突撃速度が上がる'
        '（ボス城は補正なし）。定員は城Lv−1で、防衛側は将軍が倒されるたびに城レベルが1下がる。',
        '- after は出撃の前に満たすべき状態で、null / ["captured", 城名] / ["all_captured"] のいずれか。'
        '["captured", X] は「すでにXを奪取済み」の時だけ出撃する条件なので、'
        'target が X の指示（攻略・奪回）に付けると条件は指示自身の結果待ちになり永遠に出撃できない。'
        'いま奪う城の指示には必ず after: null を使うこと。',
        '- source は将軍を出す自軍の城。target は攻める城。general は将軍名。',
        '- general は「駐留（garrison）」でその source にいると記録された将軍にすること。'
        '別の城にいると記録された将軍・進軍中（en_route）の将軍・失った城（lost）からの出撃は実行できず破棄される。'
        '駐留が記録されていない城は将軍不明として扱う。',
        '- purchases は任意。month は [年, 月]（これから来る月初）。generals は新規登用人数（現状は記録のみ）。'
        'cards は基準チャートの購入予定にある札か、card_stock で在庫を見たことのある札だけにすること。',
        '- 出力はJSONオブジェクト1つだけ。説明文やコードフェンスは不要。',
        '',
        '## 基準チャート（参考。書き換え不可）',
        json.dumps({'orders': base, 'purchases': purchases}, ensure_ascii=False),
        '',
        '## 現在の状況',
        json.dumps({k: request.get(k) for k in adjust.REQUEST_FIELDS if k != 'request_id'},
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
        if (current.get('request_id') != request['request_id']
                or current.get('request_digest') != request.get('request_digest')):
            # The payload revision changed while generating: the answer may
            # plan from stock/garrison facts that are no longer current.
            status = 'superseded'
        else:
            adjust.save(runtime_dir, parse_output(output, request, agent or 'unknown'), request)
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
        if (adjusted and adjusted['request_id'] == request['request_id']
                and adjusted.get('request_digest') == request.get('request_digest')):
            return None
        state = read_record(runtime_dir / STATE) or {}
        same_revision = (state.get('request_id') == request['request_id']
                         and state.get('request_digest') == request.get('request_digest'))
        attempts = state.get('attempts', 0) if same_revision else 0
        if attempts >= cfg['max_attempts']:
            return None
        atomic_write_json(runtime_dir / STATE, {'request_id': request['request_id'],
                                                'request_digest': request.get('request_digest'),
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
