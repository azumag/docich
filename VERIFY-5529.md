# VERIFY-5529 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch6 ハードマン

- 対象: [docich#5529](https://github.com/azumag/docich/issues/5529) / `boss_month_zero_sortie`（confidence `high`）
- fingerprint: `0bd50ce03ecfdb13` / occurrence(incident_id): `8daec1d34db597fa9217`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1496-card-open-limit`
- 月: ch6 6-9（観測 773回 / 172.278秒 / max gap 0.750秒 / coverage 0.989）
- 検証ベース: `origin/main` = `757f24fcd43d`（version marker `hanjuku-chart-v1509-card-at-hp1-audit`）。観測 version は v1496。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物本体 (`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`) は VM 側のため直接参照せず、公開 issue の sealed incident・git オブジェクト・現行コードを照合した。

## 結論: **(b) 設計どおり。既存の遅いフェーズ開始トリアージ（PR #5494）でカバーされる**

本件は「ボスフェーズのまま1ヶ月を過ごしたのに出撃できなかった」事案ではない。最後の通常城アブダレを落とした直後にボスフェーズが成立し、月境界まで **0.574秒・3観測** しかなかった。監査が記録した「月クローズ時点ではボスフェーズであり、月内ボス向け出撃は0」という事実は契約どおりだが、出撃失敗や計画停滞を意味しない。

決定的な裏付けとして、incident の `after` 窓は `complete=true` で、`departures=12` / `recovered=true`、窓内には `plan_map_activate`（理由「機械ボス計画の先頭手順をフィールドで active にして出撃へ渡す」）から `target=ボス` の `order_launched`（ラズベリー、1795576805.7900128）まで記録されている。したがって、どの gate / planning path もボス出撃を阻害していなかった。阻害したものではなく、**ボスフェーズ開始が月クローズ直前だった**ことが零件の原因である。

## 1. fingerprint / incident_id の再計算

現行 main の `src/docich/hanjuku/month_audit.py` と v1496 時点の同ファイルで、式は同じである。

- `_incident_id(identity, chapter, month)`:
  `sha256(f"{runtime_id}|{generation}|{chapter}|{month}")[:20]`
  - 入力: `g1190-ch1v9|1190|6|6-9`
  - sha256: `8daec1d34db597fa9217cd8421b617e81775cadf307b9197ceea22a24a25009d`
  - 先頭20桁: `8daec1d34db597fa9217` — issue と一致
- `_fingerprint(chapter, kind, signals)`:
  `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`
  - `_build_incident` は primary signal だけを渡す。本件 primary は `boss_month_zero_sortie`
  - 入力: `6|boss_month_zero_sortie|boss_month_zero_sortie`
  - sha256: `0bd50ce03ecfdb1372abe8bd5a3cf3e4877092c5d5f1248a2b0fcf45a04c44de`
  - 先頭16桁: `0bd50ce03ecfdb13` — issue と一致

`signals` には `monthly_scan_hold` / `battle_grind` も残るが、fingerprint の入力は全 signals ではなく primary signal 1件である。式・値ともに一致し、起票の同一性判定に異常はない。

## 2. 零件になった機構: gate 失敗ではなくフェーズ開始が月末直前

時系列は次のとおり。

| 時刻 | 事象 |
| --- | --- |
| 1795576338.8867898 | ch6 6-9 の観測開始 |
| 1795576510.780235 | `battle_result` / `sortie_outcome`: ココットのアブダレ攻撃が勝利として終了 |
| 1795576510.783335 | ボスフェーズ tracker 開始（残りはボス、ボス名ハードマン） |
| 1795576511.357787 | 月クローズ |

`_boss_phase` は、通常城をすべて保有し、ボス城だけが残った場合に成立する。アブダレは月内最後の通常城であり、その奪取完了の同じ観測でフェーズが始まった。クローズまでの実時間は `1795576511.357787 - 1795576510.783335 = 0.574452秒`、月全体の約0.333%、観測3回だけである。

したがって月内の行動は次のように読むのが正しい。

1. フェーズ成立前は通常城アブダレの攻略が正当な優先目標だった。
2. アブダレ攻略完了とほぼ同時に月が終わった。
3. ボス専用の計画・出撃 gate が評価・実行される時間がなかった。
4. 月クローズ後の `after` 窓では、機械ボス計画が active になり、ボス向け `order_launched` が出た。

issue 本文の月内 `order_start=20` / `order_launched=0` は一見すると出撃停滞に見えるが、少なくとも最後の通常城向け出撃自体は `sortie_launch_confirmed` 系ではなく既に行軍中の `launched` order として完結しており、月内の `departures=1` / `launch_events=2` と矛盾しない。`order_start` の多くは名簿読取・月次後処理を含む決定系列であり、ボス向け出撃要求が20回失敗したことを示さない。重要なのは、ボスフェーズ成立後に新規ボス出撃を組み立てる時間が0.574秒しかなかったことである。

## 3. #5500 との違い

#5500（ch5 だいまおう、`boss_phase_no_battle`）は別kind・別層である。

- #5500: ボスフェーズが600秒以上継続したのにボス戦闘証拠が出ないという連続フェーズ監査。実測ボス突入を戦闘証拠として扱う境界に実欠陥があり、検証結論は (a)。
- 本件: 月クローズ時点のボスフェーズと月内ボス向け発射数を見る月次監査。フェーズ継続は0.574秒で、ボス戦闘以前にボス出撃を組み立てる時間がなかった。
- 現行 main には #5500 への修正として PR #5537 の実測ボス突入120秒猶予、および PR #5375 の live boss sortie defer が入っている。これらは `boss_phase_no_battle` の誤報抑制であり、`boss_month_zero_sortie` の月次条件を変えるものではない。

したがって #5500 の実欠陥と同じ経路ではなく、#5537 / #5375 の有無でも本件の発火条件は変わらない。

## 4. #5489 との関係

#5489（ch1 クイーン、同じ `boss_month_zero_sortie`）は、ボスフェーズ開始が月クローズの約7.971秒前・37観測だった事案として、PR #5494 で「遅いフェーズ開始のトリアージ」が文書化された。本件は同じパターンで、さらに短い0.574秒・3観測である。

`docs/operations/hanjuku-month-audit.md` の現行契約は次を明示する。

- `confidence=high` は月全体の観測完全性を示す。
- ボスフェーズに十分な出撃時間があったことは示さない。
- `boss_month.started_at` と `month_summary.closed_at` の差、および `observations` を見る。
- 月末直前に始まったフェーズは月内ボス出撃ゼロという事実の記録として扱い、出撃失敗の証拠とは断定しない。
- 最低継続時間を設ける場合は、実測した出撃所要時間に基づく別の受入条件を決めてから変更する。

よって本件は #5489 で確立したトリアージ対象であり、新たなゲーム挙動欠陥とは判断しない。#5489 では次の完全観測月を見る必要があったが、本件の sealed incident には既に `after` 内のボス向け発射が入っているため、追加の実機証拠を待たずに「計画系は動いた」と結論できる。

## 5. #5525 の decision 集計切り捨ての影響

v1496 時点の `_decision_counts` は `Counter.most_common(30)` で上位30種だけを返した。現行 main では PR #5530 / commit `6c1fd176f` により全 decision 種を保持する。

本件への影響は限定的で、結論を変えない。

- 月内ボス出撃数は `_decision_counts` ではなく、`_boss_departures(rows, chapter)` が decision row を直接走査して `target` / `purpose` を見る。集計の上位30種制限は「0件」という結果を作らない。
- issue に保存された月内集計はちょうど30種で、`order_start=20` / `order_done=19` / `roster_probe_complete=19` などの主要系列は残っている。仮に31種目以降が切り捨てられていても、ボス向け発射 row の有無という本件の中核証拠は変わらない。
- fingerprint の primary signal は `boss_month_zero_sortie` に固定され、切り捨てで rare signal が落ちたとしても今回の fingerprint と kind 判定には影響しない。

したがって #5525 は監査の副次シグナル解釈には注意点を残すが、本件の原因判定・結論には影響しなかったと判断する。

## 6. 判定詳細: v1496 での再発か、現行 main でどうか

- v1496 での再発性: **ある**。ただし再発しているのは「月末直前にボスフェーズが始まると `boss_month_zero_sortie` が記録される」という監査挙動であり、ボス出撃不能の実欠陥ではない。#5489（v1489）と同じ late-start パターンが v1496 の ch6 で再観測された。
- 現行 main: `_close_month` は依然として、月クローズ時に `_boss_phase` が成立し `_boss_departures` が0なら `boss_month_zero_sortie` を選ぶ。ボスフェーズの最低継続時間は入っていない。したがって同じ記録は現行 main でも起こり得る。
- それでも実欠陥とは判定しない。現行運用契約がこのレコードを「事実の記録」と定義し、出撃失敗と断定しないトリアージを要求しているためである。最低継続時間や起票抑止を変えるには、ボス出撃の計画・発射に必要な実測時間を別途集めて受入条件を決める必要がある。
- 本件では `after` 窓内の `target=ボス` の `order_launched` が実測済みで、ゲーム側のボス出撃パイプラインは v1496 で動いていた。したがって修正対象の gate / planning path は存在しない。

## 検証メモ

- 実行した検証: issue 本文の sealed incident 読取、fingerprint / incident_id の sha256 再計算、v1496 と現行 main の `month_audit.py` 照合、#5489 / #5500 / #5525 の関連記録照合、`after` 窓のボス向け発射確認。
- ドキュメントのみの追加のためテストは実行しない。
- 本検証は read-only。本番操作・コード修正・VM への接続・ゲーム入力は行っていない。
