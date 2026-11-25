# VERIFY-5498 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch1 クランベリー

- 対象: azumag/docich issue #5498 / fingerprint `7641c039849071dd` / occurrence `68498958b8228d1348ad`
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1491-card-abandon-retreat-ban`
- 月: ch1 3-2 / step `A:0d4514ed:M33eb23` / 将軍 クランベリー / ジョンリギ → けっかい / purpose `attack`
- 検証範囲: read-only（本ファイル作成と verify ブランチへのコミットのみ。コード変更・push・PR・本番 SSH・ゲーム入力なし）
- 検証ベース: origin/main `5a7c20ab791031a24e8f01c25a95fb782206313f`（v1496）。観測版は v1491 `231ec8fbd3b3d969ceeede9566859e43b2a7bdf4`。v1491→origin/main で `strategy/kit.py`・`flows/deploy.py`・`strategy/execution.py`・`strategy/replan.py` に差分はなく、本件の携行・出撃確認経路の引用行番号は観測版でも同じ。

## 結論: (a) 実欠陥 — 計画空のボス波状攻撃で、安全札が無い在庫構成だと自動携行も空kit拒否も効かず、無携行のまま発射できる

検出は誤検知ではない。出撃確認で空kitが実測され、`want=[]` と `got=[]` が一致したため承認され、実際に `order_launched` へ進んだ。これは v1491 に存在する確定的な経路である。

ただし欠陥は「`_auto_carry_safe_card` が egg_drop>0 を選ばない」こと自体ではない。egg_drop>0 を無計画に自動携行しないのは PR #5336 / v1448 の明示仕様である。実欠陥は、**機械ボス波状攻撃が `cards=[]` を強制し、v1448 の安全札 fallback が効かない在庫構成では、在庫から実効札を計画する第2段も、空kit出撃を止める確認側の出口も無い**ことである。

本件の在庫は以下の通り全て egg_drop>0 で、v1448 の自動携行適格札（egg_drop 0）は1枚も無い。

| 札 | 在庫 | egg_drop | 備考 |
| --- | ---: | ---: | --- |
| イッテツーン | 2 | 8 | `general_damage` 10 / `boss_damage` 16 |
| ゼンマイン | 1 | 3 | `general_damage` 32 / `boss_damage` 50。`BOSS_DAMAGE_CARDS` ではある |
| ダイチスイム | 1 | 1 | `general_damage` 6 / `boss_damage` 8 |
| フットバース | 2 | 5 | `boss_damage` 2 |
| ブラッキー | 1 | 3 | `general_damage` 22 / `boss_damage` 20 |

ch1 のボス城は けっかい、ボスは クイーン（`hanjuku_chart.py:456`, `:1197-1198`）で、`BOSS_GENERALS` に属する。このため `_strong_card_names` はモンスター型ボス向け `BOSS_DAMAGE_CARDS` ではなく通常の `STRONG_CARDS`（クースカン/ミックミー/マグネガキン/ノリウツール）を使い、本件在庫にはその4種も無い。ゼンマインは `BOSS_DAMAGE_CARDS` に入るが、この経路では候補にならない。

## 1. fingerprint / incident_id の式照合

issue の raw 値は v1491 の `month_audit.py` の式と一致する。

- `_fingerprint`（`src/docich/hanjuku/month_audit.py:3112-3114`、v1491）は `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_build_sortie_cards_incident`（`:2493-2494`）は実測空kitのとき signals の先頭が `sortie_without_cards` なので、入力は次の文字列になる。
  - raw: `1|sortie_without_cards|sortie_without_cards`
  - sha256先頭16: `7641c039849071dd` — issue と一致
- `incident_id`（`:2488-2492`）は `runtime_id|generation|kind|step|general|int(created_at)` の sha256先頭20。
  - raw: `g1190-ch1v9|1190|sortie_without_cards|A:0d4514ed:M33eb23|クランベリー|1795559550`
  - sha256先頭20: `68498958b8228d1348ad` — issue と一致

fingerprint は章・kind・先頭signalだけで決まるため、同じ ch1 の `sortie_without_cards` occurrence は同じ fingerprint になる。fingerprint 一致だけでは原因機構の同一性を意味しない。

## 2. v1491 で実際に空kit出撃しうる機構

### 2.1 注文は機械ボス波状攻撃で、計画札が意図的に空

issue の `plan.chart_plan.source` は `mechanical`、step `A:0d4514ed:M33eb23` の note は「暫定: ガルバンゾーでボスへ波状攻撃」、target は ch1 ボス城 けっかいである。これは `strategy/replan.py` の機械ボス波状攻撃に一致する。

- `strategy/replan.py:1465-1471` — 通常の暫定 attack では owner 方針「切り札は持たせたほうが良い」に従い `_stock_cards` を計算するが、`chart.is_boss_castle(target)` では `cards=[]` に上書きする。コメントは「波: strict kit waits for nobody — carry what is there」。
- `strategy/replan.py:1509-1514` — 追加の boss wave order も `cards: []` で作られ、note は「暫定: …でボスへ波状攻撃」。

したがって `planned_cards=[]` はモデル漏れや採用層の喪失ではなく、機械ボス波状攻撃の入力値どおりである。一方でコメント上の意図は「carry what is there」であり、在庫があるのに結果的に何も持たせない現行結果は、この意図を完全には実装していない。

### 2.2 v1448 自動携行はあるが、本件在庫では候補ゼロ

PR #5336 / v1448（commit `b68f1fd20`）は v1491 に含まれる。実装は `strategy/kit.py` の通り。

- `kit.py:51-114` `_deploy_cards` — 計画・drop・spent・zero stock filter・ID cap を通した後、携行が空なら `_auto_carry_safe_card` を `_with_extra_card` 経由で1枚だけ足す。
- `kit.py:117-146` `_auto_carry_safe_card` — 在庫正数・未使用・未破棄でも、`reference.CARDS[name].egg_drop != 0` の札は明示的に除外する。
- `hanjuku_reference.py:31-38`, `:68-69` — 本件の5種は全て egg_drop 1以上。egg_drop 0 が無いため `_auto_carry_safe_card` は `None` を返す。

したがって PR #5336 は v1491 時点で有効だったが、**本件の在庫構成では適用候補がなく、本経路をカバーしていなかった**。issue の `cards_autocarried: 5` は120秒窓の総カウントであり、この step で安全札が選ばれた証拠ではない。少なくとも確定時点の在庫では適格札が無く、実測携行は空だった。

### 2.3 strong / dropper / rare の補助携行も本件では埋まらない

- `flows/deploy.py:494-546` `_dropper_card_inventory` は `_sortie_defender` が必要。`strategy/kit.py:172-180` は boss order では defender を判明扱いしないため、ボス城 けっかいへの本件では卵落札補助は発火しない。
- `flows/deploy.py:549-567` `_strong_card_names` は ch1 ボス クイーンが `BOSS_GENERALS` のため `STRONG_CARDS` を返す。在庫にクースカン/ミックミー/マグネガキン/ノリウツールは無い。
- バルムンク等の rare kit も在庫に無い。

よって補助携行が入らず、`wanted=[]` のまま `deploy.py:2695-2696` の「予定切り札の選択入力後に携行確認へ進む」でカード一覧を閉じる。

### 2.4 出撃確認は「計画空・実測空の一致」として承認する

PR #5380 / v1456（commit `97a6deba2`）も v1491 に含まれるが、guard の対象外である。

- `flows/deploy.py:912-923` `_empty_kit_blocks_departure` は `bool(want) and carried == []`。**計画が空で `want=[]` のときは False**で、PR #5380 本文も「計画が空のとき（型A）は v1448 の自動携行の対象で、本変更の対象外」と明記する。
- `deploy.py:2790-2798` — 空の携行表示が読めると `carried=[]`、`want=sorted(_deploy_cards(...))`。本件は `want=[]`。
- `deploy.py:2813-2865` — mismatch 系は `got != want` のときだけ。`got=[]`・`want=[]` は一致なので空kit取消にも mismatch 保留にも入らない。
- `deploy.py:2889-2913` — そのまま `confirm_departure_order` へ進み、`sortie_confirm` を「出撃確認で読み取った切り札が計画と一致したため承認を予定」として記録する。

issue の決定列はこの順序どおり、`hero_priority_selected` → `sortie_confirm` → `y_jump_open` → `y_jump_confirm` → `sortie_departure_requested` → `order_launched` である。計画将軍ガルバンゾーではなく実際の出撃将軍クランベリーが選ばれたのは `hero_priority_selected` の代役選択で、`confirm_departure_order`（`strategy/execution.py:72-91`）は実際の将軍・出撃元・実測kitを binding へ写す。カード問題とは独立に整合している。

## 3. #5457 / #5319 / #5317 との異同

### #5457（ch12 チキータ）との違い

- #5457 は planned cards があり、旧 payload では `carried=[]` が「実測空」か「未観測」か分からないことが問題だった。PR #5460 / v1484（commit `c73eeaece`）は監査側で `sortie_without_cards`（実測空・high）と `sortie_kit_unconfirmed`（未観測・low・auto-file false）を分けただけで、出撃挙動は変えていない。
- 本件は `sortie_confirm` の実測レシートがあり、`carried_observed=true` の実測空kit。したがって #5457 の監査修正で消えるものではない。v1491 には PR #5460 が入っており、本 issue が high confidence で起票されたのはその修正どおりの分類である。

### #5319（fingerprint `4dfd4ad0f6d10426`）との違いと修正カバー

- #5319 は ch11 で、計画されたクースカン/ミックミー/ノリウツールが実行層で失われた「計画あり」型。VERIFY-5319 は (a) 実欠陥と判定した。
- 本件は ch1 で、最初から機械ボス波状攻撃の `cards=[]`。計画札が drop/zero/binding で失われた形跡を前提にする必要がなく、#5319 の実行層喪失経路そのものではない。
- #5319 系の対応として入った PR #5380 / v1456 は「計画に札があり、読めた携行が空」だけを止める。本件は `want=[]` なので対象外。PR #5380 は本経路をカバーしない。
- VERIFY-5319 で残っていた `card_zero_at_start` や `card_drop` の再検証も、本件では計画札が最初から無いため発火しない。

### #5317（(b) 設計どおり）との違い

- #5317 は v1435 時点の明示的 cardless order で、「チャートがカードを計画しない出撃は空のまま実行される」のが当時の設計だった。
- 本件は v1491 で、owner 判断済みの v1448 自動携行が存在する。さらに #5317 の在庫バグストームは egg_drop 0 なので v1448 で自動携行されるが、本件の在庫は全て egg_drop>0 で自動携行されない。この差が両件を分ける。
- ただし本件は単なるモデル生成の cardless order ではなく、機械ボス波状攻撃である。`replan.py` のコメントは「carry what is there」で、通常暫定 attack では `_stock_cards` を使う設計がある。ボス波だけ `cards=[]` に固定し、安全札が無い場合の fallback を持たない点が、#5317 の単純な「明示的 cardless は設計どおり」とは異なる。

## 4. 決定内訳の整合性

issue の「出撃: 確定 2 / 発射 4 / order_start 4 / order_launched 0」と、`sortie_event.decision=order_launched` の組合せは矛盾ではない。

- `_build_sortie_cards_incident` は `_read_decisions`（`month_audit.py:2867-2902`）で直前120秒の JSONL を読み、`order_start` / `order_launched` を `counts` から表示する。
- `_decision_counts`（`:2962-2965`）は `Counter(...).most_common(30)` で**種類数を30に切る**。本 issue の embedded counts はちょうど30種類あり、`order_launched` は含まれていない一方、`decisions.before` の末尾には `order_launched` 自体が残っている。
- したがって表示上の `order_launched 0` は「無かった」ことの証拠ではなく、top-30 集計から漏れた表示限界である。監査判定はファイル集計ではなく現在の launch record から行われるため、検出自体は整合する。
- `sortie_confirm` の理由「計画と一致したため承認を予定」も、計画空・実測空なら一致する現行コードと一致する。`y_jump_confirm` 後の `sortie_departure_requested` → `order_launched` は通常の出撃経路である。

この top-30 表示限界は検証を遅らせる監査表示上の欠点だが、本件のゲーム挙動判定を変えるものではない。必要なら `order_start` / `order_launched` 等の重要キーは `most_common(30)` の対象外にして常時表示するとよい。

## 5. 修正の方向（実装は別タスク）

現行結果を放置せず、owner の「切り札は持たせたほうが良い」「資源は残して負けるより使い切る」に寄せるには、少なくとも次の2層が必要である。

1. **ボス波状攻撃にも在庫対応の実効kitを与える**（計画層または携行決定層）
   - `strategy/replan.py` で boss target だから `cards=[]` に固定するのではなく、`_stock_cards` の考え方を boss wave 用に分けた `_boss_wave_stock_cards` を設ける。
   - 選択は必ず実在庫正数から行い、固定チャート札を待たない。これなら元の設計意図「strict kit waits for nobody」と矛盾しない。
   - 候補順は egg_drop 0 を最優先としつつ、boss target では `BOSS_DAMAGE_CARDS`、クイーン等の `BOSS_GENERALS` では `general_damage` も評価する。少なくとも本件の ゼンマイン（general 32 / boss 50）は無条件除外ではなく候補に入るべきである。ID合計48未満・携行枠・`kit_spent`/`card_drop`・boss reserved は既存制約を維持する。
   - egg_drop>0 の自動利用を owner が許可しない場合は、その札を選ばない代わりに出撃自体を止める必要がある（下記2）。「使わないが空で出る」は資源方針にも「carry what is there」にも合わない。
2. **計画空でも在庫正数の attack/boss 出撃を、実測空kitのまま silent approve しない**（確認層の保険）
   - `deploy.py:912` の `_empty_kit_blocks_departure` は現在 `bool(want)` 前提。型Aで fallback kit を作れなかった場合は、`want=[]` かつ `got=[]` でも出撃を取消し、次回の在庫再評価へ回す guard を追加する。
   - 第9話の corridor 等、owner が明示的に「切り札を選ばない」とした経路だけは例外を維持する。

監査側の本体修正は不要である。実測空kit・在庫正数の発射を high confidence で拾うのは仕様どおり。ただし issue 本文に「egg_drop 0 の適格在庫が無かった」「strong/dropper/rare の補助対象外」を出すと、次回以降の検証は速い。

## 手順上の注記

- 本検証は issue 本文の embedded JSON と v1491 / origin/main の静的コード照合に基づく。本番 VM へは SSH せず、ランの生ログ・frames は取得していない。
- ただし本件では embedded JSON に `sortie_confirm` と `order_launched` を含む直前決定列、plan、在庫、実測空kitフラグが揃っており、上記の発火経路はコード上で一意に説明できる。生ログなしの未確定要素は、120秒窓のどの `cards_autocarried` がどの step に属したかだけであり、結論を変えない。
- `agent-guards/secret_scan.py` はこの環境に未配置。本ファイルに秘密情報は記載していない。
