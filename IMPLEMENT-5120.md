# IMPLEMENT-5120 — [半熟英雄] 月内ボス出撃ゼロ監査: 月末開始フェーズの誤発火ガード

ブランチ: `fix/5120-boss-phase-guard`（2026-11-26 に `origin/main` `bbe3144d3` へ rebase）
BOT_VERSION: `hanjuku-chart-v1551-boss-month-phase-guard`
（rebase 時点の main は v1550。open PR 一覧に v1551 の使用はなく、
次の未使用番号として v1551 を採用。）

## 背景（検証結論）

issue #5120 の opencode read-only 検証: ボスフェーズが月終端の約 6.9 秒前・
月内 43 観測だけの状態で月が閉じ、`boss_month_zero_sortie` が発行された
可能性が高い。出撃ディスパッチの猶予（`ORDER_DISPATCH_STARVE_TICKS` 相当、
約 90 秒）よりはるかに短い窓では pending 注文が `order_start` にすら
届かないため、「ボス出撃ゼロ」は停滞の証拠にならない。
直すべきはボス出撃ロジックではなく監査 detector 側のガード。

## 変更内容

### 1. 月クローズ時のフェーズカバレッジガード

**`src/docich/hanjuku/month_audit.py`**

- `MIN_BOSS_PHASE_SECONDS = 90` / `MIN_BOSS_PHASE_OBSERVATIONS = 20` を追加。
- `_boss_month_coverage(state, boss_phase, observed, close_at)` — 閉じた月の中で
  ボスフェーズがどれだけ覆っていたかを計算する。
  - `_close_month` は境界観測の `_track_boss` より先に走るため、
    `state['boss']` は月内最終観測時点のトラッカー。
  - 章・残りボス城が一致しないトラッカーは別エピソードとして捨てる。
  - フェーズが月の途中で始まった場合: 月内秒数 = `close_at − started_at`、
    月内観測数 = トラッカーの `observations`（フェーズ内観測のみ数える）。
  - 前月から継続するフェーズは月全体を覆う: 月内秒数 = `close_at − first_at`、
    観測数は月の全観測数。
  - 月内トラッカーが無い月（境界で初観測）はカバレッジを証明できないため
    `in_month_s` / `in_month_observations` は `None` → 不足扱い。
- `_close_month`: `boss_month` にカバレッジ 3 フィールドを載せ、
  `boss_departures == 0` かつ `phase_sufficient` が偽の月は理由
  `short_boss_phase` を付けて confidence を `low` にする。
  結果として kind は `boss_month_zero_sortie` ではなく
  `boss_month_zero_unverified` となり、`auto_file` は偽、
  `file` の `min_confidence='high'` フィルタでも除外される。
  ボス出撃が 1 件でもあった月・他の分類（ok / unconfirmed_launches /
  zero_unverified）はガードの対象外。

### 2. カバレッジ方式を採った理由（opencode 仕様との差異）

opencode の例示は観測数だけの下限 `BOSS_MONTH_MIN_OBSERVATIONS=60` だったが、
時間＋観測数のカバレッジ（90 秒 AND 20 観測）に統一した。

- 断定に必要なのは「bot に出撃の機会があったか」であり、それは時間で
  決まる。pending 注文が `order_start` へ届く猶予は
  `ORDER_DISPATCH_STARVE_TICKS` ≒ 90 秒。
- 観測数は観測周期に依存する。本件の実測（6.9 秒に 43 観測）のように
  密観測では数秒のフェーズでもカウントだけは十分に見える。観測数だけの
  下限ではこの形を取りこぼす／環境差で誤判定する。
- 逆に時間だけでは疎観測（例: 90 秒に 3 観測）を通してしまうので、
  既存の月監査と同じ `MIN_OBSERVATIONS=20` の証拠密度も要求する。
  両方を満たす場合だけ「フェーズが十分」とみなす。

### 3. #5077/#5082 系シグネチャへの非影響

ガードは `_close_month` の分類だけに効く。`boss_phase_no_battle`（600 秒の
フェーズ継続で発行）は `_track_boss` の別経路であり、トラッカーは
格下げされた月をまたいで継続する。翌月以降にフェーズが続けば
従来どおり発行される（回帰テストあり）。

### 4. 表示

- `render` の `### ボスフェーズ月次` に `月内フェーズ: <秒>秒 / 観測 <n>回`
  を追加。不足時は `（出撃猶予不足・docich#5120）` を付ける。
  旧 record（フィールド無し）は `None` 表示になるだけで壊れない。

### 5. バージョン・allowlist

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1551-boss-month-phase-guard`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同文字列を追加
- `ops/vm_actions/tests/test_collect_diagnostics.py`: fixed-values リストに同文字列を追加

### 6. ドキュメント

- `docs/operations/hanjuku-month-audit.md`: 「ボスフェーズで月内ボス出撃ゼロ」
  節にガードの説明（しきい値・計算方法・対象外シグネチャ・record/render の
  カバレッジフィールド）を追記。

## テスト

`tests/test_hanjuku_month_audit.py` に追加/更新:

- `test_boss_phase_beginning_at_the_month_end_is_unverified` — (a) 月末 ~16 秒・
  30 観測だけのフェーズでは `boss_month_zero_unverified`（`short_boss_phase`、
  `auto_file` 偽）になり zero_sortie を発行しない。観測数下限は満たしても
  時間下限で止まる本件の形。
- `test_boss_phase_first_seen_at_the_boundary_is_unverified` — 月内トラッカー
  無し（境界で初観測）はカバレッジ `None` で同じく unverified。
- `test_boss_phase_month_with_zero_boss_sorties_is_recorded` — (b)(d) 前月から
  継続するフェーズは月全体のカバレッジを持ち、`phase_sufficient` が真で
  従来どおり `boss_month_zero_sortie` high / `auto_file` を発行（assert 追加）。
- `test_the_month_guard_does_not_suppress_boss_no_battle` — (c) 格下げ月の後も
  フェーズトラッカーが継続し、600 秒到達で `boss_phase_no_battle` が発行される。

## 検証結果

- 2026-11-26 の rebase 後に `PYTHONPATH=src:.:$HOME/py311shim python3 -m pytest -q
  tests/test_hanjuku_month_audit.py
  ops/vm_actions/tests/test_collect_diagnostics.py::test_hanjuku_hotload_versions_are_fixed_values_not_an_arbitrary_prefix`
  を実行し、193 passed。
- `tests/test_hanjuku_month_audit.py` と `ops/vm_actions/tests/test_collect_diagnostics.py`
  を併走した広い確認では 417 passed・2 failed。2件（rate limit recent_events、
  manual receipt updated_after_selection）は変更前の `origin/main` でも同じ失敗を
  再現した既存事象で、本変更の退行ではない。full-suite は CI 任せ。

## 制約の内訳

- ボス出撃ロジック・ゲーム入力・`_track_boss` の発行条件は一切変更していない。
- ガードは記録を消すのではなく `boss_month_zero_unverified` へ格下げするため、
  証拠自体は `boss_month.in_month_s` / `in_month_observations` とともに残る。
- 本番 VM への SSH は実施していない。
