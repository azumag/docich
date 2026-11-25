# VERIFY-5470 — [半熟英雄] 卵を残して敗北: ch1 ソーピニヨン

- 対象: [docich#5470](https://github.com/azumag/docich/issues/5470) / 監査 `battle_loss_with_resources`（confidence `high`）
- fingerprint `2114e84c7599b48c` / occurrence `13c7d9b1b4912597e30b`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1485-locked-boss-activation`
- ch1 / 月 2-8 / 戦闘: ソーピニヨン vs キャンディー、城カストーラ、side `attack`、loss、HP ally 0 / enemy 26、卵残数 4、切り札 選択/使用/残りすべて空、退却選択 False
- 検証ベース: `origin/main` = `cb1e21dc86bf5ea5c00dfc19e10587c4cbb07817`（`BOT_VERSION` `hanjuku-chart-v1492-boss-only-card-miss-eligibility`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。VM 側の `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は直接参照せず、issue 本文に埋め込まれた監査 incident JSON・GitHub の関連 issue/VERIFY・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 設計どおり — 卵は当該戦闘で召喚へ実使用済み。残数 4 は消費を差し引かない stale な帳簿値で、卵行が使えない状態へ戻った後の白兵・退却なしも現行方針どおり**

#5246 / #5300 と同じクラスである。issue の「卵残数 4」だけを見ると卵を残した敗北に見えるが、当該戦闘の連続した決定列には味方召喚獣ターンの証拠がある。少なくとも 1 回は卵を召喚へ使っており、「使える卵を最初から温存したまま敗北した」事象ではない。

監査側は `battle_result` の生の `egg_uses` 台帳値を読み、卵だけの資源には降格条件を設けず high で保存する。したがって本起票は監査契約どおりの出力ではあるが、ボットが使用可能な卵を見送ったことを示すものではない。卵行が使用不能表示へ戻った後は、灰色・使用済み行を blind 確定しない fail-closed 設計と退却一律禁止方針に従って白兵で戦い切った。

なお、同じ 18:03:46Z には `battle_boss_card_missed` があり、同時刻のクースカン未使用は別 issue #5469 の対象である。これは卵とは別の card_miss 機構で、観測版 v1485 には #5472 の修正がなかった。現行 main には #5472（実効 marker `hanjuku-chart-v1488-summon-card-without-egg`、merge commit `0a99182c0e6cb15f0bf2ec3278ba6ce45c86effc`）が入っている。この隣接欠陥は #5470 の卵判定を変えない。

## 1. fingerprint / occurrence の照合

現行 `month_audit.py` の式で再計算し、issue の識別子は一致した。

- occurrence 式: `sha256("g1190-ch1v9|1190|battle_loss_with_resources|egg|ソーピニヨン|A:b5c6ea46:M8c8457|1795543446")[:20]` = `13c7d9b1b4912597e30b`
- fingerprint 式: `sha256("1|battle_loss_with_resources|battle_loss_with_resources")[:16]` = `2114e84c7599b48c`
- コード上の生成箇所: `src/docich/hanjuku/month_audit.py` L1367-L1372

fingerprint は章・kind・主 signal だけから作られるため、issue コメントの後続再発（ゼウス、ココット、ビーツ、アルテミス）が同じ fingerprint でも同じ root cause を意味しない。本検証の結論は依頼された主 occurrence `13c7d9b1b4912597e30b` に対するものである。

## 2. 卵は当該戦闘で実際に使われた

issue 本文の監査 incident に含まれる直前決定は、最後の 30 件だけの投影だが連続している。18:03:34Z から 18:04:06Z の敗北まで、間に別の `battle_result` はない。したがってこの区間の `egg_battle_menu` / `monster_menu` は、最後に step `A:b5c6ea46:M8c8457` で敗北した同じ連続戦闘へ帰属する。

重要な時系列:

| 時刻 | 記録 | 意味 |
| --- | --- | --- |
| 18:03:29Z | `battle_enemy_transform_observed` | 敵が卵で変身したことを観測 |
| 18:03:34Z | `egg_battle`「原典戦術⑥、既定はたまご」 | 敵召喚へ対して卵を使う方向の独立判断。これ単体は使用確定ではない |
| 18:03:38Z〜18:03:45Z | `monster_menu_choice`（「とけこむぞー」「たべちゃうぞー」） | カメレオンマンの技メニューを操作 |
| 18:03:43Z | `monster_skill_stall`「2ターン使っても敵のHPが変わらないため、もう一方の技を試す」 | **味方召喚獣ターンであることの決定的な証拠** |
| 18:03:46Z | `situation_held`「たまごをつかうが使えない表示」→ `battle_egg_general_attack`（step `A:b5c6ea46:M8c8457`） | 卵行が使えない状態へ戻り、将軍は通常攻撃へ移行 |
| 18:04:06Z | `battle_result`（同 step、loss） | HP ally 0 / enemy 26 で敗北 |

`monster_skill_stall` が味方ターンの証拠になる理由はコード上明示されている。`_monster_stall_switch` は `owner != 'ally'` なら即座に return し、記録を出さない（`src/docich/hanjuku/flows/battle_menus.py` L3412-L3429）。`monster_skill_stall` の emit は `owner == 'ally'` の枝にだけある（同 L3446-L3454）。したがって 18:03:43Z の記録は、敵側メニューや owner 不明メニューではなく、味方召喚獣の技選択が実際に進んでいたことを示す。

技名も整合する。`たべちゃうぞー` / `とけこむぞー` はカメレオンマンの 2 技で、`src/docich/hanjuku_reference.py` L696-L699・L1109 にある。`monster_menu_step` は「Our summoned monster's own turn」を扱う関数で、敵側表示は `monster_menu_wait` に留める設計である（`battle_menus.py` L3529-L3540、L3617-L3649）。

`egg_battle` の初回記録だけでは行選択の確定を証明できないが、味方召喚獣ターンの存在は卵使用の独立した証拠である。#5300 でも同じ構造を「卵はこの戦闘で召喚へ実使用済み」と判定している。

## 3. 「卵残数 4」は敗北時点の実残数ではない

現行 main でも `battle_result` は `egg_uses` に `(mem.get('egg_uses') or {}).get(cur.get('ally'))` をそのまま載せる（`src/docich/hanjuku/flows/battle.py` L1793-L1798）。ここには戦闘中の召喚消費を差し引く処理も、`egg_recheck` 中かどうかの注記もない。

卵選択側は `_egg_recheck` で将軍を再確認待ちへ入れるだけで、台帳を減らさない（`src/docich/hanjuku/strategy/economy.py` L508-L515）。`_own_egg_empty` の docstring も「Selection alone is not a use receipt, so neither this gate nor `_egg_recheck` invents a decrement」と明記する（`src/docich/hanjuku/judgment/battle.py` L2815-L2833）。`_own_egg_positive` は `egg_recheck` 中の将軍について False を返し、古い正数を使用可能な証拠として扱わない（同 L2851-L2861）。

`state/egg_memory.py` L127-L134 の `current_uses` docstring も、battle / direct ledger update が卵を消費した場合は record refresh なしに ledger だけが古くなりうることを前提にしている。敗北すると名前行・出撃画面・野営再確認などの再観測機会がないため、`4` は戦闘前またはそれ以前の最終観測値であって、敗北時に 4 回分使えたことのレシートではない。

監査はこの生値をそのまま使う。`_battle_resources` は `egg_uses > 0` なら resources に `egg` を入れ（`src/docich/hanjuku/month_audit.py` L1140-L1175）、`_battle_resource_event` は loss と resources の存在だけで `battle_loss_with_resources` を作る（同 L1230-L1287）。confidence の medium 降格は `cards_unconfirmed_only` の切り札側だけで、real egg は high のままというコメントどおりである（同 L1317-L1324）。

## 4. 18:03:46Z 以降に卵へ戻らなかったのは fail-closed 設計どおり

18:03:46Z の `situation_held` は、現行コードの「`action == 'use_egg'` だが `たまごをつかう` が画面 text にない」分岐の理由文と一致する（`src/docich/hanjuku/flows/battle_menus.py` L3092-L3103）。コメントには、使用済み卵行は灰色になり OCR から落ちるため、存在しない行を追い続けず通常攻撃へ戻すとある（同 L3093-L3097）。画面分類側も、使用済み卵行が灰色で OCR に載らないことを明示している（`src/docich/hanjuku/os_layer/screen.py` L1545-L1557）。

この分岐では行が読めない・灰色・使用済みの区別を盲確定で埋めず、`action = 'attack'` に落とす。続く `battle_egg_general_attack` は実コマンド位置を確認して通常攻撃へ戻す記録で、issue の 18:03:46Z の記録と同じ step・同じ理由文である（`battle_menus.py` L3303-L3312）。読めない行や灰色行を A 連打で確定しないのは fail-closed 設計であり、使用可能な卵行を認識しながら捨てた経路ではない。

退却が False なのも現行方針と一致する。`_hero_retreat_menu` は退却を一律禁止し、「切り札・卵を使い切った後もそのまま戦い切る」方針を記録する（`battle_menus.py` L701-L711。VERIFY-5300 §5、VERIFY-5318 §3 と同じ）。本 occurrence でも `retreat_selected` は False で、この点に異常はない。

## 5. 観測版 v1485 と現行 main の差分

issue の bot version `hanjuku-chart-v1485-locked-boss-activation` は commit `8bcc1fd0931c5a7a6a317fc06ac3762f6de044eb` の marker と一致する。`8bcc1fd09..main` を確認すると、`battle_result` の卵 metric、`_battle_resources` / `_battle_resource_event`、`_egg_recheck`、`_own_egg_positive` / `_own_egg_empty` を変える差分はない。`month_audit.py` には差分があるが、築城 gold と camp-watch identity の無関係な変更だけである。したがって本件の卵検知機構は現行 main でも同じである。

一方、`battle_menus.py` には卵が使えない召喚戦で切り札を開く修正が入っている。現行 `_summon_card_row_hidden` は、灰色・使用済み卵行が白 OCR から落ちることを明示し、`egg_battle_menu` で `こうげき` / `もうこうげき` が読める実測コマンド箱なら卵行欠落を no-egg 証拠として有界スクロールする（`battle_menus.py` L2811-L2840）。これが PR #5472 の修正で、`git merge-base --is-ancestor 0a99182c0 main` は真である。

この差分が意味するのは次の範囲だけである。

- 観測版 v1485 では、同時刻の `battle_boss_card_missed`（クースカン）が示すとおり、卵行欠落時に切り札を開かない card_miss 欠陥があり得た。
- その欠陥は別 issue #5469 / card_miss 系列の問題であり、現行 main には #5472 の修正済み。
- #5470 の監査 resource は egg のみで、切り札は `cards_selected` / `cards_used` / `cards_remaining` すべて空。卵の使用済み性・stale ledger・fail-closed 設計の判定は #5472 で変わらない。

## 6. 関連 VERIFY / issue との照合

| 関連記録 | 判定 | 本件との関係 |
| --- | --- | --- |
| VERIFY-5246（#5246） | (b) | 卵は召喚へ使用済みだが残数表示が残る同型。停滞帰還後の fail-closed も同じ方向 |
| VERIFY-5300（#5300） | (b) | `monster_menu_choice` を卵使用証拠とする直接先例。本件はさらに `monster_skill_stall` が owner=ally を裏付ける |
| VERIFY-5318（#5318） | (c) | 卵使用の証拠がなく、stale ledger だけでは使用可能卵を証明できないケース。本件とは証拠構造が違う |
| VERIFY-5393（#5393） | (c) | `monster_menu_choice` が別 step で当該戦闘へ帰属できなかったケース。本件では連続区間に別 `battle_result` がなく、`monster_skill_stall` が allied menu を証明する |
| #5469 / VERIFY-5466 | card_miss は (a) | 同時刻・同系の切り札未使用は別機構の実欠陥。卵の本判定とは分ける。現行 main には #5472 の修正済み |

したがって本件は、VERIFY-5318/5393 の「卵使用証拠なし」型ではなく、VERIFY-5246/5300 の「卵使用済み」型である。

## 判定詳細

- **(a) 実欠陥ではない（卵監査として）**: 味方召喚獣ターンが進んでおり、卵は使われた。卵行が使えない表示へ戻った後に blind 確定せず白兵へ戻ったのも fail-closed 設計どおり。隣接するクースカン未使用は別 issue #5469 の card_miss 実欠陥として扱う。
- **(b) 設計どおり**: `egg_uses` の生台帳を監査し、卵だけなら high で保存する現行監査契約どおりの起票。ボット側も卵を使い、使用不能表示後は通常攻撃、退却禁止という既存方針に従った。
- **(c) 誤検知ではない**: 「残数 4」が敗北時の実残数でない点は検知上の限界だが、当該監査はそもそも生の観測台帳を使う仕様で、本 occurrence には卵使用と使用不能化の連続証拠がある。使用可能な卵行を見送った証拠はない一方、起票自体は監査契約に反していない。

## 補足（将来の改善余地。本検証では実装しない）

- `battle_result` metric に `egg_battle` / `card_summon_observed` / `egg_recheck` 所属 / 最後に観測した卵行状態を含めると、`monster_menu_choice` や `monster_skill_stall` を人が読まなくても「この戦闘で卵使用済み」を監査側で判別できる。
- `battle_survival_unavailable` や `situation_held` の `observed_metric.labels` / `owner` は issue へ投影されない。投影されれば、灰色卵行と OCR 欠落の切り分け、召喚獣メニュー所有者の確認がさらに速い。
- 卵だけの `battle_loss_with_resources` を high のまま残すか、`cards_unconfirmed_only` と同様に検知限界として降格・注記するかは owner 判断である。現行契約は high を維持する。

## 検証メモ

- 実行した確認: issue embedded incident JSON の読解、fingerprint / occurrence の再計算、観測版 commit `8bcc1fd09` と現行 main の関連差分、`git merge-base --is-ancestor 0a99182c0 main`、現行コードと関連 VERIFY / issue の静的照合。
- ドキュメントのみの追加のため pytest は実行しない。`git diff --check` と秘密情報スキャンは投稿前に実施する。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
