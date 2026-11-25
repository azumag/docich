# VERIFY-5606 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ミックミー (ch4 四季王城(北東))

- 対象: docich#5606（`bug` / `auto-generated` の監査自動起票）/ 監査 `card_miss`（confidence `high`）
- fingerprint `b92107fb55d221d0` / occurrence `6dcdf24ac57002cdff7e`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1513-duel-decline-funded`
- ch4 / 月 4-4 / 未使用の携行札: **ミックミー**（携行札候補: ミックミー）
- 戦闘: タピオカ vs ウイナッツォ / 城: 四季王城(北東) / side `attack` / 時刻 13:44:36Z
- 画面 `egg_battle_menu` / scrolls `0` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 理由: 敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ
- 検証ベース: `origin/main` = `818700b19`（`BOT_VERSION` `hanjuku-chart-v1518-card-purchase-priority`）。観測版は commit `17af7ecba`（同 v1513）。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 本文・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 既存修正でカバー済み — scrolls=0 は「切り札一覧を開いた後に announce/selection 済み `card_flow` が召喚画面で凍結した」#5538/#5553 亜型。ただし修正 PR #5607 は未マージ・CONFLICTING で、origin/main にはまだ入っていない**

本件は監査誤検知ではない。v1513 では、召喚画面で `きりふだ` を開いた事実を示す `summon_card_scroll=0` が残っているのに、最後は `こうげき` が確定され、残りの携行札ミックミーが使われなかった。`scrolls=0`・非含有の表示文言・emit 条件を v1513 のコードへ当てはめると、揭示スクロールと再オープンの両ゲートを抑止する真の `card_flow` が残っていたこと、しかもそれは menu/down/list の単純中断ではなく **announce または `selection_planned` を持つ選択済み・消費未確定 flow** だったことが論理強制される（§3）。

この機構は VERIFY-5538 が特定した announce `card_flow` 凍結亜型と同じで、既存修正 commit `8ef923c11` / open PR #5607 が対象にしている。PR #5607 は凍結 flow を有界3観測で未分類へ移して解放し、残りの `cards_left` に揭示スクロールと再オープンを再開させる。したがって本件は新規の未知欠陥ではなく、**v1513 に残っていた既知欠陥の別観測**と判定する。重複実装の fix PR は作成しない。

ただし現行 `origin/main`（v1518）にも #5607 はまだ入っておらず、PR #5607 は `CONFLICTING` である。したがって運用上の再発防止には、別実装ではなく **PR #5607 の rebase/解消とマージ** が必要である。本検証での (b) は「修正済みコードが main に反映済み」ではなく、「既存修正車両が同機構をカバーする」意味である。

## 1. fingerprint / occurrence の sha256 再計算

現行 `month_audit.py` で式を確認し、本環境で実際に sha256 を再計算した。

| 項目 | コード | 入力 | 再計算結果 | issue |
|---|---|---|---|---|
| fingerprint | `_fingerprint`（`month_audit.py:3324-3326`）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 3528-3529）の signals は `[card, castle]` | `4|card_miss|ミックミー+四季王城(北東)` | `b92107fb55d221d0` | `b92107fb55d221d0` |
| occurrence | `_track_card_miss`（`month_audit.py:3477-3480`）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 2026-11-25 13:44:36Z は epoch `1795614276` | `g1190-ch1v9|4|1795614276|ミックミー` | `6dcdf24ac57002cdff7e` | `6dcdf24ac57002cdff7e` |

両方とも完全一致し、issue データは現行コードの監査形式と一致する。時刻・章・札・城・ランのどれかが違えば一致しないため、本件の fingerprint / occurrence は正しい。

## 2. 発火機構（何が「切り札を開くべきだった」と判定したか）

- emit サイトは `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:3342-3375`）の1箇所のみ。
- 起票条件（同 3342-3353）: `move == 'here' and label == 'こうげき'`、`missed = _carried_card_left(mem, battle)` が非空、`cards_left` 非空、`not battle.get('cards_used')`、かつ `not battle.get('boss_card_missed_noted')`。
- `_carried_card_left`（同 1268-1283）は、planned/確定携行から `{cards_selected, cards_used, cards_unclassified, cards_missing}` を除いた残りを返す。ミックミーが emit されたことは、少なくとも監査時点で同札が「選択済み・使用済み・未分類・欠品」のいずれの帳簿にも入っていなかったことを意味する。
- `_summon_card_row_hidden`（同 2835-2864）の owner ルールは、敵召喚獣戦・ボス戦で卵が使えず携行札があれば `きりふだ` を開くこと。emit はその違反の実事象を示す。
- `empty_egg=false` / `summon_egg_unavailable=false` は attack 側では fail-closed な証拠状態として矛盾しない。`enemy_summon_observed=true` は敵召喚画面系であることの補強証拠である。

## 3. scrolls=0 による亜型の確定 — 開札後の `card_flow` 凍結

`scrolls=0` は本件の決定的な手掛かりである。`scrolls: 不明` ではなく整数 0 なので、`metric.get('scrolls')` には実際に int 0 が入っていた（`month_audit.py:302-303` の `_bounded_int`、描画は同 4131-4133）。

v1513 `battle_menus.py` で `summon_card_scroll` を書く場所は2系統だけである。

1. 揭示スクロール実行時に `+1` する（現行 main 3194-3203、v1513 と同じ）。
2. `きりふだ` 行を開いた時に `0` へリセットする（現行 main 3212-3228、特に 3225、v1513 と同じ）。

したがって `scrolls=0` は、**この戦闘で少なくとも一度 `battle_summon_card_open` に到達し、切り札一覧を開いた**ことを示す。scroll 不発型（`不明`）でも、scroll 枯渇型（`3`）でもない。

次に、emit 時の表示文言は `こうげきもうこうげき` で `きりふだ` を含まない。`cards_left` はミックミー非空である。もし `battle['card_flow']` が偽なら、`_summon_card_row_hidden` は次の経路で真になる。

- `cards_left` 非空、`card_flow` なし、`きりふだ` 非含有を前提に先頭ゲートを通過し、
- `screen.kind == 'egg_battle_menu'` かつ `こうげき` / `もうこうげき` を含むため最終行で True（同 2857-2864）。

True なら attack へ進む前に有界スクロールへ分岐し、`return [pad('down')]` する（同 3192-3203）。実際には `こうげき` が選択され emit されたので、**`battle['card_flow']` は真であった**と論理強制される。

その flow の種類も絞れる。attack 分岐先頭の interrupted-flow 救済（同 3151-3166）は、`stage in ('menu','down','list')` かつ `selection_planned` と `read_kit` を持たない flow を `summon_card_resume` へ退避してクリアする。クリアされた flow はゲートを抑止しない。`read_kit` は防衛戦の実携行読み取り専用で、作成側 `_defense_kit_read_open`（`flows/battle.py:1581-1611`）は defense 窓に限定される。本件は side `attack` なので該当しない。したがって残っていたのは、**`stage == 'announce'` または `selection_planned=True` を持つ、選択済み・消費未確定の flow**である。

開かれた/選択された札 X はミックミーではない。もし X=ミックミーなら `cards_selected` に入り、`_carried_card_left` の attempted セットでミックミーが除外されて emit 自体が成立しない。したがって本件では、**別札 X が開札・選択されたか少なくとも開札 flow に入り、その消費が確定しないまま召喚画面へ置き換わり、真の flow が残りのミックミーの揭示・再オープンを抑止した**と読める。X の札名と発射有無は issue 本文だけでは分からない（発火時の emit に `card_flow` 投影がなかったため）。ただし root cause の機構分類にはその情報は不要で、scrolls=0 が no-open 型と明確に分ける。

## 4. 比較: #5600 / #5538 / #5528・#5541

| 記録 | 証拠形 | 機構 | 本件との関係 |
|---|---|---|---|
| #5600 ch2 バグストーム v1513 | scrolls `3` | 有界スクロール3回を使い切っても `きりふだ` 行が読めず、一度も開札できない scroll 枯渇型。open PR #5615 の `_summon_card_rearm`（branch 上 `battle_menus.py:2867-2895`、8観測間隔・最大2回）が対象 | **別亜型**。本件は scrolls `0` で、少なくとも1度開札済み。rearm は `card_flow` 真値によるゲート抑止を解除しない |
| #5538 ch8 ノリウツール v1497 | scrolls `不明` | 開札・スクロール前から announce/`selection_planned` flow が凍結し、揭示と再オープンを抑止 | **同 root cause**。本件は開札済みでカウンタが 0 へリセットされた後ろ姿であり、#5538 の発展形・別観測と読める |
| #5553 ch6 ピッグローラー v1500 | scrolls `不明` | #5538 と同じ announce flow 凍結亜型として open PR #5607 / commit `8ef923c11` の対象 | 同じ修正車両が本件をカバーする |
| #5528 ch6 ピオーネ / #5541 ch8 カシス | `sortie_without_cards`、plan `cards=[]`・携行なし | boss wave / concrete boss sortie が在庫札を持たず空 kit で発射する upstream の非携行欠陥。#5541 は PR #5544 / v1500 でカバー済み | **別層**。本件は携行札候補ミックミーが確定しており、出撃時に札がなかった話ではなく、戦闘中に開けなかった話 |
| 参考: #5603 ch4 バグストーム v1513 | scrolls `不明` | no-open 型の外見 | 本検証の主対象ではないが、#5538 系の外見。scrolls=0 の本件とは証拠形が異なる |

## 5. 既存修正 #5607 / commit `8ef923c11` のカバー内容

open PR #5607（branch `fix/5538-card-flow-announce-rescue`、head `d9e30080c`）には commit `8ef923c11` が含まれる。差分の要点:

- `egg_battle_step` の interrupted-flow 救済へ announce/selection 系を追加（branch 上 `battle_menus.py:3167-3190`）。同じ `scope` の flow が `SUMMON_CARD_FLOW_STALL_LIMIT = 3` 観測（同 3482）を超えて召喚画面に滞留したら、`_card_use_unclassified` 相当で未分類帳簿へ移し、`card_flow` を解放する。
- 解放後は `cards_left` に残る札について通常どおり `_summon_card_row_hidden` の揭示スクロールと `きりふだ` 再オープンが動く。回帰テストでは、選択済みミックミーが未分類へ移り、残りノリウツールの scroll/open に進むことを確認している。
- emit の `observed_metric` へ `card_flow` の `{card, stage, selection_planned}` を追加し（branch 上 `battle_menus.py:3402` 付近、`month_audit.py:313` / 3508 / 4084 / 4156）、今後はこの亜型を issue 単体で識別できるようにする。

この修正は scrolls=0 と scrolls=不明の区別に依存せず、共通の阻害条件である「announce/selection 済み flow が egg_battle_menu で進行不能のまま真」に直接作用する。したがって本件の root cause をカバーする。

一方で反映状態は未完了である。

- `8ef923c11` は `origin/main` の祖先ではない（`git merge-base --is-ancestor` = 非0）。
- PR #5607 は OPEN・`CONFLICTING`。
- `git diff 17af7ecba..origin/main -- battle_menus.py` の差分は召喚獣メニュー選択回数上限（#5608）だけで、`_summon_card_row_hidden` / interrupted-flow / emit / 再オープンゲートには差分がない。

よって **main 上では再発しうる**が、それは未知・未対処の新欠陥ではなく、既存修正 PR の未マージ状態である。重複実装ではなく #5607 の競合解消が正規の後続作業と判断した。

## 6. 判定詳細

- **(a) 実欠陥か**: v1513 時点では実欠陥だった。ミックミーを携行し、`cards_used` なしで `こうげき` を確定した実事象であり、誤検知ではない。しかし root cause は既存修正車両 #5607 が対象にする既知機構なので、本検証の最終分類は新規実装を要する (a) ではなく (b) とした。
- **(b) 既存修正でカバー済み**: announce/selection 済み flow の有界解放が #5607 / `8ef923c11` に存在し、scrolls=0 の本件機構を直接カバーする。ただし未マージ・CONFLICTING。
- **(c) 設計どおりではない**: owner ルールは敵召喚獣戦で卵が使えず携行札があれば `きりふだ` を開くこと。ミックミー未開封の `こうげき` 確定は設計意図に反する。
- **(d) 要素あり（結論は変わらない）**: 別札 X の札名、X が実際に発射したか、なぜ announce 確認へ進まなかったか（敵召喚会話の正確な挿入タイミング）は、発火時 emit が `card_flow` を投影していないため issue 単体では確定できない。確定には `g1190-ch1v9` の `hanjuku_decisions.jsonl` で当該戦闘前後の `battle_summon_card_open` / `battle_card_selected` / `battle_card_unclassified` / `battle_boss_card_missed` を時系列で読む必要がある。ただし scrolls=0 による open 済み証拠と、真 flow の論理強制は成立するため、機構分類は undetermined ではない。

## 検証メモ

- 実行した検証: issue 本文の照合、fingerprint / occurrence の Python sha256 再計算、`git show` / `git diff` による観測版 `17af7ecba`（v1513）・現行 main `818700b19`（v1518）・修正 branch `origin/fix/5538-card-flow-announce-rescue` の静的照合、#5600/#5538/#5553/#5528/#5541 の比較、`summon_card_scroll` 全代入箇所の確認、PR #5607/#5615 の状態確認。
- ドキュメントのみの追加のためテストは実行しない。
- 依頼どおり verify-only PR は作成しない。結論は issue コメントへ記録し、(b) のため新規 fix PR も作成しない。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
