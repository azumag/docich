# INVESTIGATE-4547 — hanjuku ハブ奪還→陥落の往復

調査日: 2026-11-28
対象: azumag/docich#4547
調査範囲: read-only。コード修正・commit・push・PR 化なし。`gh` は実行せず、issue 本文は GitHub Web 表示で確認した。第11話の本番 decision log はローテーション済みの前提どおり参照していない。

## 結論

**(c) 既存修正でカバー済み — ただし「提案3点が main に実装済み」という意味であり、元事象の完全な再発防止が証明済みという意味ではない。**

根拠: issue 起票翌日の 2026-11-19 に PR #4672 / commit `02bd9e96b`（`fix(hanjuku): hub増援を近い城から優先し月次昇格する`, v1182）が main へマージされ、現行 `origin/main` (`e50f39a4a`) に提案 (1)(2)(3) の実装が残っている。現行コードとテストには明示的に `docich#4547` が記載されている。

一方で、元実測には「62-2 でハブ garrison 6」があり、今回の近距離優先・月次昇格は **garrison 1〜2 のときだけ**に限られる。また、主人公のハブ移動・失敗抑止・ログの2段ローテーションに残余の設計論点がある。したがって、issue は「既存修正で提案対応済み」として扱えるが、元事象が再発しないかは別途実測または follow-up の設計判断が必要である。

## 1. 提案 (1): 近い前線城から先に送る

### 現行コード

`src/docich/hanjuku/strategy/replan.py`

- `HUB_REINFORCE_MAX = 2`: `replan.py:359`
- hub gather の生成: `replan.py:1840-1870`
- 手薄判定と並べ替え: `replan.py:1871-1886`
  - `undermanned` は、ハブが自軍で、ハブ roster が list/tuple として記録され、`0 < len(set(hub_roster)) <= 2` の場合。
  - `undermanned` のとき: `gather.sort(key=lambda item: (_distance(chapter, item[0], hub), item[0]))`
  - それ以外: 従来どおり `(-len(spares), distance, source)` で、山の大きい城を先頭にする。
- gather の move 化: `replan.py:1917-1932`
- 回帰テスト: `tests/test_hanjuku_ch11_gateway.py:583-627`

### issue の前提との関係

issue の「`gather.sort` が `-len(spares)` 先頭で遠い本城の大部隊が先に動く」という前提は、**現行 main では無条件には成り立たない**。手薄なハブ（1〜2人）では距離優先へ切り替わっている。`-len(spares)` 先頭が残るのは、ハブが3人以上、未読、または0人として扱われる場合だけである。

奪還直後の roster については、攻撃側が勝った戦闘の終了処理で対象城を `captured` に入れ、いったん `garrison[castle] = []` にしてから勝った将軍を入れる実装がある（`src/docich/hanjuku/flows/battle.py:1929-1944`）。したがって、通常の戦闘勝利で奪還した場合は roster が少なくとも1人として記録され、`undermanned` の条件に乗りやすい。

### 影響範囲と副作用

- donor は `gate['front']` に属する自軍城だけで、`spares` は `_spares(free)` かつ主人公除外なので、少なくとも1人は donor に残る設計（`replan.py:1861-1870`）。
- 近い前線城から送ることで、到着時間は短くなる可能性が高い一方、前線城自体の防御層は薄くなる。現行実装は最低1人を残すが、戦力として十分かは別問題。
- hub 所有時の `stage_limit/per_source` は `HUB_GATHER_ALL_PER_PLAN = 8`（`replan.py:357-358`, `replan.py:1912-1916`）であり、提案文の「2〜4名」より多い。これは 2026-11-18 のオーナー指示「hub に全軍を集める」を優先した設計と読める。
- 距離評価は `_distance`（座標距離）であり、実際の行軍路・障害物・途中の自城吸収までは見ていない。第11話の front→hub で問題ないかは、コードだけでは最終確認できない。
- roster 未読の近接城は donor に入らないため、測定済みの遠い城が選ばれ得る。これは盲発射を避ける既存方針と整合する。

## 2. 提案 (2): garrison 1〜2 の間のハブ増援優先・月次1回

### 現行コード

`src/docich/hanjuku/strategy/replan.py`

- `_hub_reinforce_pending`: `replan.py:308-322`
- `_note_hub_reinforce` / `_hub_reinforce_available`: `replan.py:325-347`
- promotion 判定: `replan.py:1902-1909`
- `hub_first` を付けた move 生成: `replan.py:1917-1932`
- candidate 出力順での先頭昇格: `replan.py:2114-2133`
- 発行時にだけ月次枠を消費: `replan.py:2636-2643`, `replan.py:3199-3205`, `replan.py:3221-3228`, `replan.py:3243-3254`
- 回帰テスト: `tests/test_hanjuku_ch11_gateway.py:597-617`

実装は、ハブが手薄な場合に月1本だけ `hub_first` を付け、候補列の先頭へ昇格する。`interim_candidates` を評価しただけでは月次枠を消費せず、adopted/interim order または stored plan に載った時点で消費する設計である。

### 既存優先ルールとの関係

- 出力順は `due_orders` の後、`front`（昇格move）が `staffing`/boss waves/通常の retake/attack より先に来る（`replan.py:2132-2133`）。したがって「前線城の攻撃より優先」は候補順の意味では実装済み。
- ただし boss waves は owner 2026-11-21 の「ボス攻撃を最優先」に従い、候補列のかなり前に置かれる（`replan.py:1369-1374`, `replan.py:2132-2133`）。hub 増援は boss waves より前ではない。これは既存のボス優先設計との明示的な競合というより、ボス最優先を残した現在の優先度設計である。
- `sortie_launch_priority` は `attack/retake/boss/invade` だけを対象とし、`move` は対象外（`src/docich/hanjuku/strategy/orders.py:1066-1102`, 有効判定は `orders.py:1149-1195`）。したがって hub 増援 move は「発射確認まで月次家事を中断させる launch priority」には載らない。築城スキャンの defer もこの latch を見る（`src/docich/hanjuku/flows/month.py:1524-1546`）。
- つまり現行修正は **候補選択順では優先するが、実行中の発射確認優先ラッチでは attack/retake と同格ではない**。これは設計上の非対称であり、提案文が要求した範囲を超えるかどうかは判断が要る。

### 残余の注意点

1. **主人公ハブ移動との相互作用**  
   `_note_hero_hub_move` は `_note_hub_reinforce` も呼び、主人公のハブ移動を月次増援枠として扱う（`replan.py:1149-1167`）。一方、`promoted` は `stage_first` または `hub_first` のみで、`hero_hub_move` 自体は昇格対象ではない（`replan.py:2118-2127`）。主人公移動が存在すると通常の近距離 `hub_first` は作られず、かつ主人公移動は known retakes より後ろに並び得る。テスト fixture でも「hero がいると promoted slot を隠す」ため主人公を除外している（`tests/test_hanjuku_ch11_gateway.py:573-575`）。この仕様は意図的コメントがあるものの、「近い前線城から先に送る」という提案 (1) の保証を弱める可能性がある。

2. **失敗抑止と月次枠消費の整合は未検証**  
   PR #4672 のレビューでは、`_suppress_recent_failures` で `hub_first` step が failed へ戻されても `_note_hub_reinforce` が月次枠を消費し得る点が指摘されていた（関連コード: `replan.py:2650-2670`, `replan.py:3221-3228`, `replan.py:3243-3254`）。ただし、抑制状態はローカル step 名で書かれ、計画実行時は request id 付き execution step へ変換されるため、この指摘が実際の実行経路でどこまで成立するかは今回の静的調査では確定できなかった。少なくとも「発行候補が作られた時点で枠を消費する」設計なので、失敗・抑制された候補と枠消費の対応は実測または専用テストで確認する価値がある。`sortie_move_overdue_settled` は `_mark_order_failed` 経由で `failed_local_step` を直接は作らないが、launch/menu 失敗で `_finish_order(..., 'failed')` となったケースでは `failed_local_step` が残る（`src/docich/hanjuku/strategy/orders.py:3378-3411`）。

3. **元実測の garrison 6との閾値差**  
   issue本文の実測は 62-2 で hub garrison 6、62-5〜62-7 で 9→6 だった。現行の近距離優先と月次昇格は `<=2` に限定されるため、「6人いても落ちた」部分を直接はカバーしない。奪還直後の1〜2人の時間を短くする効果は期待できるが、garrison 6 での陥落が戦力不足・HP・敵構成によるものなら、閾値や防御強化の別設計が必要になる。

## 3. 提案 (3): sortie 行軍ログの回転対策

### 現行コード

- `brains/hanjuku/bot.py:48-52` で `SORTIE_EVENT_PREFIX='sortie_'` を定義。
- decision 永続化時に、`decision` が `sortie_` で始まる record を `hanjuku_events` にも `event='sortie_decision'` としてミラーする（`brains/hanjuku/bot.py:241-267`）。
- `sortie_outcome` については nested `observed_metric.outcome` を 32文字以内でトップレベル `outcome` にも保存する（`brains/hanjuku/bot.py:256-267`）。
- speedrun 側にも同等実装がある（`brains/hanjuku_speedrun/bot.py:50-53`, `brains/hanjuku_speedrun/bot.py:258-267`）。

### 既存のログ保持設計

- `src/docich/hanjuku_run.py:66` の `MAX_LOG_BYTES = 4*1024*1024`。
- `append_log` は 4 MiB で現行ファイルを `.previous.jsonl` へ1回だけ入れ替える（`src/docich/hanjuku_run.py:85-99`）。したがって原則として current+previous の最大約8 MiB。
-  docs も `hanjuku_events.previous.jsonl` を4 MiBごとのローテート先、現行と合わせ最大約8 MiBと説明している（`docs/hanjuku_script_bot.md:760-773`）。
- さらに completed generation については `src/docich/hanjuku_log_retention.py:1-34` が decision/event/input の current+previous を対象に、最新10世代を残して削除する。

### 評価

提案どおりの `sortie_*` → `hanjuku_events` ミラーは実装済みである。ただし、これは「decisions ログより events ログの方が長く残る」ことを保証するものではない。

- `hanjuku_events` には全観測の `event='observation'` も書かれる（`src/docich/hanjuku_run.py:478-489`）ため、events 側も高密度でローテーションする。
- ミラーによって events の容量消費はむしろ増える。
- したがって、**decision ローテーションで即座に証拠が消える問題は緩和するが、長期・確実な監査保持を保証するものではない**。
- 「ログ保持時間を延ばす」方を選ぶ場合の変更範囲は少なくとも:
  1. `hanjuku_run.MAX_LOG_BYTES` または log 別の保持ポリシー分離
  2. `hanjuku_log_retention.HANJUKU_LOG_RETENTION_GENERATIONS` / `LOG_FILES`
  3. diagnostics/reader が current+previous の2ファイル前提で読んでいる箇所（例: `soren_corner_stats.py:1173-1189`, `hanjuku_periodic.py`, `hanjuku_commentary.py`）
  4. speedrun fork 側の同種実装とテスト
  5. VM ディスク使用量・秘匿情報を含めないログ設計の確認

## 4. (a)/(b)/(c) の切り分け

- (a) 実欠陥として即修正すべき単一の不具合、とは今回は断定しない。hub 陥落自体はゲーム内の兵力・行軍時間・敵攻撃を含む戦略結果であり、現行コードには既に本 issue 向けの対応が入っている。
- (b) 設計判断が必要な点は残る。具体的には、閾値 `<=2` で十分か、garrison 6 の陥落をどう扱うか、主人公ハブ移動を近距離増援より優先するか、`move` を launch priority に載せるか、ログ保持をどこまで延ばすか。
- それでも issue本文の提案 (1)(2)(3) の具体的実装は PR #4672 で main に入っており、現行コードにも回帰テストがある。したがって分類は **(c) 既存修正でカバー済み** とする。ただし上記の残余論点は「カバー済みだが十分性未確認」として issue コメントに残すのが適切である。

## 未決の点

1. 実運用で奪還直後に主人公ハブ移動が先行した場合、近距離前線城からの `hub_first` が実際に先頭実行されるか。静的には、主人公移動が月次枠を消費し、かつ `hero_hub_move` 自体は `promoted` 対象外なので、retake が先に来るケースがあり得る。
2. garrison 6でも落ちた元実測に対し、`<=2` 条件で十分か。現行コードだけでは判断できない。閾値を enemy-facing threat / roster HP / inbound count を見る設計に変えるかは別判断。
3. `sortie_decision` ミラー後に、events ログが実運用でどれくらいの期間残るか。本番ログを見ない方針のため未検証。
4. 失敗抑止で月次枠だけ消費されるケースが、実運用でどの程度起きるか。コード上は可能だが頻度は不明。

## 調査上の制約

- 本番 decision/event log は指定どおり参照していない。
- 静的コード調査中心。テスト実行や本番再現は read-only 調査の範囲外として未実施。
- `handoff.md` の明示参照は確認できなかったため、本調査は GitHub issue と現行 `origin/main` のコードを根拠にした。

## 検品ゲート確認（Muse、2026-11-28 23:14 JST、現行 main e50f39a4a）

- 結論 (c) 既存修正でカバー済みを維持する。現行 main で直接確認した点：
  - PR #4672 / commit 02bd9e96b（`fix(hanjuku): hub増援を近い城から優先し月次昇格する (#4657) を最新mainへリベース (v1182)`）が main に入っている。変更ファイル：`src/docich/hanjuku/strategy/replan.py`（104行）、`brains/hanjuku/bot.py`、`tests/test_hanjuku_ch11_gateway.py`（64行）。
  - `replan.py` には `#4547` の明示的な参照が残っている：`HUB_REINFORCE_MAX = 2 # a hub holding this few is undermanned (#4495/#4547)`（L359）、`_note_hub_reinforce` の docstring「The month's promoted hub reinforcement is now planned (docich#4547)」（L326）。
  - 提案 (1) 近距離優先・(2) 月次昇格・(3) `sortie_*` の `hanjuku_events` ミラーは、現行コードで上記のとおり実装されている。
- 残余の設計論点（`<=2` 閾値と元実測の garrison 6 の陥落、主人公ハブ移動との相互作用、move の launch priority 非対称、ログ保持の実運用での持続期間、失敗抑止と月次枠消費の整合）は、(c) の分類を変えないが、別途の設計判断として issue コメントに残す。
- なお、旧台帳にあった「デプロイ→本番検証→owner の close 判断」は、コード側のカバーは確認済みだが、本番での再発有無の実測確認はこの調査の範囲外である。issue は owner の close 判断待ちとする。
