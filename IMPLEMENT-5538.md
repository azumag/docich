# IMPLEMENT-5538 — 召喚画面で凍結した announce card_flow の有界救済

docich issue #5538（ch8 ノリウツール）と #5553（ch6 ピッグローラー）。両 issue とも
card_miss「選択済み・消費未確定の announce 凍結 card_flow」亜型で結論 (a) 実欠陥。
検証記録 VERIFY-5538.md §6 と VERIFY-5553-REF.md §3/§6 の機構に従い実装した。

## 欠陥（再掲）

敵召喚が通常戦闘メニューや切り札一覧を `egg_battle_menu` へ置き換えると、
「札を選択入力済み・消費未確定」の `card_flow`（`stage == 'announce'`、または
`selection_planned` を持つ flow）はこの画面系では二度と進行しない
（announce 待ちは `kind == 'card_list'` の `card_list_step` でしか進まない）。
ところが `egg_battle_step` の `interrupted_flow` 救済は
`stage in ('menu','down','list')` かつ `selection_planned`/`read_kit` を持たない
flow だけをクリアするため、announce 系の flow だけが選別されて残る。
残った真値が揭示ゲート（`_summon_card_row_hidden` の
`battle.get('card_flow')` 条件）と再オープンゲートを同時に抑止し続け、
残りの携行札は画面に存在するのに一度も揭示・選択されないまま
`こうげき` が確定されていた（#5538/#5553 とも `scrolls: 不明` で確定）。

## 実施内容

### 1. `egg_battle_step` の interrupted_flow 救済を announce 系へ拡張

`src/docich/hanjuku/flows/battle_menus.py` の attack 分岐（既存救済直後）に
`elif` を追加し、`stage == 'announce'` または `selection_planned` を持つ flow が
`egg_battle_menu` 上で観測されるたび `battle['summon_card_flow_stall']`
（`{card, stage}` を scope とする観測カウンタ）を加算する。
観測回数が `SUMMON_CARD_FLOW_STALL_LIMIT`（=3）に達したら
`_card_use_unclassified`（`judgment/battle.py:152`、戦闘終了時と同じ既存機構）で
その flow の札を `cards_unclassified` へ移して `battle['card_flow'] = None` とし、
カウンタを消去する。一覧は既に閉じているため B は送らない。
解放は `_summon_card_row_hidden` 評価より前に行われるため、解放された同じ観測から
残りの `cards_left` に対する揭示スクロール・再オープンが通常どおり働く。
flow が announce 系でなくなった経路（救済済み・他経路で消滅・read_kit 等）は
`else` でカウンタを掃除する。

**有界回数の根拠**: announce 系 flow はこの画面系では構造的に進行不能であり、
待っても回復しない。上限が存在するのは「1フレームの画面誤認で凍結扱いしない」
猶予のためだけで、観測間隔は約 1.5 s（`hanjuku-hero.toml` interval_ms 1500）のため
3観測 ≒ 4.5 s は再描画フリッカを吸収するのに十分であり、滞留1観測ごとに
「切り札を開かない `こうげき`」が1ターン増える（= 修正対象の欠陥そのもの）ため
これ以上伸ばす意味がない。`SUMMON_CARD_SCROLL_LIMIT = 3`（同一箱の揭示上限）と
同スケールとした。

**温存設計との整合**: `test_selected_or_announcement_card_flow_is_not_reopened`
（1観測で announce flow を温存する固定テスト）は上限未満のため引き続き成立する。
救済の拡張は `egg_battle_step` 内に限定しており、通常メニュー文脈
（`battle_menu_step` / `card_list_step` / `battle_step` / `_hp1_resource_hold` /
`_hero_retreat_open`）の announce 温存には一切触れていない。
`cards_unclassified` は「未使用」でも「使用済み」でもない保守的分類で、
`enemy_hp_at_open` が int の flow では `card_pending_confirm` による事後確定経路も
`_card_use_unclassified` 側がそのまま保持するため、`cards_used` ガードや監査の
unconfirmed 意味論と矛盾しない。

### 2. emit の `observed_metric` に card_flow を追加

`egg_battle_step` の `battle_boss_card_missed` 起票部で、その時点に残っている
`battle['card_flow']` の `{card, stage, selection_planned}` を `observed_metric`
へ載せる（flow が無ければ `None`）。#5538/#5553 は `scrolls: 不明` からの演繹でしか
亜型を確定できなかったため、本亜型を issue 単体で識別可能にする。

さらに PR #5521 と同型の配線として、`month_audit.py` 側も更新した（emit だけでは
issue 本文に出ないため）:

- `_track_card_miss` の `battle` 投影に `'card_flow'` を追加。新設の
  `_bounded_card_flow` で `{card: _bounded_text(48), stage: _bounded_text(16),
  selection_planned: bool のみ}` に有界化し、非 dict は `None`。
- `_render_card_miss` に `- card_flow: ...` 行を追加。新設の `_card_miss_flow` が
  `card=… / stage=… / selection_planned=…` または `なし` を描画する。

## 変更ファイル

- `src/docich/hanjuku/flows/battle_menus.py`
  - `egg_battle_step` の announce 系救済（`elif` 追加）、
    `SUMMON_CARD_FLOW_STALL_LIMIT = 3` 新設、emit の `observed_metric` へ
    `card_flow` 追加。
- `src/docich/hanjuku/month_audit.py`
  - `_bounded_card_flow` 新設、`_track_card_miss` の `battle` 投影へ `card_flow`
    追加、`_card_miss_flow` 新設、`_render_card_miss` に `card_flow` 行追加。
- `tests/test_hanjuku_boss_egg_row_log.py`
  - `test_a_frozen_announce_flow_is_released_after_bounded_observations`:
    announce 凍結 flow が有界観測後に `cards_unclassified` へ移され解放され、
    残りの携行札（ノリウツール）が揭示スクロール→再オープンされること。
  - `test_the_missed_card_record_projects_the_blocking_card_flow`:
    emit の `observed_metric['card_flow']` に `{card, stage,
    selection_planned}` が載ること。
  - 既存 `test_selected_or_announcement_card_flow_is_not_reopened`
    （温存の固定）は上限未満の観測のため不変で通る想定。
- `tests/test_hanjuku_month_audit.py`
  - `test_card_miss_incident_projects_the_blocking_card_flow`（新規）:
    投影 `battle_event['card_flow']` と issue 本文への描画を確認。
  - `test_card_miss_projection_bounds_malformed_diagnostics`（拡張）:
    非 dict の `card_flow` が `None` に落ちることを確認。
- `src/docich/hanjuku/version.py`
  - `hanjuku-chart-v1514-card-flow-announce-rescue`。現在の main marker は
    `hanjuku-chart-v1513-duel-decline-funded` で、本 PR は次の v1514 を使用する。
- `ops/vm_actions/collect_diagnostics.py`
  - allowlist へ同文字列を追加（末尾カンマ）。
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - サンプル一覧へ同文字列を追加（末尾カンマ）。

## テスト結果

- **テスト未実行（環境制約）**: 本環境では `python3 -m pytest` / `py_compile` 等の
  実行が拒否される（VERIFY-5444/5476/5509/5538/5553 と同制約）。
  ゲートでの実行に委ねる。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読（AGENTS.md §1）。

## コミット状態（環境制約）

本環境では `git add` / `git commit` 等の書き込み系 git 操作が拒否される
（VERIFY-5538/5553 と同じ制約パターン）。上記7ファイルの変更と本ファイルは
ブランチ `fix/5538-card-flow-announce-rescue` の worktree 上に**未コミット**で
置いてある。別環境でのコミット手順:

```
git add src/docich/hanjuku/flows/battle_menus.py \
        src/docich/hanjuku/month_audit.py \
        src/docich/hanjuku/version.py \
        tests/test_hanjuku_boss_egg_row_log.py \
        tests/test_hanjuku_month_audit.py \
        ops/vm_actions/collect_diagnostics.py \
        ops/vm_actions/tests/test_collect_diagnostics.py \
        IMPLEMENT-5538.md
git commit -m "fix(hanjuku): 召喚画面で凍結した announce card_flow を有界観測後に解放する (v1514) (#5538) (#5553)"
```

`VERIFY-5553-REF.md`（参照用・未追跡）はコミットに含めないこと。
`rm` も環境拒否されたため worktree に残置している。

## 残件・既知の注意点

- 猶予中の `こうげき` 確定は依然 `battle_boss_card_missed` を起票しうる
  （`boss_card_missed_noted` は1戦闘1件）。修正後に本亜型で発火する issue は
  「無限凍結」ではなく「有界猶予内の攻撃」の意味になり、`card_flow` フィールドで
  判別できる。猶予の攻撃そのものを起票対象外にするかはオーナー判断の余地あり。
- 凍結 flow の発生元（戦術 flow / survival flow / レア戦術等）と札 X の発射有無は
  issue 単体では確定不能（VERIFY の (d) 要素どおり）。ラン成果物側の確認は継続。
- `summon_card_flow_stall` は `battle` 内の一時帳簿で、戦闘終了とともに消える。
- push・PR は未実施（検品ゲートで実施）。
