# VERIFY-5778 — brain例外 KeyError@corridor.py:718:_probe_spend

検証日: 2026-11-28
対象: azumag/docich#5778
worktree: `/home/hatch/work/docich-5778` / branch `verify/5778` / ref `c032a6a37c0bd96d6e683bd37c2dbd53ded619eb`

## 結論

**(a) 実欠陥**

issue が示す `KeyError("'count'")` は、当時の `corridor._probe_spend` に実在した欠陥であり、誤検知ではない。現行 main の `hanjuku-hero` 側コードには、同じ事象を明示するコメント付きの防御 (`state.setdefault('count', 0)`) と安全な読み取り (`int(state.get('count') or 0)`) が既に入っている。owner PR #5780 も同じ症状・原因を「探索状態に count が無くても落ちないようにする」として修正し、マージ済みである。

したがって issue の判定自体は実欠陥を正しく検出している。ただし現行 main の `src/docich/hanjuku` では既に修正済みであり、新たなコード修正は不要と判断する。

## 検証範囲と制約

- gh コマンド禁止のため、issue 本文と PR #5780 は GitHub の公開ページを read-only に参照した。
- 本番 decision log は指示どおりローテーション済みとして探していない。issue 本文と現行 main のコードだけで検証した。
- 非公開 handoff 正本は `DOCICH_HANDOFF_PATH` の明示参照を取得できなかったため未読。通常の read-only 検証を GitHub と現行コード根拠で実施した。
- python3 実行は承認却下された。したがって fingerprint の SHA-1 値は独立には再計算できていない。式と入力値を下記へ残す。

## fingerprint / occurrence の計算式

重要: `brain_error` の fingerprint は、通常の月次監視インシデント用 `month_audit._fingerprint(chapter, kind, signals)`（SHA-256）ではない。`brain_error` は `src/docich/hanjuku/fault_issue.py` が record を作り、`month_audit.record_incident` / `spawn_issue_filer` の起票管線へ渡す構造である。

現行 main の該当式:

- `src/docich/hanjuku/fault_issue.py` `note_fault`
  - `digest = hashlib.sha1(f'{kind}:{signature}'.encode('utf-8')).hexdigest()[:16]`
  - `incident_id = f'{digest}-{int(now)}-{count}'`
  - この `digest` が record の `fingerprint` へそのまま入る。
- `src/docich/hanjuku/month_audit.py` は record 内の `fingerprint` / `incident_id` を marker として出力し、起票時の fingerprint 重複排除に使う。brain_error 用に別の fingerprint を再計算しない。

今回の入力値:

- `kind = 'brain_error'`
- `signature = 'KeyError@corridor.py:718:_probe_spend'`
- SHA-1 入力文字列: `brain_error:KeyError@corridor.py:718:_probe_spend`
- issue の `last_at` / `created_at`: `1795808629.37752`
- `int(now)`: `1795808629`
- `count`: `3`

検品ゲート用の再計算式:

```python
import hashlib
kind = 'brain_error'
signature = 'KeyError@corridor.py:718:_probe_spend'
digest = hashlib.sha1(f'{kind}:{signature}'.encode('utf-8')).hexdigest()[:16]
incident_id = f'{digest}-{int(1795808629.37752)}-3'
# digest は issue の fingerprint 3f82c4ab0d3c2e4f と比較する
# incident_id は issue の occurrence 3f82c4ab0d3c2e4f-1795808629-3 と比較する
```

再計算の成否: **未計算**（python3 実行が承認却下されたため）。ただし issue JSON 内では次の内部整合が確認できる。

- `fingerprint` = `3f82c4ab0d3c2e4f`
- `incident_id` の先頭 = 同じ `3f82c4ab0d3c2e4f`
- `incident_id` の epoch 部 = `1795808629` で、`int(last_at)` / `int(created_at)` と一致
- `incident_id` の末尾 = `3` で、`fault.count` と一致

したがって occurrence は形式・入力の対応として一致する。SHA-1 digest そのものの暗号学的再計算だけが未実施である。

## KeyError が出る経路

### 呼び出し経路

現行 main でも同じ構造の経路がある（行番号は当時 v1575 からずれている）:

1. `src/docich/hanjuku/strategy/orders.py` `_orders(mem)` が chapter 9 では `corridor.orders(mem)` を返す。
2. `src/docich/hanjuku/strategy/corridor.py` `orders()` で `_due_probe(...)` が見つかると、現行 line 550 付近で `_probe_spend(mem, 'roster_probe_due', budgeted=False)` を呼ぶ。
3. 当時の `_probe_spend` は、記録用 `observed_metric` を作る箇所で `state['count']` を直接読み、`count` が無い dict では `KeyError("'count'")` になった。
4. `brains/hanjuku/bot.py` は `KeyError` を `invalid observation` として exit code 2 にし、その観測の入力を失う。同時に `_note_exception_fault` が `fault_issue.note_fault` へ署名を渡す。

issue の detail `corridor.py:718:_probe_spend < corridor.py:550:orders < orders.py:1006:_orders` はこの経路と一致する。現行 main では `orders.py` 側の呼び出し行は 1009-1011 へ移動しているが、分岐構造は同じである。

### `count` を持たない条件

`_probe_spend` は月が変わっていれば `state.clear()` 後に `count=0` を入れる。問題は **同じ月の `corridor_probe` state に `count` だけが無い場合**である。

その状態は少なくとも次で成立しうる:

- `_probe_exhausted_once()` は `mem.setdefault(PROBE, {})` で state を作り、`exhausted` と `month` だけを置いても `count` は置かない。プローブ対象が最初に無い月では、通常の `_probe_spend` 成功前にこの state が作られうる。
- owner PR #5780 は、旧バージョン由来の永続 state や途中で clear された state に `count` が無いことを原因として説明している。

当時の `_probe_spend` では:

- `budgeted=True` かつ `idle=False` なら `state['count'] = ...` の代入が先に走るため、欠けていてもキーが作られる。
- 今回の `roster_probe_due` は `budgeted=False` なので count を代入しない。
- `idle=True` のプローブも count を代入しない。
- そのため「同じ月・count なし」の state で `budgeted=False` または `idle=True` の spend が走ると、末尾の記録用 `state['count']` 直接参照で落ちる。

### なぜ 3 回連続したか

brain は観測ごとのプロセスで state を読み、正常終了後に書き戻す。`_probe_spend` 内で例外になると、probe order の採用・実行だけでなく state の正常な書き戻しも失われる。メモリ上で一部キーが変わっても永続 state は同じままなので、次観測でも同じ due probe が選ばれ、同じ `KeyError` が短周期で再発する。

issue JSON の `observations=121310` に対し `last_input_observations=121307` であり、ちょうど 3 観測が入力なしだったことも、fault count 3 と一致する。`first_at=1795808628.947571`、`last_at=1795808629.37752`、window 0.4 秒という短さも、この状態不変の再発と整合する。

### 現行 main の防御

現行 `src/docich/hanjuku/strategy/corridor.py` には次の修正がある。

- `_probe_spend` 冒頭で `state.setdefault('count', 0)` を行う。
- コメントに「 persisted probe state ... must not crash ... KeyError('count') ... live 2026-11-28 04:3x 」とあり、今回と同じ時刻・症状を直接参照している。
- `observed_metric` の `probe_count` も `int(state.get('count') or 0)` になっている。

owner PR #5780（merged）の説明も同じで、`state.setdefault('count', 0)`、記録側の `int(state.get('count') or 0)`、ローカル 703 passed、実機で入力再開と記載されている。incident は v1575、PR は v1586/v1588、現行 main は v1593 なので、現行 main には修正後の版が入っている。

さらに PR の初回 commit `238c94d92d7af0c2b4f85f7b6b342b8afa063c68` の patch を確認すると、差分は `state.setdefault('count', 0)` の追加と `probe_count: state['count']` → `probe_count: int(state.get('count') or 0)` の変更であり、当時 line 718 付近がまさに直接参照だったことを裏付ける。同 patch には corridor の回帰テスト追加はなく、テスト変更は diagnostics の version allowlist のみだった。

なお、現行 worktree の静的検索でも「due probe 前に count を消す/置かない状態を明示的に作る」回帰テストは `tests/test_hanjuku_corridor_probe_budget.py` 内に見つからなかった。実装防御は存在するが、この特定条件を直接固定するテスト覆盖は確認できなかった。

## 過去の同型 brain_error との異同

同型の先行事象として、2026-11-24 の g1342 / ch4 帰還Yジャンプがある（`docs/operations/hanjuku-recall-yjump-goal-20261124.md`）。

共通点:

- 1 観測の fail-soft（brain exit 2）が、状態が進まないため毎観測繰り返され、実質的な入力停止になる。
- 例外署名は「例外クラス + 最内フレーム」で安定化され、3 回再発で `brain_error` issue になる。
- 今回の `fault_issue.py` 自体、この 2026-11-24 事象を受けて追加された再発検出機構である。

相違点:

- 2026-11-24 は ch4 の `'ボス'` ラベルに実セルが無く、`boss_probe_cell` が None を返して TypeError になった。対応は goal 解決の検証・修正だった。
- 今回は chapter 9 corridor の probe state に `count` が無く、`budgeted=False` の受領証 probe が直接参照して KeyError になった。対応は状態の防御的初期化だった。

## 補足: hanjuku_speedrun の frozen copy

`src/docich/hanjuku_speedrun/hanjuku/strategy/corridor.py` は生成済み frozen copy で、現時点でも `observed_metric` に `state['count']` の直接参照が残っている。ただしこの copy は `docich.hanjuku_speedrun` 用で、issue の runtime game は `hanjuku-hero`、今回検証した現行 main 側の `src/docich/hanjuku` とは別系統である。したがって #5778 の判定には影響しないが、speedrun copy が実運用で同じ state 形状を持つ場合は別件の残リスクになりうる。frozen copy の扱い（freeze 解除まで編集しない）があるため、ここでは修正提案まで行わない。

## 最終判定の根拠まとめ

- issue の署名・呼び出し経路は現行コード構造と一致する。
- `count` が無い同月 state で `budgeted=False` の `_probe_spend` が走ると、当時コードでは確実に `KeyError('count')` になる。
- 例外で state 書き戻しが失われるため、同一入力・同一永続状態で短周期再発する。
- issue の observations / last_input_observations が 3 観測分の入力欠落を示し、再発 count と一致する。
- owner PR #5780 が同じ症状・原因・修正・実機入力再開を独立に確認し、現行 main にマージ済みである。

結論は **(a) 実欠陥**。現行 main の `hanjuku-hero` 側では修正済み。fingerprint の SHA-1 再計算のみ未実施（式と入力値は上記）。

## 検品ゲート確認（Muse、2026-11-28 09:53 JST）

- fingerprint の SHA-1 を再計算した：`sha1('brain_error:KeyError@corridor.py:718:_probe_spend')[:16] = 3f82c4ab0d3c2e4f` で issue の fingerprint と一致。occurrence `3f82c4ab0d3c2e4f-1795808629-3` も一致。
- 現行 main（c032a6a37）の `src/docich/hanjuku/strategy/corridor.py` で防御を確認：`state.setdefault('count', 0)`（708行目）、`int(state.get('count') or 0)` による安全な読み取り（694・716・722・783行目ほか）。owner PR #5780 の修正が main に入っている。
- 結論 (a) 実欠陥は、issue の署名・経路と現行コード・#5780 の diff で裏付けられている。現行 main では修正済みのため新規の実修正 PR はなし。issue は owner クローズ待ち。
- 残件：回帰テストで「count を持たない同月 state + due probe」を直接固定するものは現行 tests には確認できず（unit 報告どおり）。speedrun frozen copy の `state['count']` 直接参照は別系統のため本件の判定には影響しない。
