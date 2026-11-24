# IMPLEMENT-5127-followup — [半熟英雄] 未確認終了の帰還が ch10 で名簿読みへ繋がらない

ブランチ: `fix/5127-roster-due-consume`（push・PR・本番 SSH はなし）
BOT_VERSION: `hanjuku-chart-v1393-roster-due-consume`
（main は v1389（#5133 マージ済み）。DSH の最終割当で v1390=#5128、v1391=#5137、v1392=#5138 のため、
v1393 を採用。gh の PR 一覧は実行環境で取得できず、リポジトリ内の v1389
参照が無いことのみ確認）

## 前提（issue #5127 / PR #5129 の現状確認）

- `gh` が実行環境から使えなかったため、issue/PR の外部参照は不可。代わりに
  マージ済みコミット `654c4024cfb296322895353a4e5399fdd3739283`（PR #5129、
  v1387）の差分と現行コードで登録・消費経路を確認した。
- 登録（PR #5129、`flows/recall.py`）: `arrival_unconfirmed` で入力送信済み・
  `goal` が文字列のとき
  `roster_read_due[goal] = tick + RECALL_READ_DELAY` を予約（章を問わない）。
- 既存の消費は 2 経路のみ:
  - `strategy/corridor.py::_due_probe` — 章 9 の回廊ルート表 `routes_by` に
    ある城だけ probe を mint（mint 時点で due を pop）。
  - `replan._boss_wave_pick` / ボス波生成 — 同一 source の blind/kept
    `launch_probe` がある場合だけ読み取り専用 probe を mint。
- `flows/deploy.py` 側の読み取り実行経路は完成済み（`roster_probe` 注文が
  `general_list` で `_observe_garrison` を呼び、結論が出た読み取りだけ due
  を pop。不完全な読み取りは due を保持して再試行可能）。
- よって ch10（再現章）では due が登録されても誰も起票せず、TTL 後に消えて
  帰還将軍の所在が未確認のまま——issue #5127 最新レビュー指摘どおり。

## 変更内容

### 1. 章共通の due 消費ヘルパー（`strategy/routes.py`）

- `ROSTER_READ_DUE_TTL = 900` を `routes` へ移動（corridor 側は同名エイリアス
  を残して外部参照を維持）。
- `roster_read_due(mem)` を新設: `mem['roster_read_due']` のうち
  (a) due 時刻が未来のものは除外、(b) due から TTL 超過・または tick 値が
  int でない stale エントリは pop（どの章でも枯渇掃除する——ch9 限定だった
  枯渇処理を章共通へ拡張）、(c) 残りを `{castle: due_tick}` で返す。

### 2. corridor の共有化（`strategy/corridor.py::_due_probe`）

- TTL 判定と due 抽出を `routes.roster_read_due` に委譲。挙動は不変
  （`routes_by` にある城だけ probe 対象、mint で due を pop——ch9 契約維持）。

### 3. 章共通の消費経路（`replan.interim_candidates`）

- 全候補を組み立てる直前、`routes.roster_read_due(mem)` の due 城（所有城のみ）
  から 1 つを選び、読み取り専用 probe を先頭候補として mint:
  - `general = MOVE_ANY_GENERAL`（名指ししない）、`source_probe = True`、
    `roster_probe = True`、`purpose = 'move'`、`target = ボス城`
    （ボス城宛は `order_allowed` が全章で受理・`is_road` ゲート対象外。
    実際には出撃せず、一覧読みで終了する）。
  - `route_probe_read_tick` が最古の城を優先（複数 due の fair ローテーション）。
  - mint では due を pop しない——pop は実一覧の結論付き読み取り
    （`deploy._observe_garrison`）だけが行う。計画再構築は due が残る限り
    同じ probe を出し続ける。
  - 同一 source の既存注文（出撃・移動・probe 問わず）があれば二重起票しない
    ——その注文が一覧を開いた時点で due は消費される。
- `_mechanical_interim` の `stable_step`: `roster_probe` 注文の identity に
  `_mechanical_step` と同じ stamp（`unread_dispatches` 最大 tick、なければ
  `garrison_tick`）を連結。1 回の読み取りで step が 'done' になっても、
  不完全読み取り後の再試行・次の移送に対する新規 due が同一 step id の
  完了状態を継承して再発行されないことを防ぐ。

### 4. 既存インフラとの整合（変更不要を確認済み）

- `chart_adjust.startable`: `source_probe` + `PROBE_GENERAL`（'—'）を受理。
- `chart_adjust.validate`: `roster_probe` は `source_probe` 必須——両フラグで満たす。
- `orders._ready`: `source_probe`+`PROBE_GENERAL` は source/target が実在すれば可。
- `orders._source_list_open`: `roster_probe` かつ `source_probe` → 記録上
  空の城でも一覧を開く（due 城が空記録でも読みに行ける）。
- `orders._source_spare_missing`: `roster_probe` は即 False（1 将軍城でも開ける）。
- `deploy`: `general_list` で `roster_probe` 分岐が先に来て、`_observe_garrison`
  で実一覧を記録し結論付きなら due を pop、読み取り専用で 'done' 終了
  （`roster_probe_complete` / `roster_probe_incomplete` 記録）。
- `interim_candidates` の 1 将軍城ガード: `roster_probe` は対象外——最後の
  守備を出させないルールを侵さない（probe は読み取りのみ）。

### 5. バージョン・allowlist

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1393-roster-due-consume`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同文字列を追加
  （`test_collect_diagnostics` は `BOT_VERSION` を直接参照するため
  テスト側の固定値リストは変更不要）。

## テスト

新規 `tests/test_hanjuku_roster_due_consume.py`（7 件、ch10 を主対象）:

- `test_a_due_castle_mints_a_read_only_roster_probe` — ch10 全通常城保有・
  ボス待ちの局面で ファルゲ の due から `roster_probe`/`source_probe`/
  `MOVE_ANY_GENERAL` の読み取り専用注文が起票され、mint では due が
  消えない（pop は結論付き読み取りのみ）。
- `test_the_due_probe_leads_the_plan` — 到着確認がボス波に埋もれず先頭に来る。
- `test_the_probe_is_startable_from_the_recorded_garrison` — 実測 1 将軍城でも
  `startable` が受理（読み取りのみなので最後の守備を出させない）。
- `test_a_future_due_waits_for_the_walk` — due 時刻前は起票しない、
  時刻到来で起票される。
- `test_a_stale_due_is_dropped_not_probed` — TTL 超過の stale は pop され
  probe 化しない。
- `test_an_unowned_due_castle_is_not_probed` — 非所有城の due は起票しない。
- `test_the_due_probe_is_chapter_agnostic` — ch6 でも同じ probe が mint される
  （chapter 9 回廊への非依存を確認）。

## 検証結果

| 対象 | 結果 |
|---|---|
| `tests/test_hanjuku_roster_due_consume.py`（新規 7 件） | 未実行 — 実行環境で python3/pytest の起動がすべて拒否された |
| 関連既存テスト（corridor probe budget / boss probe source / recall destination 等） | 同上・未実行 |

本環境では `python3 -m pytest tests/test_hanjuku_roster_due_consume.py -x -q`
を含む python 系コマンドが承認されず実行不能だった。実装者の手元で
以下を実行して確認する:

```sh
python3 -m pytest tests/test_hanjuku_roster_due_consume.py \
    tests/test_hanjuku_corridor_probe_budget.py \
    tests/test_hanjuku_boss_probe_source.py \
    tests/test_hanjuku_recall_destination.py \
    tests/test_hanjuku_recall_month_scene.py \
    tests/test_hanjuku_unconfirmed_overdue_4965.py \
    tests/test_hanjuku_single_defender_planning.py \
    tests/test_hanjuku_off_chart.py -x -q
```

フルスイートは CI に委譲（ローカル方針どおり実行しない）。

## 制約の内訳

- due の pop 責任は ch9 と異なる（ch9 は mint 時に pop、本経路は結論付き
  読み取り時に pop）。`test_hanjuku_corridor_probe_budget.py` の既存契約は
  共有化のみで変更していない。
- 1 計画につき due probe は最大 1 件。先頭候補だが、採用されなければ
  通常の移動・攻撃候補はそのまま続くので、通常出撃を恒久的に阻害しない。
- 非所有城・未来 due・stale due は起票しない（stale は掃除のみ）。
- push・PR・本番 SSH は実施していない。
