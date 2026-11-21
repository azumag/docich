# VERIFY-4999 — [半熟英雄] 切り札を残して退却: ch1 シナモン

- Issue: azumag/docich#4999 / 判定 `battle_retreat_with_resources` (confidence: high)
- ラン: g1190-ch1v5 / generation 1190 / hanjuku-chart-v1352-password-measured-order
- 観測: ch1 2-10、シナモン vs クランベリー、ally_hp 10 / enemy_hp 10、結果 retreat
- 切り札: 選択 [バグストーム, バグストーム, フットバース] / 使用 [バグストーム] / 残り [バグストーム, フットバース]
- 決定: `battle_hero_retreat_select` (choice=たいきゃく)、退却選択 False、理由は危険敗北回避の定型文
- 作業 base: origin/main b783f47ba（worktree、commit/push なし）
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードと GitHub 上の事実を根拠に調査

## 結論: 設計どおり（欠陥ではない）。修正しない

`battle_retreat_with_resources` は「退却/敗北時に卵残数>0 または切り札が未使用で残った」ことを
機械的に数えるレビューシグナルであり、退却の正当性を主張するものではない
（`docs/operations/hanjuku-month-audit.md` §卵・切り札を残した退却/敗北、
`src/docich/hanjuku/month_audit.py` `_battle_resources` / `_battle_resource_event`）。
残り札 = `cards_selected` − `cards_used`（消費確認できた分のみ差し引く）+ pending。
#4958 と同じ集計規則であり、本件も同じ構造で発火した。

## 根拠（コード確認）

### 1. 退却判定の閾値

- `GENERAL_CRITICAL_RETREAT_HP = 12`（`judgment/battle.py:1364`）。
  ally_hp 10 ≤ 12 → `_critical_survival_needed` True（同:1504-1508）
  → `_survival_needed` True（1389-1390）→ `_default_retreat_needed` True（2129-2130）
  → `retreat_needed` の baseline True（`judgment/retreat.py:178`）。
- 危険域での基準は敵HPとの比較ではなく絶対値。互角（10 vs 10）でも次の接触1回で
  将軍が落ちうる帯域であり、危険HPでの確定退却行優先は g460（ココット 24→12→0 が
  カードメニュー待ちで死亡）の教訓として実装済み。
- baseline を覆しうる `_live_melee` 経路（retreat.py:63-84）は `card_flow` が無い
  こと・兵数が current であること等を要求する。本件は選択済み・使用未確認の
  card_flow（pending フットバース）があったため不成立。
  `_resources_exhausted` も `not cur.get('card_flow')` を含む（retreat.py:86-93）
  ため「資源確認済みの弱将軍リスク許容」（expendable 経路, threshold = HP≤4
  または HP<敵 かつ開始値20%以下）には届かず、`existing_resource_first_guard`
  で baseline が維持される。シナモンは profile 'expendable'（combat 6 ≤ 6、
  max_hp 39 ≤ 40）だが、未確認の切り札計画がある限りリスク許容は発動しない設計。
- side は 'attack'（defense では「たいきゃく」行が無く choice=たいきゃく の
  記録自体が成立しない）。ch1 のため `_retreat_forbidden`（ch9 限定）も無関係。

### 2. 切り札の使用確認

- `cards_used` には実測証拠（敵HP低下・札行の消失2フレーム・敵兵数減少）で
  確定した分だけが入る（`flows/battle_menus.py` announce 段、
  `card_pending_confirm` 経路）。バグストーム1枚のみ確定。
- `battle_hero_retreat_select` では `card_flow.selection_planned` の pending 札を
  `_card_use_unclassified` で「退却を優先し切り札の実使用は未確認」として保持する
  （`flows/battle_menus.py:357-358`）。`_card_use_unclassified` は `card_flow` を
  消すため、この decision の `card_pending` metric は実質 None になり、残り札の
  検出は `cards_selected` − `cards_used` の差分だけが担う（監査側も
  `pending` の追加は同名の重複計上を避ける構造）。選択≠使用のまま危険HPで
  メニュー待ち継続しないのは g460 由来の明示設計
  （`flows/battle.py:1348-1364` `battle_retreat_preempted_card` 経路と同一方針）。
- 監査イベントの `retreat_selected` は `observed_metric.hero_retreat_selected`
  を見るが、`battle_hero_retreat_select` の metric は同キーを emit しない
  （`flows/battle_menus.py:366-372`；`hero_retreat_selected` は battle_result 系の
  記録でのみ付く）。本 decision では常に False になる cosmetic な項目であり、
  「退却できなかった」証拠ではない。`choice='たいきゃく'` の行選択記録そのものが
  退却说の証拠。

### 3. 残り札の評価

退却を遅らせるのは `_critical_safe_card(..., for_retreat=True)`
（`judgment/battle.py:1940-1956`）で、観測した実一覧 `listed_cards` のうち
「SURVIVAL_CARDS 内・未 attempt・未 selected」で `single_card_lethal` または
必要時の `healing_not_a_kill` の札だけを数える。

- 残り2枚はどちらも `cards_selected` 済みのため、仮に致死札でもこのチェックの
  対象外（「選択は使用ではない」の裏返しで、選択済み札は新たな救済選択肢を
  構成しない）。構造的に退却遅延の理由にならない。
- バグストーム: 対将軍は `half_both_hp`（`hanjuku_reference.py:1336-1344`）。
  lethal は常に False で self_harm（両軍半減）。10 vs 10 で撃てばシナモンが
  5 になり悪化するだけで、退却を遅らせる救済札ではない。
  SURVIVAL_CARDS 外（`judgment/battle.py:863-867`）。
- フットバース: general_damage 6 + 味方兵1人×3 − 敵兵1人×10 吸収
  （`hanjuku_reference.py` CARDS / `card_damage_estimate`）。敵 HP 10 への
  保守的下限は兵数未読で 0（敵兵6×10吸収）、lethal False。致死には
  「敵兵≈0 観測 かつ 味方兵≥2」の最有力読みが必要で、本件の兵数では裏付け不能。
  クランベリーは卵なし（`GENERAL_EGGS` (False,2)）なので、仮に一覧に未選択で
  残っていても role は `no_autonomous_egg_trigger` であり defer 対象の
  `single_card_lethal` にはならない。
- シナモン自身の卵もなし（`GENERAL_EGGS` (False,2)）→ `_egg_before_retreat` /
  `_egg_rescue_pending` 不発で、退却前に確認すべき卵救済も存在しない。

### 4. #4958 との差分の評価

#4958（HP 6 vs 37、ミックミー未確認残り）は「使用未確認のまま危険HPで退却優先」
として非欠陥確定（tests/test_hanjuku_issue_4958.py）。本件との差は
「互角 HP」と「残り札の内容」のみ:

- 互角でも ally_hp 10 は絶対閾値 12 以下の危険域であり、閾値は敵HP比ではない。
- 残り札は自爆半減（バグストーム）と非致死（フットバース）で、残して退却して
  失うものが無い。#4958 同様「将軍を救えない札を温存する必要は無い」が、
  本件はさらに「撃つと悪化する札」まで含む。
- 唯一の非退却シナリオ（expendable + 資源枯渇 + live melee 計測優位）は
  card_flow pending の時点で到達不能であり、実際の分岐は安全側の既定経路。

## 変更ファイル

なし（検証のみ。`version.py` / `collect_diagnostics.py` 変更は欠陥時のみのため未実施）。
`tmp_verify_4999.py`（再現用スクラッチ、未追跡）は実行・削除ともに環境側で
拒否されたため worktree に残置。

## テスト結果

- 静的解析で全分岐を追跡（上記ファイル・行番号）。
- ローカル pytest / `python3 -m compileall` の実行を複数回試みたが、この環境では
  python3 起動を含む exec が拒否され未実行。**実行未確認を成功扱いしない**ため、
  検証はコード読解のみと明記する。`tests/test_hanjuku_issue_4958.py`（同構造の
  固定テスト）は既存で、この判定を変える変更は無い。

## 残課題（本 issue の範囲外・参考）

- バグストーム×2・フットバースの携行は `_stock_cards`（`strategy/replan.py:861`、
  #4685/#4895: 在庫保有札を持たせる）由来と整合し、開幕使用は
  `no_autonomous_egg_trigger` で許可される設計内動作。ただし互角の 10 vs 10 は
  「最初のバグストームで両軍半減（例 20→10）」の可能性を示唆する。
  self_harm 札を通常将軍への開幕札として使う可否自体を見直すかは別 issue の
  論点であり、本件の退却判定の正当性とは独立（シナモンは
  `sacrifice_general` でもない: combat 6 > 5）。
- 監査シグネチャの性質上、「選択済み・使用未確認」の札は今後も残り札として
  計上される。実際には消費済みだった可能性を監査だけでは区別できない
  （未校正の使用告知という既知の限界）。
