# 半熟英雄: 月内出撃ゼロ・ボスフェーズ無戦闘監査（v1347）

## 目的

ゲーム内の1ヶ月が終わった時点で、敵城向けの出撃が1件も成立しなかった月を
記録し、前後の計画・在城・行軍・画面遷移・決定を残す。記録はランタイム内に
JSONLで保存し、必要なときに `gh` で修正用 issue を発行する。ゲーム入力・
方策・月次処理は変更しない。

関連: #4642（月内出撃頻度）、#4626（出撃記録と実体の乖離）、
#4448（野営帰還の月次中断）、#4632（未読画面の保留）。

## 判定

`month_sorties`（docich#4642）を主指標にする。

| 指標 | 意味 |
| --- | --- |
| `confirmed` | 閉じた月の `month_sorties.launches`（敵城向け・確定） |
| `departures` | 月内の `order_launched` / `sortie_departed_observed` / `sortie_launch_confirmed` の step 重複除去 |
| `moves` | `purpose=move` の自城移動。判定には含めない |
| `house_dispatches` | 修理派遣。判定には含めない |

- `zero_sortie`: `confirmed == 0` かつ `departures == 0` で、月を最初から観測できた。
- `unconfirmed_launches`: `departures > 0` だが `confirmed == 0`。出撃はしたが
  成立確認が取れなかった月で、zeroとは別に記録する（既定ではissue化しない）。
- `zero_unverified`: 出撃0だが、月の観測が不完全・ledger不明・ログ被覆不足などで
  「0」と断定できない月。記録はするが既定ではissue化しない。
- `skipped`: ラン開始後の最初の月、章替わり、月の逆行、短すぎる月など。
- `ok`: 出撃あり。記録しない。

## 完全観測の条件

- 監査状態が前月から継続している（ラン開始後の最初の月は partial）。
- 同じ章で、月が `YYYY-MM` の次月になっている（章替わり・月ジャンプは除外）。
- 観測回数 20 以上、月の実測時間 120 秒以上。
- 観測間隔（境界の直前を含む）が 60 秒以下。
- `month_sorties.scope` が閉じた月と一致する。
- 決定ログの被覆率が 0.9 以上。

`confidence` は `high / medium / low`。`observation_gap` と
`decision_log_coverage` だけのときは `medium`、それ以外の理由が付くと `low`。
issue化の既定は `high` のみ。

## ボスフェーズ無戦闘（v1347）

通常城をすべて確保し、ボスの城（第4話は四季王サイト）だけが残った状態を
**ボスフェーズ**とする。第9話の移動ボスは街道の全城が自軍になった時点。
ボスフェーズが600秒以上連続して観測され、その間にボス戦闘の証拠が一度も
出なかった場合に `boss_phase_no_battle` を1回記録する。

ボス戦闘の証拠（いずれか1つ）:

- `battle_start` の `enemy` が章ボス名 / `BOSS_GENERALS` / `BOSS_MONSTERS`
- `boss_hp_seen` または `boss_hp_history`（ボスHPパネルを読めた）
- `boss_victories` または `boss_defeated`

600秒はボス城への行軍・突入を待つ猶予。phaseが途切れた場合（通常城を失った、
章が変わった、ボスを撃破した）はリセットし、同じphaseで一度だけ記録する。
記録は `world` / `plan` / `flow` とフェーズ中の決定を含み、既定でissue対象
（confidence high）。issue題名は
`[半熟英雄] ボスフェーズでボス戦闘ゼロ: ch<章> <ボス名>`。

## キャンプ帰還の失敗・キャンセル（v1347）

キャンプ（野営）からの帰還が terminal に失敗/取消/未確認になった観測で、
`camp_*` のrecordを1件保存する。対象decision:

| decision | kind | 意味 |
| --- | --- | --- |
| `camp_recall_aborted` | `camp_recall_failed` | 操作上限・野営メニュー未開放・カーソル未確認で断念 |
| `camp_boss_redirect_aborted` | `camp_recall_failed` | ボス再出撃の帰還失敗 |
| `camp_recall_skipped` | `camp_recall_cancelled` | 自軍城の屋根が見えず帰還先を選べず取消 |
| `camp_group_screen`（`unclassified_screen_closed`） | `camp_recall_cancelled` | 未分類画面で帰還を閉じる |
| `camp_recall_unconfirmed` | `camp_recall_unconfirmed` | A送信後の本人・到着を確認できない |

- 将軍・野営位置・帰還先・stage/steps・input_sent/interrupted・理由・
  直前120秒の決定・計画/在城/flowを1件に保存する。
- 同じ `(chapter, kind, reason_code, camp, general, castle)` は900秒cooldown。
  連続する同一事象でissueを乱発しない。
- issue題名は `[半熟英雄] キャンプ帰還失敗/キャンセル/未確認: ch<章> <将軍または城>`。
  fingerprintは章＋kindで、既存issueにはコメントする。
- `hero_recall_skipped`（城内の主人公）や `retreat_recheck_invalidated`（加入で名簿変更）
  は通常経路なので対象外。
- 保存後のissue発行はbotのseal時callback（v1257以降）と同じ。

## 出撃失敗・キャンセル（v1347）

terminal な出撃失敗/取消のdecisionを、`sortie_*` のrecordとして保存する。

| decision | kind | 意味 |
| --- | --- | --- |
| `sortie_launch_failed` | `sortie_failed` | 出撃確定後も部隊▲が現れず、再試行上限で指示失敗 |
| `order_failed` | `sortie_failed` | 出撃元メニュー・将軍不在・切り札・携行札などで指示失敗 |
| `situation_held`（`attack_retry_exhausted` / `boss_retry_exhausted`） | `sortie_failed` | 戦闘再試行の上限で指示失敗 |
| `sortie_cancelled_observed` | `sortie_cancelled` | 出撃元の将軍一覧に将軍が残り、出撃取消を観測 |

- 将軍・出撃元・対象・目的・step・misses・理由・直前120秒の決定・
  計画/在城/flow/statsを1件32KB以内で保存する。
- 同じ `(chapter, kind, reason_code, step_local, general, target)` は900秒cooldown。
- issue題名は `[半熟英雄] 出撃失敗/キャンセル: ch<章> <将軍またはstep>`。
- `sortie_launch_unverified`（再試行中）、`target_not_under_marker`（位置再測定）、
  `order_retry`（戦闘再試行）はterminalでないため対象外。
- issue発行はbotのseal時callback（v1257以降）と同じ。

## 卵・切り札を残した退却/敗北（v1347）

戦闘結果が退却または敗北なのに、本人の卵残数が正数、または切り札が未使用で
残っている場合に `battle_retreat_with_resources` / `battle_loss_with_resources`
を保存する。

| decision | 条件 | 意味 |
| --- | --- | --- |
| `battle_hero_retreat_select`（choice=たいきゃく） | `egg_uses>0` または切り札が未使用 | 卵/切り札を残して退却 |
| `battle_retreat_preempted_card` | 切替待ちの切り札が未確認 | 切り札を残して退却 |
| `battle_result`（outcome=loss） | `egg_uses>0` または `cards_selected` に未使用札 | 卵/切り札を残して敗北 |

- 将軍・敵・城・side・HP・卵残数・切り札の選択/使用/残り/pending・
  退却選択・理由・直前120秒の決定・計画/在城/flow/statsを1件32KB以内で保存する。
- `battle_egg_before_retreat` は「退却より先に卵を確認する」 proactiveな記録なので
  単独ではissue化しない。実際の退却選択または敗北だけを対象にする。
- 同じ `(chapter, kind, resources, general, step)` は900秒cooldown。
- 題名は `[半熟英雄] 卵/切り札を残して退却/敗北: ch<章> <将軍またはstep>`。
- 卵・切り札の残数はbattle結果/退却metricへ追加し、実測値だけを使う。

## 野営放置（3ヶ月以上連続）（v1347）

`general_statuses.statuses` で `location=camp` の将軍を月ごとに追跡し、
同じ将軍が**3ヶ月以上連続**で野営のままなら `camp_abandoned` を1回保存する。

- 現在の月は `general_statuses` のcamp行で判定する。月が変わってもcamp行が
  残っていれば継続月数を+1し、城・行軍・死亡（hp<=0）・一覧から消えた場合は
  その滞在を終了する。
- 初回導入時は `camp_status_seen.actors[name].tick` と
  `month_start_tick` / `month_durations` から通過した月境界数を推定し、
  すでに3ヶ月以上の滞在は即時記録する。それ以外は月境界で数える。
- 一度issue化した滞在は `reported` で再発行しない。城へ戻った後に再び
  野営になった場合は新しい滞在として1ヶ月から数え直す。
- 将軍・継続月数・初回tick/月・HP・卵・壊れ・状態文・城候補・最終観測tick・
  現在のflow/計画/在城/統計を1件32KB以内で保存する。
- 題名は `[半熟英雄] 野営放置: ch<章> <将軍> (<n>ヶ月)`。
- issue発行はbotのseal時callback（v1257以降）と同じ。

## 築城可能なのにキャンセル/月一終了（v1347）

月一メニューの `month_done` / `month_confirm`、または terminal な
`chikujou_declined` / `chikujou_leftover` の時点で、**資金・将軍・未試行の
所有城がそろって築城可能**なら `chikujou_available_at_end` /
`chikujou_cancelled_with_resources` を保存する。

- 判定は `strategy.economy._chikujou_reserve` / `_chikujou_candidates` /
  `_chikujou_affordable` と同じ条件を使う。
- 候補は所有城のうち、`chikujou_tried`・`chikujou_upgrades`・
  `chikujou_max_level_seen`・`chikujou_castle_unavailable` を除外。
- 城に将軍がいることは `world.garrison` または
  `general_statuses.location=castle` で確認する。将軍不明ならissue化しない。
- `month_done` / `month_confirm(うむッ!)` は「月一終了」、
  terminal declined/leftover は「キャンセル」として1回だけ保存。
- 同じ章・月は1回だけ。所持金・必要残し・候補・試行済み・増築済み・
  flow/計画/在城/統計を1件32KB以内で残す。
- 題名は `[半熟英雄] 築城可能なまま月一終了: ch<章> <月>` または
  `築城可能なのにキャンセル: ...`。

## 保存先

ランタイムディレクトリ（例: `run-local/runtimes/g1190-ch1v4/`）:

| ファイル | 内容 |
| --- | --- |
| `hanjuku_month_audit.json` | 進行中の月・pending incident（1KB程度、毎観測atomic write） |
| `hanjuku_month_audit.jsonl` | sealed incident（1件5〜15KB、4MBでrotate） |

`hanjuku_bot.json` には監査状態を追加しない（256KB制限とstate budgetに影響させない）。
incident は runtime_id / generation / bot_version / chapter / month / tick /
観測数 / max gap / 出撃内訳 / 計画 / 在城 / 行軍 / flow / stats / 決定内訳 /
前後30件 / signals / 証拠ファイル名を持つ。リストは20件、文字列は160字、
1件は32KBで打ち切る。生ログ・例外文・環境変数・tokenは保存しない。

月を閉じた直後から300秒を after window として集計し、その間に出撃・奪取・
防衛が起きたかを `after` に残す。次の月境界が先に来た場合は early seal する。

## issue 発行（gh）

bot は GitHub に接続しない。記録がsealedになった後、`gh` が使える環境で
CLIを実行する。

```sh
# 一覧
HANJUKU_RUNTIME=/path/to/runtimes/g1190-ch1v4 \
  PYTHONPATH=src python3 -m docich.hanjuku.month_audit list

# 本文の確認（ネットワーク不要）
HANJUKU_RUNTIME=/path/to/runtimes/g1190-ch1v4 \
  PYTHONPATH=src python3 -m docich.hanjuku.month_audit render

# dry-run（投稿しない）
HANJUKU_RUNTIME=/path/to/runtimes/g1190-ch1v4 \
  PYTHONPATH=src python3 -m docich.hanjuku.month_audit file --dry-run

# 発行（投稿前に agent-guards/secret_scan.py を必ず通す）
HANJUKU_RUNTIME=/path/to/runtimes/g1190-ch1v4 \
  PYTHONPATH=src python3 -m docich.hanjuku.month_audit file --repo azumag/docich

# 変更のあったruntimeだけを定期発行（サービスは起動しない）
PYTHONPATH=src python3 -m docich.hanjuku.month_audit watch \
  --state-dir /path/to/state --interval 60
# cron等で1回だけ
PYTHONPATH=src python3 -m docich.hanjuku.month_audit watch \
  --state-dir /path/to/state --once
```

- `agent-guards/secret_scan.py` が見つからない場合は投稿しない（fail-closed）。
  場所は `AGENT_GUARDS` 環境変数、またはリポジトリの隣の `agent-guards/`。
- `gh auth status` が失敗する場合も投稿しない。
- ラベルは既存の `bug` と `auto-generated` を使う。新しいラベルは作らない。
- 題名は `[半熟英雄] 月内出撃ゼロ: ch<章> <主シグネチャ>`。同じ章・同じ
  主シグネチャの open issue があれば、新しいissueを作らずコメントする。主シグネチャは
  plan_starved / departure_failure / monthly_scan / camp_recall / battle / unread /
  no_source / stuck_march / ledger_unknown の優先順で1つ選ぶ（全signalsは本文に残す）
  （同じ incident の重複コメントはしない。コメントは1時間に1回まで）。
- 既知の追跡issueへ寄せたい場合は、本文の `fingerprint` を確認して手動で
  コメントする。自動closeはしない。回復は次の完全な月の `confirmed > 0` で
  確認する。

### 本番VMの記録を発行する場合

production VM のランタイムは読み取り専用で取得する。tracked file の編集や
production checkout 上の `gh` 実行はしない。

1. 対象ランタイムの `hanjuku_month_audit.json` と `hanjuku_month_audit.jsonl`
   を、ローカルの一時ディレクトリ（例: `runtimes/<runtime_id>/`）へコピーする。
2. そのディレクトリを `HANJUKU_RUNTIME` にして `render` / `file --dry-run` で本文を確認する。
3. `secret_scan` の clean を確認して `file` を実行する。

## botからの自動発行（v1347）

月内出撃ゼロ・ボスフェーズ無戦闘のsealed recordを保存した直後、**その観測を
実行しているbotプロセス**が、detachedの子プロセスで

```
python3 -m docich.hanjuku.month_audit --runtime <runtime> file --incident <id>
```

を起動する。子プロセスはbot終了後も継続し、botのマシンの `gh` でissueを発行する。
タイマーやGitHub Actionsは使わないため、コーナーが動いていない時間には何も実行しない。

- 対象は `auto_file=true` のrecord（zero_sortie high / boss_phase_no_battle high）だけ。
- 子プロセスは `start_new_session=True` で切り離し、botは待たない。
  入出力はruntime内の `hanjuku_month_audit_issue.log`（1MBでrotate）。
- 投稿前スキャンは `agent-guards/secret_scan.py` を優先し、無い場合は
  repo内の `ops/vm_actions/secret_scan.py`（vendored copy）を使う。
- `gh` はbotと同じユーザーの認証を使う。tokenはrepo・unit・envへ置かない。
- 同じrecordの重複起動は、sealed/pending状態とissue本文のoccurrence markerで防ぐ。
- 旧5分timerは廃止。`ops/vm_actions/disable_hanjuku_month_audit_timer.sh` が
  production deploy時に残存unitをdisable/removeする。

## 検証

- 合成ログ: `python3 -m pytest -q tests/test_hanjuku_month_audit.py`。
- 実ラン: 次の月境界で `hanjuku_month_audit.jsonl` に1件できること、
  `hanjuku-bot: month_audit_failed` が出ないこと、入力actionsとintervalが
  変わらないこと、`hanjuku_bot.json` のサイズが増えないことを確認する。
- 負の確認: 出撃のある月、partial、章替わり、stale ledger、短い月、gap>60秒で
  issue対象にならないこと。
- 本番: 次の月境界で記録を確認し、`render` の本文をレビューしてから `file`。

## 制限・ロールバック

- 月が2分未満、または観測が60秒以上途切れた月は `zero_unverified` になり、
  既定ではissue化しない。
- ランが月の途中で終了した場合、pending は次回観測または
  `python3 -m docich.hanjuku.month_audit finalize` で seal する。
- 監査ログは世代ディレクトリ内に残る。既存の `hanjuku_log_retention` は
  4ログのみを対象にしているため、監査ログの世代削除は follow-up とする。
- 問題が出た場合は `brains/hanjuku/bot.py` の `month_audit.observe` 呼び出しを
  外すか、`src/docich/hanjuku/month_audit.py` を revert する。ゲーム入力には
  影響しない（effectは `month_audit_failed` として記録されるだけ）。