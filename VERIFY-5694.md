# VERIFY-5694 — カシスとの一騎打ち敗北

## 結論

**(a) 実欠陥**。ただし issue 本文が想定した「accept_duel の条件読み違い」や「6連打パターン自体の周波数不足」は、今回の実測では原因と証明できない。直接の実欠陥は、**月イチ一騎打ちで存在しないコマンドメニュー（B）を開こうとして、白兵可能な最初の約8秒に A を一度も送らなかった**ことである。

カシス戦は runtime `g1342-1fe0734c` / bot `hanjuku-chart-v1557-order-start-proof` で実測された。戦闘開始の decision `164159`（epoch `1795687777.540`）で `battle_event_duel` と同時に `battle_card` が立ち、planned action は `B x100ms` だった。`battle_card` の理由は「実携行を確認した未指定の切り札を温存せず開幕に使用」で、札は `イッテツーン`。これは直前の出撃確認で作られた携行札スナップショットを流用した開幕札経路である。その後も `164160`〜`164167` で B が計画・送信され、実送信時刻は `1795687777.879` / `7779.216` / `7780.488` / `7781.800` / `7783.072` / `7785.643` / `7786.944` / `7788.801` の8回だった。この間に HP は どうし90 / カシス73 から どうし25 / カシス24 まで悪化し、最終的に どうし0 / カシス3 で敗北した。

## (1) accept_duel を選んだ観測

判定: **(d) 判定不能**（ただし将来の accept 記録不足は **(c) 既存修正でカバー**）。

- decisions 全体に `accept_duel` は存在しない。`battle_event_duel` に残ったイベント本文末尾は `19ねん10のつき8433Gわたしとはたしあいをしてそなたが` であり、所持金 8433G は読めている。これは 500G 以上だが、通常の `いってごあいてねがえぬか? うむッ!/いかんッ!` という yes/no 観測としては残っていない。
- 戦闘前の text 画面では既定進行の A が連続しているが、yes/no プロンプトとしての decision はない。実際に選択肢が出たか、謀反・強制型の一騎打ちで選択肢が無かったかは、保存された末尾テキストだけでは確定できない。`はたしあい` は両方の経路に現れ得るため、本文末尾だけから「了承した」と断定しない。
- 実行版は v1557 で、accept/decline の実測値を記録する #5700 / v1562 より前である。したがって、仮に yes/no の accept_duel へ進んでいた場合でも、このランに金・将軍数の判定証跡が無いこと自体は説明できる。将来の同種事案の観測不足は #5700 でカバー済みである。
- 今回の証拠では、decline 条件を外した根拠を特定できない。一方、敗北の直接原因は accept/decline の前段ではなく、戦闘中入力が B に奪われたことと実測で一致する。

## (2) 青ゲージ判定と A 連打タイミング

判定: **(a) 実欠陥**。ただし「連打パターンの周波数が不足した」点は **(d) 判定不能**。

- カシス戦には `battle_power` / `battle_melee` / `battle_clash_gauge_cross_check` が残っていない。これはゲージ読取や連打が失敗したというより、切り札フローが `_melee_step` へ到達する前に B を返し続けたためである。
- したがって、6 tap x 3 frame / 30ms wait や dim 時 1 press が実画面の短い窓に合っていたかは、このランからは評価できない。少なくとも今回の敗北では、連打パターン以前に A がゼロ回だったことが支配的である。
- 青は owner 確認どおり1話チュートリアル限定で、2章以降は赤の dim が実窓である。従来の cross-check は blue しか記録しなかったため、今回のような事案で dim の実測タイミングを後照合できないという観測欠陥もあった。

## (3) カシス戦の最終 HP パネルと入力記録

判定: 証拠は残っている。

- `hanjuku_frames/decision-119.png`: 戦闘開始、どうし HP90 / カシス HP73。
- `hanjuku_frames/decision-005.png`: B 連打中、どうし HP25 / カシス HP24。
- `hanjuku_frames/decision-008.png`: 赤い敗北演出、どうし HP0 / カシス HP3。
- `hanjuku_frames/decision-013.png`: 最終パネル、どうし HP0 / カシス HP3。
- `hanjuku_events.jsonl` には上記の B `input_sent` が残っている。`1795687798` に battle から transition へ移り、以後は敗北後のイベント/タイトル遷移であり、白兵中の A 入力は無い。
- 最終 observation は `1795687857.149` に `terminal_reason: game_over` / `terminal_evidence: title_return_after_gameplay`。

## 実装した修正

`hanjuku-chart-v1571-duel-melee-only` として、証拠で確定した欠陥だけを直した。

1. `src/docich/hanjuku/flows/battle.py`
   - `event_duel` では切り札・卵・退却・防衛 kit 読みなど、B のコマンドメニューを前提にする全経路を bypass し、開場台詞の保留後は直接 `_melee_step` へ進める。`planned_cards` も空にし、古い出撃携行札を一騎打ちの利用資源として表示しない。
   - 未オープンの `card_flow` は破棄する。使用告知済みの flow が残る場合だけ未分類として扱い、メニューを待たない。
   - `_hero_losing_melee` による生存メニュー再確認も event duel では使わない（一騎打ちには B メニューが無いため）。
   - `battle_clash_gauge_cross_check` は blue だけでなく dim も戦闘中1回記録する。記録だけで、卵予測・制御は変更しない。
2. `src/docich/hanjuku/version.py` — v1571 へ更新。
3. `ops/vm_actions/collect_diagnostics.py` — v1571 を hotload version allowlist に追加。
4. `tests/test_hanjuku_event_duel.py`
   - 古い launched-order の `イッテツーン` があっても event duel の初手が B ではなく A burst になることを固定。
   - 主人公敗北劣勢時にも存在しない survival menu へ行かないことを固定。
5. `tests/test_hanjuku_clash_gauge.py`
   - dim が cross-check に1回だけ記録され、予測を書き換えないことを固定。

## 変更しなかった点

- accept/decline 条件と所持金読取のフォールバック: 今回 yes/no の accept_duel 観測自体が残らず、謀反・強制型だった可能性を排除できないため。証拠の無い仕様変更は入れない。
- duel mash の 6 tap x 3 frame / 30ms wait と dim 時 1 press: 今回は A が一度も送られず敗北したため、このパラメータが敗因だったとは証明できない。実測で不足が示された場合に別途校正する。

## テスト

テストは未実行。ローカル実行コマンドの承認が拒否されたため、この環境では pytest を走せなかった。実行予定だった最小対象は次である。

- `python3 -m pytest -q tests/test_hanjuku_event_duel.py tests/test_hanjuku_clash_gauge.py`
- duel 関連を確認する場合は `tests/test_hanjuku_chart_bot.py` の該当テスト

## 残りの未確認事項

- `hanjuku-speedrun` copy は `tools/hanjuku_speedrun_fork.py` が git ref から生成する規約のため、commit 禁止のこの worktree では同期できない。検品側で commit 後に generate/check を通す必要がある。
- v1571 の実機確認は次回の月イチ一騎打ちで必要。完了条件は、戦闘開始直後の action_plan が B ではなく A burst になり、`battle_melee` / `battle_power` が残ること。
- dim cross-check が実機で記録されれば、従来の 6 tap / dim 1 press のタイミング適合性を初めて実測評価できる。
- 今回の event 本文が謀反・強制型か、yes/no を伴う武芸者型だったかは、保存テキスト末尾だけでは確定できない。将来 v1562 以降の accept_duel 記録または選択肢フレームで判定する。
