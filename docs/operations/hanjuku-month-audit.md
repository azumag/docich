# 半熟英雄: 月内出撃ゼロ・ボスフェーズ無戦闘監査（v1252）

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

## ボスフェーズ無戦闘（v1252）

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
