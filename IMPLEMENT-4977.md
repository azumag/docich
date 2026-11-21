# docich#4977 実装記録 — 切り札使用告知の代替証拠

対象: azumag/docich#4977「切り札の使用告知を確定できず卵・札を残して敗北する」
base: origin/main 30cec8457。変更は worktree に未コミットで残す（commit/push/PR/fetch/rebase なし）。
BOT_VERSION: `hanjuku-chart-v1347-card-announce-evidence`（v1348 は main の #4975 が使用中のため v1347 を使用）。
handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本は未読（AGENTS.md §1 に従い未読と記録）。

## 原因（issue とコードの対応）

- 切り札選択後の「使用告知」は実フレームで校正済みの署名がなく、`stage == 'announce'` の
  `CARD_ANNOUNCE_LIMIT`（6 観測）経路では従来「選択後の敵HP低下」だけが使用確定の代替証拠だった
  （`battle_card_effect_confirmed`、docich#4703 導入）。
- 告知バナーを読めず HP パネルも見えないと、消費済みの札をもう一度選び直し
  （`battle_card_announce_retry` → `battle_card_candidate`）、一覧の再読も不安定だと
  `battle_card_list_unclassified` で B 退出し、札・卵を残したまま白兵へ戻っていた。
- `battle_card_list_unclassified` の有界待ち（`list_observations < 4`）は、同一フレームの
  再観測も待ち時間として消費していたため、一覧が安定して描画される前に予算を使い切っていた。

## 変更内容

### 1. 使用告知の代替証拠を追加（`src/docich/hanjuku/flows/battle_menus.py` `card_list_step`）

`stage == 'announce'` かつ `selection_planned` の `CARD_ANNOUNCE_LIMIT` 超過経路で、
既存の敵HP低下に加えて以下の2証拠を `battle_card_effect_confirmed` として採用する
（`observed_metric.evidence` に `enemy_hp_drop` / `card_row_absent` / `enemy_soldiers_drop` を記録）。

- `card_row_absent`: announce 待機中の各観測で、読める一覧から選択した札の行が消えた
  （または明示的な「きりふだはありません」表示）フレームを `frame_sha256` で重複排除して収集し、
  **異なる2フレーム以上**で確定。誤確定防止のため、選択時に見えた一覧
  （`flow['names_at_select']`）に含まれる札が対象で、現在の可視行が
  `names_at_select - {選択札}` の部分集合に限る。スクロールや別の実札名への誤読で
  選択外行が見えるフレームは証拠にしない。
- `enemy_soldiers_drop`: 戦闘パネルが併存するフレームで敵兵士数が選択時点
  （`flow['enemy_soldiers_at_open']`）より減少した場合に確定。
  `_card_battle_reading` が更新する `cur['enemy_soldiers']` と比較する。

いずれの証拠も成立しない場合は従来どおり `battle_card_announce_retry`（1回）→
`_card_use_unclassified` の有界経路を維持する（再試行時に `announce_absent_frames` と
`list_obs_sha` をリセット）。

スナップショット追加:
- `battle.py` のチャート札 flow 生成箇所と `battle_menus.py` のキャトルミュー flow 生成箇所に
  `enemy_soldiers_at_open` を追加。
- `card_list_step` の選択確定点（チャート系）と `_survival_card_list` の選択確定点
  （生存系）で、`enemy_hp_at_open` / `enemy_soldiers_at_open` を `setdefault` し、
  `names_at_select` を保存。生成時にスナップショットを持たない flow（生存系・旧 bot の
  ホットロード flow）にも同じ証拠基盤を与える。`names_at_select` を持たない flow では
  行消失証拠は発火しない（fail-closed）。

### 2. 一覧読み取りの安定化（同ファイル、stage `'list'` の missing-card 経路）

`flow['card'] not in names` の有界待ちで、`frame_sha256` が前回観測と同一のフレームは
`list_observations` にカウントしない（`flow['list_obs_sha']` で記憶）。
`frame_sha256` が無い画面（既存テストの合成 Screen 等）は従来どおり各呼出を数えるため、
既存の有界性は変わらない。

### 3. バージョン

- `src/docich/hanjuku/version.py`: `BOT_VERSION = 'hanjuku-chart-v1347-card-announce-evidence'`
- `ops/vm_actions/collect_diagnostics.py`: 許可リストに同エントリを追加
  （末尾カンマあり、既存エントリ保持、`v1347-chikujou-issue` の次・`v1348` の前に配置）

### 4. テスト追加（`tests/test_hanjuku_card_announce_retry.py`）

- `test_announce_timeout_row_absent_on_two_frames_confirms_use`: 異なる7フレームで
  選択札の行が消えた一覧 → `card_row_absent` で `battle_card_effect_confirmed`、
  `cards_used` に追加。
- `test_announce_timeout_single_absent_frame_still_retries`: 行消失が1フレームだけ →
  確定せず従来の retry 経路。
- `test_announce_absent_evidence_requires_distinct_frames`: 同一 sha の7観測は1観測
  → 確定せず retry。
- `test_announce_absent_outside_the_selected_list_is_not_evidence`: 選択時一覧に無かった
  実札名が見えるフレーム（スクロール・誤読相当）→ 証拠にせず retry。
- `test_announce_timeout_enemy_soldiers_drop_confirms_use`: 併存パネルで敵兵士 6→3 →
  `enemy_soldiers_drop` で確定。
- `test_unreadable_list_wait_counts_only_distinct_frames`: 同一 sha の読めない一覧は
  `list_observations` を消費せず、別 sha 4観測で従来どおり
  `battle_card_list_unclassified` + B退出。

## 検証結果

- **ローカル実行は未実施**: 本環境で `python3 -m pytest`、`python3 -m compileall`、
  `python3 --version` の各コマンド実行が拒否された（実行可否の承認が得られなかった）。
  構文・回帰の確認は差分レビューと既存テスト契約の照合で行った。CI のフルスイートに委ねる。
- 自己レビューで確認した回帰観点:
  - `frame_sha256=None` の合成 Screen を使う既存テストは、sha 非設定時に従来どおり
    各呼出を `list_observations` へ数えるため挙動不変
    （例: `test_unreadable_chart_list_backs_out_boundedly_without_inventing_absence`）。
  - `frame_sha256` 定数付きの一覧を使う既存テスト
    （`test_hanjuku_queen_battle_menu_evidence.py::test_absent_charted_card_is_recorded_missing_and_closes_without_a`）
    は、同一 sha の2回目で加算されなくても `missing_list_names` の一致判定で従来どおり
    missing 経路に入り、出力・`cards_missing` は不変。
  - announce 系の他テスト（`test_hanjuku_card_progress.py`、生存系・防衛系の announce flow）
    は `battle_step`/`battle_menu_step` 経由または札名を含む画面で、`names_at_select` 未設定の
    flow は行消失証拠が fail-closed で発火しないため不変。
  - `battle_card_effect_confirmed` のイベント名・`cards_used`/`card_consumption_complete` の
    更新経路は既存 HP 低下経路と同一で、月次オーディットの集計対象を変えない。

## 完了条件との対応

- 改善案の 1（代替証拠）と 2（同一 frame_sha256 を待ち時間に数えない）を実装。3（実フレームでの
  OCR 校正）は実機画像が必要で本変更の範囲外。
- `battle_card_list_unclassified` / `battle_loss_with_resources` の発生率比較は月次オーディット
  待ち（ローカルでは計測不可）。
- 追加テストを含む `tests/test_hanjuku_card_announce_retry.py` ほか札フローの green は
  CI 確認待ち（ローカル実行拒否のため未確認、上記）。

## 残課題

- `card_row_absent` は一覧の**行消失**のみを証拠にする。行が残ったまま灰色になる実画面が
  ある場合は検出できず、retry/missing 経路へフォールバックする。灰色行の読み取りは
  `os_layer/screen.py` 側の追加 OCR が必要（本変更では未着手）。
- announce 待機中に `card_select` 画面では `field_soldiers` が計測されないため、
  `enemy_soldiers_drop` は戦闘パネル併存フレームでのみ発火する。`card_select` への兵士計測
  拡張は #4048 の誤読規律と整合させて別途検討。
- 使用告知の OCR 校正（改善案3）と、校正済み署名が得られた場合の `battle_card_candidate`
  解消は別 issue で継続。
- 月次オーディットで `battle_card_list_unclassified` / `battle_loss_with_resources` の
  前後比較、および `battle_card_effect_confirmed` の `observed_metric.evidence` 内訳確認が必要。

## 検品ゲートでの CI 修正（2026-11-22、主担当）

PR #4981 の CI で full-suite / prediction-regressions が 4 件失敗した
（`test_hanjuku_card_damage_gate.py`、`test_hanjuku_control_melee.py`、
`test_hanjuku_control_chain_melee.py`、`test_hanjuku_delayed_control_melee.py`、
いずれも `cards_used == ['クースカン']` で未確定のはずの制御札が確定扱い）。

原因: 選択遷移（`card_list_step` と `_survival_card_list`）で追加した
`enemy_hp_at_open` / `enemy_soldiers_at_open` の setdefault スナップショット。
選択時点の読みを基準にすると、announce 待機中の白兵による HP・兵士数の
減少まで札の効果と誤認し、受領書を捏造する（制御札の followup テストが
明示的に禁止している挙動）。

修正: 選択遷移の setdefault を両方とも除去した。HP・兵士数の証拠は、
戦闘メニュー開放時に記録された基準値を持つ flow でのみ発火する
（fail-closed）。`names_at_select`（行消失証拠用）は選択時点の一覧が
正しい基準なので両経路とも維持する。修正後、関連 7 ファイル 172 件と
新規 6 件を含む札・戦闘・白兵・制御系の広域実行で確認する。
