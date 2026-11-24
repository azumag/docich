# VERIFY-5318 — [半熟英雄] 卵を残して敗北: ch11 コンポート（battle_loss_with_resources）

- Issue: azumag/docich#5318。`battle_loss_with_resources`（confidence: high、
  fingerprint `dc40d3a96d6e0629` / occurrence `53247f5d588f5220dfec`）。
  ch11 17-10、観測 427回 / 95.6秒、ラン `g1190-ch1v9` / `hanjuku-chart-v1435`。
- 戦闘: コンポート vs オレガノ @ チリベット、side=attack、outcome=loss、
  HP ally 0 / enemy 65。卵残数 1 / 切り札 選択[] 使用[] 残り[] / pending None /
  退却選択 False。直前の決定: `battle_survival_unavailable`、
  `battle_hero_retreat_open`、`battle_command_open_requested`。
- 検証 base: 作業ブランチ `verify/5318-battle-loss-with-resources`（作成対象:
  origin/main `bc5bed955`、**v1445**）。**この環境では `git branch/switch/
  checkout/commit`、`gh`、`python3` の exec がすべて拒否されるため、ブランチ
  作成・コミット・issue 取得・テスト実行は未実施**。VERIFY-5202 と同じ制約。
  行番号は origin/main のツリー（`git show` で読み取り）を基準とする。
  チェックアウト中のファイルは `dd6ed0fbe`（origin/main より4コミット前）で、
  本件に関係する `flows/battle_menus.py`・`month_audit.py`・`os_layer/*` は
  origin/main と同一（`judgment/battle.py`・`flows/battle.py` は #5160 由来の
  追加があり、行番号は origin/main 側で採番）。
- 検品ゲート訂正（2026-11-24 11:4x、Muse）：(1) 本ファイルの初版は base を「v1446 相当」、
  #5160 を「v1446」と記していたが誤り。`bc5bed955` は v1445、#5160 も v1445
  である。ゲート時点の origin/main は `23c108d62`（v1447、#5325 マージ済み）。
  (2) §5 の「issue JSON の labels」前提も誤り。issue #5318 の JSON には
  `observed_metric` / `labels` が一切含まれない（ゲートで 0件を確認）。labels は
  実行時決定記録（`battle_survival_unavailable` の observed_metric）に残るが、
  issue へは輸出されないため、残留切り分けには `hanjuku_decisions.jsonl` が要る。
  (3) `month_audit.py` は `src/docich/hanjuku/month_audit.py`（`flows/` 配下ではない）。
  (4) 灰色卵行コメントは `os_layer/screen.py:1535-1537`（初版の :1516-1522 は約19行ずれ）。
  いずれも結論 (c) には影響しない。関連テストはゲートで実行済み（下記）。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の
  事実を根拠に調査。

## 結論: **(c) 誤検知（#5300 / #5246 と同型の stale 台帳系）**

issue の主張「使用可能な卵を残して敗北した」は、incident に内包された
証拠では成立しない。`battle_survival_unavailable` は**実際に開かれて読めた
戦闘メニューで、使用可能な卵行が存在しなかった**ことの記録であり、現行
コードで「読めた白い卵行を選択しない」経路は存在しない。卵残数 `1` は
実測値ではなく `mem['egg_uses']` の最終観測台帳値であり、戦闘中の使用に
よって減算されない設計（コードコメントで明記）のため、選択・召喚後に再観測
されなければ stale なまま `battle_result` に乗る。#5300（卵は召喚へ実使用
済み）・#5246（卵使用済み）と同じ構造の検知限界で、ボット行動自体は
「使える資源が無い → 白兵で戦い切る」という設計どおりのものだった。

最も確からしい再構成（提示された決定列と整合）:
`battle_hero_retreat_open` → `battle_command_open_requested`（Bで箱を開く）
→ メニュー上で `battle_retreat_banned`（一律退却禁止）と `battle_survival_
unavailable`（灰色/不在の卵行、切り札なし）→ B → 白兵 → HP0 → `battle_result`
。卵はこの戦闘または以前の戦闘で選択・消費され（または使用不能表示）、
台帳だけが残った、と読むのが自然。

**残留する限定留保**: 実行時決定記録（`hanjuku_decisions.jsonl`）にある
`battle_survival_unavailable.observed_metric.labels` が取得できれば、
「卵行だけ灰色（=消費済み）」か「読み取り自体が部分的（=白い卵行の単発
誤読で救済が一度で尽きた、別機構の実欠陥）」かを最終切り分けできる。
issue #5318 の JSON には `observed_metric` / `labels` が含まれないため、
issue だけではこの一点は確定できない（§5 参照）。ただしどちらでも「使える卵が
提示されたのに捨てた」事実にはならず、(a) にはならない。

---

## 1. 「卵残数 1」は台帳値であり、戦闘中の消費を反映しない

- `src/docich/hanjuku/flows/battle.py:1793-1799` — `battle_result` の
  `observed_metric.egg_uses` は `(mem.get('egg_uses') or {}).get(cur.get('ally'))`
  であり、生の台帳値をそのまま載せる（`observed_uses` / `current_uses`
  照合なし、`egg_recheck` 未確認ラッチの考慮なし）。
- `mem['egg_uses'][name]` の書き込み点は実画面の観測だけ:
  `flows/prompts.py:890-901`（出撃画面のたまご行）、`flows/camp_recheck.py:464-466`
  （野営再確認）、`flows/house.py:786`（ハウス情報）、`flows/egg_recovery.py:73,327`
  （回復数量）、および `judgment/battle.py:2843` の一括無効化
  `mem['egg_uses'] = {}`。**選択や召喚の確定による減算経路はコード全体に存在しない。**
- `judgment/battle.py:2817-2819`（`_own_egg_empty` docstring）:
  「Selection alone is not a use receipt, so neither this gate nor
  `_egg_recheck` invents a decrement」— 台帳が減らないのは明示的設計。
- `state/egg_memory.py:130-131`（`current_uses` docstring）:
  「battle and direct ledger updates can spend an egg without refreshing
  the record」— 監査が使うのはこの「古い方を取る」照合すら通っていない
  生値であり、さらに stale になりうる。
- `strategy/economy.py:508-516` `_egg_recheck` — 卵選択時は `dirty_status`
  と `egg_recheck` リストに積むだけで台帳は減らない。`judgment/battle.py:2857`
  `_own_egg_positive` は `egg_recheck` 内の将軍を False とする（旧回数を
  信じない）が、`battle_result` の metric はこれを無視して台帳値を出す。
- つまり「前の戦闘（またはこの戦闘）で卵を選択→消費→未再観測」なら
  `egg_uses=1` のまま敗北記録が作られる。#5300 判明の型そのもの。

## 2. 実メニューの証拠: 卵行は使用可能な状態では読めていない

- `_survival_menu`（`flows/battle_menus.py:361`）の行選択は `labels` の
  メンバシップで決まる。第3分岐 `:469-471` は **HP 条件なしで**
  `('たまごをつかう' in labels and not rescue['egg_attempted']
  and not _own_egg_empty)` → `たまごをつかう` を選ぶ。読めた白い卵行を
  スキップする経路は存在しない。
- `battle_survival_unavailable`（`:488-493`）は全分岐が外れたときだけ
  記録される。`egg_uses` 台帳=1 なら `_own_egg_empty` は False
  （`judgment/battle.py:2821-2827`: uses>0 → False）。`rescue['egg_attempted']`
  は `:504` でのみ立ち、必ず `:514` の `battle_survival_select` と対になる。
  提示された直前の決定に `battle_survival_select` は無い（120秒窓は
  `month_audit.py:1277`、戦闘時間 95.6秒を全て含む）。
- よって `battle_survival_unavailable` が立った時点で **「たまごをつかう」
  行は labels に存在しなかった** = 灰色（消費済み・使用不能）または
  読み取り不能。`os_layer/screen.py`（灰色2行検出 `disabled_item_box` /
  `hidden_battle_commands`、:1248-1274 付近）と `:1535-1537` のコメント
  「A general whose egg is spent draws たまごをつかう greyed out, so it is
  not read」が、消費済み卵の行は OCR に載らないことを裏付ける。
- `battle_menu_step` は `screen.kind == 'battle_menu'` 必須（`:937-941`）で、
  `_survival_menu` の呼び出し3箇所（`:1023` `:1046` `:1090`）はすべてその
  内側。つまり**実際に開いてパースできたメニュー**での判定であり、
  「メニューを一度も開けなかった」系列ではない。
- 既存テストとの整合: `tests/test_hanjuku_survival.py:136-143`
  `test_greyed_egg_and_unsafe_cards_are_not_selected` — 灰色卵行は選択
  されず `survival['exhausted']` が立つ。`:129-133` — 読める卵行は無条件に
  選ばれる。`:363-374` — disabled box は1観測で `battle_survival_unavailable`
  + B。実行は環境制約（python3 拒否）で未実施、静的照合は整合。

## 3. 切り札・退却・オーナー方針との関係

- 切り札: 選択[] 使用[] 残り[] pending None は「この出撃で切り札を非携行」
  と整合（携行ゼロなら残り札も無い）。`month_audit.py:1124-1159`
  `_battle_resources` の `cards_remaining` も空で、`resources=['egg']` のみ。
- 退却 False は設計どおり: `flows/battle_menus.py:344-355` `_hero_retreat_menu`
  — オーナー決定 2026-11-23（v1414 `e91428f7e`、issue 記載の PR #5230）で
  退却は一律禁止。「切り札・卵を使い切った後もそのまま戦い切る」
  （`battle_retreat_banned` の理由文 :353）。`battle_hero_retreat_open`
  （`flows/battle.py:1442`）は「退却の可否を確認する」ために箱を開くが、
  メニュー段で選択しない。VERIFY-5202 §「残存観察」でも同旨を確認済み。
- 方針の系譜: `771faaa51`（v1360、#4999/#5014）「退却の前に卵と切り札を
  使い切る」→ v1414「退却一律禁止、使い切って戦い切る」。現行方針では
  「退却して残す」より強く「戦い切る」ため、監査シグネチャ
  `battle_loss_with_resources` が意味を持つのは「本当に使える資源を
  残したか」の一点だけになる。本件では使える卵行が提示されなかったため、
  この前提が成立しない。

## 4. battle_survival_unavailable の判定はその場では正しい

発火時点で提示された救済手段:

- 卵: 行なし（灰色・消費・読み取り不能のいずれか、§2）。
- 切り札: 非携行（§3）。
- 退却: 一律禁止（§3）。
- `おくのて`: `_survival_menu` の選択肢に含まれない別機構（防衛・召喚戦
  向けの `okunote_step` が担当）。本件は攻撃側の通常白兵で、見落とされた
  救済手段には該当しない。

したがって「読み取れる未試行の救済手段がないため白兵へ戻る」はその時点の
画面と方針の下で正しい局所判断。選択後に `rescue['exhausted']` が立ち
白兵へ戻るのも仕様（`battle_menus.py:490-493` + 救済枯渇時の
`battle_menu_rescue_exhausted` 経路）。

## 5. 切り分けの限界（不足証拠）

判定不能要素ではなく、残留する唯一の別機構候補は「**開いたメニューの
読み取りが一時的に不完全で、白い卵行を見落とした**」場合。その場合は
「1観測で `exhausted` が立つ」設計により、実際に残った卵を未使用のまま
失う実欠陥になる。これを確定/排除するには実行時決定記録の
`battle_survival_unavailable.observed_metric.labels` が要る（**issue #5318
の JSON には `observed_metric` / `labels` が含まれない**ため、issue だけでは
確認できない。`battle_menus.py:488-493` で labels は記録されているが、
監査の issue 輸出には載らない）:

- `labels` に `こうげき`/`きりふだ`/`たいきゃく` があって卵だけ無い
  → 卵行だけ灰色 → 消費済み/使用不能 → 誤検知確定。
- `labels` が空または大部分が欠落 → 単発誤読の可能性 → (a) 相当の
  「一読即枯渇」欠陥の再検討が要る。

加えて `battle_retreat_banned`（開いたメニューで必ず記録。本件では issue JSON
内に 2件あり、メニューが実際に開かれて読めたことの裏付け）や
`egg_battle`/`battle_summon_*` 系記録の有無、`mem['egg_recheck']` への
コンポートの所属も、同じく実行時決定記録で確認できる証拠。`labels` 未読でも、
「実メニューで使える卵行が提示されなかった」点は決定列だけで成立し、
結論は変わらない。

## 6. 監査側の構造的な穴（本件判定には影響しない観察、follow-up 候補）

- `month_audit.py:1127` — 卵側は生の `egg_uses` をそのまま資源扱いする。
  切り札側には `cards_unconfirmed_only` → confidence medium の降格
  （`:1257` `:1306`、#5165 由来の「消費確定不能は検知限界」扱い）が
  あるが、**卵には同型の降格が存在しない**。#5300/#5246/本件と同じ
  stale 台帳の誤検知が再発し続ける構造。
- `battle_result` の metric（`flows/battle.py:1793-1806`）には
  `cur['egg_battle']` / `cur['card_summon_observed']` /
  `mem['egg_recheck']` 所属が含まれない。この戦闘で召喚が走ったかを
  監査から判別する手段がない。#5246 では `cur['egg_battle']=True` が
  実使用の証拠だったが、監査 metric はそれを見ない。
- 方向案（実装は別タスク）: 卵資源のみの `battle_loss_with_resources` を
  `cards_unconfirmed_only` と同様に検知限界として降格する、または
  `battle_result` metric に `egg_battle`/egg_recheck 所属/最終メニュー読み
  （`resource_menu_observed` 系の卵行可読性）を追加して、監査側で
  「使える卵が最後に提示されたか」を照合する。
- 既存の緩和: v1445（origin/main、#5160 `01d44e161`）の
  `_early_resource_rescue_needed`（`judgment/battle.py:1447-1524` +
  `flows/battle.py:921-938`）は「劣勢+開始HPの30%減少」で救済メニューを
  早期に開く。ただし卵側の発火条件は同じ `_own_egg_positive`（dirty
  ラッチで塞がる）なので、stale/dirty 状態では本件と同じく発火しない。
  本当に使える卵が残るケースには効くが、誤検知の発生源ではない。

## 手順上の注記

- read-only 検証。VERIFY-5318.md の作成以外のファイル編集・本番 VM 操作なし。
- `git branch`/`switch`/`commit`/`gh`/`python3` の exec がこの環境では拒否
  されるため、ブランチ `verify/5318-battle-loss-with-resources` の作成と
  コミット、および関連テスト（`tests/test_hanjuku_survival.py`、
  `tests/test_hanjuku_month_audit.py`、origin/main の
  `tests/test_hanjuku_early_resource_rescue.py`）の実行は**未実施**。
  静的照合では §2 のテスト表明と現行コードが整合する。
- 検品ゲート（2026-11-24 11:4x、Muse）：現行 origin/main（`23c108d62`、v1447）
  で主要な引用を直接確認（`battle_result` の egg_uses 生台帳値、`_own_egg_empty`
  docstring、`current_uses` docstring、`_survival_menu` の卵行選択分岐と
  `battle_survival_unavailable` 記録、`_hero_retreat_menu` の退却一律禁止、
  灰色卵行コメント、`_battle_resources` / 120秒窓 / `cards_unconfirmed_only`
  降格、`_early_resource_rescue_needed`）。`flows/battle_menus.py`・
  `month_audit.py`・`os_layer/*` は worktree base `dd6ed0fbe` と origin/main
  で同一。関連テスト `tests/test_hanjuku_survival.py` +
  `tests/test_hanjuku_month_audit.py` は 250件 green（
  `tests/test_hanjuku_early_resource_rescue.py` は本 base には存在せず未実施）。
  秘密情報スキャン clean。
- 本ファイルは worktree ルートの新規ファイルとして残す。commit は
  git 書き込みが許可される環境で行う（VERIFY-5202 と同じ扱い）。
- push・PR・issue コメントは実施しない。
