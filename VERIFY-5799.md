# VERIFY-5799 — [半熟英雄] 切り札を残して退却: ch12 ナストリーニ

- 対象: azumag/docich#5799
- 判定: `battle_retreat_with_resources` / confidence `high`
- fingerprint `9c2d030318f33e19` / incident_id `8e55968640cd02012efb`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1593-unreadable-card-source`
- 検証 base: 現行 main `cc5b097ce00349b16d67aee84c93e02bb95092b8`（`BOT_VERSION` `hanjuku-chart-v1600-ch4-boss-not-direct`）
- 検証範囲: read-only。issue 本文と現行 main の静的照合。コード修正・commit・push・PR・gh コマンドなし。本番 decision log はローテーション済みのため探していない。

## 結論: (a) 実欠陥

ただし監査の「残り 4 件」は物理在庫 4 枚の証明ではない。`cards_selected` は選択/再選択の試行帳簿であり、同名札の重複試行を数える。実欠陥と判断した対象は、物理枚数の過大計上ではなく、**HP 13（critical HP 12 より上）で、`battle_cards_before_retreat` が始めた切り札使用試行を settle / 使用確認へ完走させず、退却 open が selected-but-unconfirmed flow を supersede した制御経路**である。

現行 main にも同じ構造穴が残っている。`stage='announce'` の選択済み flow だけは HP>12 で退却を止めるが、announce retry 後の `list` / `menu` / `down` などへ戻った flow は、過去の `cards_selected` に同名札があるだけで「選択済み」と見なされ、`_retreat_waits_for_card_path` の未選択 flow 保護から外れる。その結果、HP>12 でも `_hero_retreat_open` 末尾の supersede 分岐へ落ち、`battle_retreat_preempted_card` が出る。これは #4999 / #5101 系の「退却前に卵・切り札を使い切る。使用未確認の札は有界な再試行を済ませる」方針に対する実装上の残存欠陥である。

## fingerprint / occurrence の式照合（未計算）

現行 `src/docich/hanjuku/month_audit.py` の `battle_retreat_with_resources` 系 builder での式は以下。

- fingerprint: `_fingerprint(chapter, kind, [kind])`
  - 実装: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}".encode('utf-8')).hexdigest()[:16]`
  - 今回の入力 raw: `12|battle_retreat_with_resources|battle_retreat_with_resources`
  - 期待値: `9c2d030318f33e19`
- incident_id / occurrence: `_build_battle_resource_incident` 内の固有式
  - 実装: `sha256(f"{runtime_id}|{generation}|{kind}|{','.join(resources)}|{general}|{step}|{int(float(now))}".encode('utf-8')).hexdigest()[:20]`
  - 今回の入力 raw: `g1190-ch1v9|1190|battle_retreat_with_resources|card|ナストリーニ|A:cf2b0b0b:Mc81a52|1795825010`
  - 期待値: `8e55968640cd02012efb`
  - `now` は issue JSON の `created_at: 1795825010.2747421` から `int(float(now)) = 1795825010`。

この環境では `python3` 実行が承認却下されたため、sha256 の再計算は**未実施**である。上記の式と入力値を検品ゲートで再計算すること。他種別の `_incident_id(identity, chapter, month)` 式は今回の battle resource 系には使わない。

## 経路の切り分け

### 1. `battle_cards_before_retreat` は実際に始まり、選択入力まで行ったが、使用完了へは到達していない

issue の直前決定は以下の順序である。

1. `battle_survival`（HP・兵士数・将軍の戦闘力で不利なため救済手段を確認）
2. `battle_cards_before_retreat`（使用未確認の切り札が残るため、退却より先に救済メニューで1回だけ使用を試行）
3. `battle_command_open_requested`
4. `battle_survival_select`
5. `battle_card_selected`（HP低下に対処する切り札を選択。消費は未確定）
6. 約 1.7 秒後に `battle_card_unclassified`（選択済み・消費未確認のまま退却確認へ置き換わったため中断）
7. 同時刻に `battle_retreat_preempted_card` と `battle_hero_retreat_open`

したがって #4999 の survival 経路そのものは発火している。ただし `battle_card_selected` は「選択入力を予定/送信したが消費は未確定」の記録であり、使用完了ではない。使用確定・announce 限界・retry 完了・`battle_retreat_card_wait` の記録はなく、直後に退却 open へ置き換わっている。1 回の救済メニュー開放が始まったことと、切り札を使い切れたことは別である。

現行コードでも `battle_cards_before_retreat` は退却前の survival 開放を返すだけであり（`flows/battle.py` L1589-L1606）、実際の使用確定は card flow / survival list 側の後続ライフサイクルに依存する。その後続ライフを退却 open が止めたことが本件の核心である。

### 2. HP 13 では `stage='announce'` の選択済み flow が設計上は退却を止める

現行/v1593 とも `_hero_retreat_open` は `card_flow.stage == 'announce'` の場合、

- `ally_hp` が int でない、または `ally_hp > GENERAL_CRITICAL_RETREAT_HP` なら退却 open を返さない
- `GENERAL_CRITICAL_RETREAT_HP = 12`
- critical 以下でも `selection_planned` には `RETREAT_CARD_GRACE_LIMIT = 6` の有界 grace を与える
- grace 消費後または HP<=4 の即死帯で preempt する

という分岐を持つ（`flows/battle.py` L1548-L1588、`judgment/battle.py` L1748-L1760）。HP 13 は 12 より上なので、真に `stage='announce'` の flow なら、この supersede は起きない。実際、現行テストにも HP 13 では pending announce flow を保持し `battle_retreat_preempted_card` を出さない固定がある（`tests/test_hanjuku_survival.py` L570-L581）。

それにもかかわらず issue は HP 13 で `battle_retreat_preempted_card` を記録した。したがって、少なくとも v1593 観測系では announce 以外の段阶へ落ちた selected/retry-reset flow が末尾の supersede 分岐へ進んだと読むのがコードと整合する。

### 3. 現行 main に残る非 announce flow の穴

現行 main には #5776 残件対応として `_retreat_waits_for_card_path` がある（`flows/battle.py` L1469-L1501）。これは未選択の `card_flow`、開幕 retry pending、damage-gate 再確認を退却より先へ待たせる。しかし選択済み判定は、

- 現在 flow の `selection_planned`、または
- flow の card が過去の `cards_selected` にある

のどちらかで真になる。announce retry は `stage='list'` へ戻し `selection_planned=False` にするが、過去の `cards_selected` は残る。この retry-reset flow は `_retreat_waits_for_card_path` では待機対象にならず、`stage='announce'` でもないため HP>12 の保護も効かない。最後に `_hero_retreat_open` 末尾で `_release_interrupted_card_flow` → `battle_retreat_preempted_card` となる。

これは仕様テストにも現れている。`tests/test_hanjuku_card_interrupted_release.py` L121-L143 は retry-reset 後の flow（`stage='list'` / `selection_planned=False` / 過去選択あり）が退却に supersede され、`battle_retreat_preempted_card` へ行くことを固定している。つまり現行 main は、この非 announce の selected 扱い flow を HP 13 でも守らない。

#5101 系では `CARD_SPEND_ATTEMPT_LIMIT = 3` で同名札の再試行を数える（`judgment/battle.py` L2574-L2619）。issue では最後の retry 試行が selection 直後に中断され、3 回分の spend attempt や生存側の bound 消化が見えない。したがって「有界な試行を尽くした後のやむを得ない退却」ではなく、試行途中を退却が摘んだ経路である。

### 4. `battle_card_effect_confirmed` 3 件と使用未分類 3 件の関係

`battle_card_effect_confirmed` は、告知署名が読めなかった札について、メニューが閉じた後の敵 HP 低下を実測証拠として使用を確定する経路である（`flows/battle.py` L738-L753、`flows/battle_menus.py` L2292-L2333）。

issue の `cards_used` は `['ノリウツール']` だが、これは同名札を重複追加しない帳簿であり、「物理的に 1 回だけ使った」ことの直接証明ではない。一方で issue の decision rows には同時刻の `battle_card_effect_confirmed` が重複して見えるため、3 件をそのまま物理 3 回使用とも読めない。少なくとも 1 回は敵 HP 低下により使用確定した、という以上の物理枚数推定はできない。

`cards_unclassified` 3 件は、選択したが告知署名未校正のまま settle した fail-closed 帳簿である。最後の pending flow も `_release_interrupted_card_flow` 経由でこの帳簿へ積まれる。監査では `cards_selected` 5 - `cards_used` 1 = `cards_remaining` 4 だが、unclassified は 3 件なので `cards_unconfirmed_only` が False となり、confidence は high のままだった（`month_audit.py` L1348-L1377, L1396-L1450, L1590-L1597）。

ここにも既知の検出限界がある。`cards_selected` は物理枚数ではなく選択試行の多重集合であり、同名札の再選択・retry・重複確定を監査だけでは厳密に物理在庫へ写像できない（IMPLEMENT-4999 の残課題でも明示）。したがって本検証は「4 枚残っていた」とは断定しない。断定できるのは、少なくとも最後の選択済み・消費未確認 flow を抱えたまま退却 open へ進んだことである。

### 5. `pending` が退却を妨げなかった理由

`card_pending` 自体は常に退却を止めるフラグではない。現行分岐では、

- `stage='announce'` の選択済み flow: HP<=12 で grace 後に preempt、HP>12 では保持
- `stage != 'announce'` の flow: HP>12 の保護がなく、過去選択済みなら末尾 supersede で `battle_retreat_preempted_card`
- HP<=4: card wait / spend detour を打ち切る安全弁
- 未選択 flow: v1595 以降は `_retreat_waits_for_card_path` で有限に保護

という非対称がある。issue の pending はこの非対称の穴側に落ちたため、退却を妨げなかった。

### 6. #5776 との異同

同じ点:

- どちらも retreat open / retreat 側の判断が card flow の完走より先に入る「カード完走 vs 退却」の競合である。
- #5776 の残件1「退却 open が card flow を中断する隣接経路」は本件と同じ系列である。

異なる点:

- #5776 はボス戦で、ダメージゲートに拒否された強札が未選択のまま一覧ごと閉じられた事案である。
- 本件は通常の攻防で、`battle_cards_before_retreat` → survival list → `battle_card_selected` まで進んだ後に、消費未確認の flow を supersede した事案である。
- #5805 / v1595 は未選択 flow・開幕 retry・damage-gate recheck を保護したが、過去選択済みの retry-reset / 非 announce flow は保護していない。したがって #5776 修正で本件は完全にはカバーされていない。

### 7. HP 13 vs 14 の退却判断自体

退却そのものは単純な HP 互角だけで否定できない。ナストリーニは `(maxHP 66, combat 8)`、ガーラントは `(maxHP 97, combat 14)` で、基礎戦力は味方が大きく劣る。現行の退却判定は critical HP 12 以下だけでなく、開始/参照 HP に対して `hp < enemy` かつ `hp * 4 <= ref` の深い減少でも退却する（`judgment/battle.py` L2838-L2878）。HP 13 なら 52 以下の開始/参照 HP でこの帯に入る。issue の `battle_survival` 理由も、単に HP 比ではなく兵士数・将軍の戦闘力を含む不利を示している。

したがって「HP 13 vs 14 なのに退却した」こと自体は不合理ではない。問題は退却の有無ではなく、退却前の有界な切り札試行を完走させなかった順序である。

## 未決・限定事項

- issue の decision rows は `observed_metric` を落としているため、最後の `battle_card_unclassified` に付いていたはずの `stage` を直接確認できない。上記の非 announce 判定は、HP 13 で announce 保護を通らない現行/v1593 分岐との突き合わせによる静的推定である。
- `cards_remaining` 4 件は物理在庫 4 枚の証明ではない。実欠陥の結論は物理枚数ではなく、選択済み flow の未完走 supersede に置いている。
- 本番 decision log はローテーション済みのため、selection input が実機のどの画面へ届いたか、一覧が実際に消えたか、物理的に何枚消費されたかは確認不能。
- `python3` 実行が承認却下されたため、hash 再計算と pytest は未実施。静的照合のみ。

## 最終分類

**(a) 実欠陥**。退却判断自体は妥当な帯域だが、HP 13・生存側の使用試行中に、非 announce の selected/retry-reset card flow が退却 open に supersede される経路が現行 main にも残っている。#5776/v1595 の未選択 flow 保護ではこの経路を完全には塞いでいない。監査の「残り 4 件」は試行帳簿由来で物理枚数の証明ではないが、それは本件の制御欠陥を否定しない。

## 検品ゲート確認（Muse、2026-11-28 16:28 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|battle_retreat_with_resources|battle_retreat_with_resources")[:16] = 9c2d030318f33e19、SHA-256("g1190-ch1v9|1190|battle_retreat_with_resources|card|ナストリーニ|A:cf2b0b0b:Mc81a52|1795825010")[:20] = 8e55968640cd02012efb。いずれも issue の値と一致する。
- 結論 (a) 実欠陥は、HP 13（critical HP 12 より上）で `battle_cards_before_retreat` が始めた切り札使用試行を settle / 使用確認へ完走させず、退却 open が selected-but-unconfirmed flow を supersede した制御経路が現行 main にも残っていることで裏付けられている。`stage='announce'` の選択済み flow だけが HP>12 で退却を止め、announce retry 後に `list` / `menu` / `down` へ戻った flow は過去の `cards_selected` があるだけで「選択済み」と見なされ、`_retreat_waits_for_card_path` の未選択 flow 保護から外れる。#5776 / v1595 の保護ではこの経路を完全には塞いでいない。
