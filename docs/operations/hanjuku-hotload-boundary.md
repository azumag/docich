# Hanjuku 無停止 hot-load 境界（stable trampoline / 世代一致 / mixed 世代 fail-closed）

Issue: azumag/docich#1469。前提は PR #1465（強制解雇の未評価決定を保留する bot 側変更＋
判断trace中の `screen_stalled` 抑止）で、#1465 はこの境界が production に入り、
新しい active run がこの境界を持って起動した後に載せ直す。

## 何を解決するか

半熟英雄の観測境界は二つに分かれている。

- bot は `CommandBrain` が毎観測 `brains/hanjuku/bot.py` を fresh subprocess で起動するため、
  deploy 後の policy はそのまま次の判断で読まれる。
- agent loop と retro corner は長寿命 Python process で、`hanjuku_run` / `hanjuku_bot` を
  起動時に import した module object をそのまま実行し続ける。`RetroCornerManager._wait_hanjuku`
  自身も長寿命 loop なので、canonical deploy は process も module も reload しない。

そのため「bot だけ新コードへ進む」部分反映が起きうる。この状態では、判断待ちで画面が
静止したまま旧 `hanjuku_run` が 300 秒で `screen_stalled` を確定でき、run が
assessment 待ちのまま強制終了する。

## 境界の構成

### 1. stable trampoline（`src/docich/hanjuku_hotload.py`）

長寿命 observer（agent / corner）は毎観測この module を通る。

- `LOGIC_MODULES = ('hanjuku_bot', 'hanjuku_run')` の source bytes から SHA-256 の
  **Hanjuku logic generation** を決定的に求める（`deployed_generation()`）。
- その process が実際に読んだ generation は、各 logic module が import 時に記録する
  `LOGIC_GENERATION` が表す。trampoline は両者を比較し、
  - 一致していれば process 自身の module をそのまま使う（通常時は従来と同一挙動・
    monkeypatch もそのまま効く）、
  - 一致していなければ logic 集合全体を **generation 単位の non-共有 namespace に
    fresh-load** する。単純な `importlib.reload` と違い、実行中の frame が
    半端に reload された module を見ることはなく、process 再起動も不要。
- deploy は「次の観測」から効く。game / runtime_id / generation / lease_id、input gate、
  RetroArch、save/state、配信・audio process は触らない。

`hanjuku_policy`（467 KB、bot subprocess 側で実行される）は reload 集合に含めない。
境界は「長寿命 observer が実行する per-observation の terminal/policy 判定」に限る。

### 2. 世代一致の durable 公開

観測ごとに、observer は自分が実行した generation を runtime 直下へ durable に公開する。

- `hanjuku_generation_agent.json` / `hanjuku_generation_corner.json`
- `{schema, game, runtime_id, generation, lease_id, role, logic_generation, bot_version, at, pid}`
- 読み出しは runtime identity と完全一致を要求（不一致は fail-closed で例外）。
  同一世代の再公開は 30 秒に一回へ抑える。
- corner は `retro_corner.json`（corner state）の `hotload` にも `consistency()` の結果を残す。

### 3. mixed 世代 fail-closed

`generation_gate(runtime_dir, identity, reason)` は次の 3 状態を返す。

- `consistent` — 両 observer が同じ generation を公開。通常どおり。
- `mixed` — 両方が公開し、世代が異なる。**新 policy 依存の stall teardown を取らない**。
- `unestablished` — どちらかが未公開。境界が使われていない runtime（旧 deploy、
  単独 observer）なので従来どおり。

`mixed` のとき `_wait_hanjuku` は `screen_stalled` の teardown を保留し、入力は
terminal evidence が既に保持したまま観測を続ける（`hotload_generation_hold` event）。
`HOTLOAD_MIXED_GRACE_SECONDS = 60` を超えても一致しない場合は、従来の semantics で
teardown し `hotload_generation_timeout` を記録する（観測を止めた process が corner を
永久に塞がないための上限）。`game_over` と既存の `input_stalled` は policy 非依存なので
gate しない。

teardown 前の durable evidence 再検証（`runtime_identity` / `terminal`、restoring の
replay 検証、`hanjuku_predictions`）も trampoline 経由にした。これで「旧 generation の
検証関数が新 generation の証拠を読む」経路が無くなる。

## 運用時に見えるもの

- 各 runtime の `hanjuku_generation_agent.json` / `..._corner.json` の `logic_generation`。
  deploy 直後に両者が揃えば境界は成立している。
- `hanjuku_events.jsonl` の `hotload_generation_hold` / `hotload_generation_timeout`。
- corner state の `hotload`。

## この変更で未実施のこと

- #1465 の載せ直し・現ラン hot-load は行っていない。#1465 の抑止（assessment_required 中の
  `screen_stalled` 抑止）を有効化する側で、`consistent` を要求する。
- 実 VM / 実 run での deploy 相当の世代切替は未実施（ローカルテストのみ）。
