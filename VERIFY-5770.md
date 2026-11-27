# VERIFY-5770 — [半熟英雄] 月内出撃ゼロ: ch8 出撃開始後に発射されない

- 対象: azumag/docich#5770 / 判定 `zero_sortie` / confidence `high`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1575-ch9-cliff-camp-recall`
- 月: ch8 `8-1`（観測 833 回 / 197.1 秒 / max gap 0.7 秒 / coverage 0.929）
- 出撃: 確定 0 / 発射 0 / `order_start` 8 / `order_launched` 0
- fingerprint `2a53671cf459ccc5` / occurrence(incident_id) `d2c3a4099c0ac0b284c2`
- 検証範囲: read-only。issue 本文は公開 GitHub ページを参照した（`gh` は実行していない）。現行 worktree のコードと `verify/5759` ブランチの VERIFY-5759 を照合した。本番 decision log / events / frames はローテーション済みの前提で参照していない。git の書き込み操作、コミット、push、PR 作成、コード修正はしていない。
- 現行 worktree: `verify/5770` @ `86044e2e1`（`origin/main` と同じ）。`src/docich/hanjuku/version.py` は `hanjuku-chart-v1589-ch9-no-horse-ch12-three`。issue のラン時版 v1575 から後続の launch-priority / 月次処理修正は入っているが、本件の中核になる「切り札一覧を読めない場合は空携行で進める」経路と「空携行なら出撃を取り消す」経路の組み合わせは現行 main に残っている。
- `DOCICH_HANDOFF_PATH` の確認を含む最初の shell 実行が本環境で拒否されたため、handoff 正本は**未読・参照可否不明**と記録する（AGENTS.md §1）。

## 結論

**(a) 実欠陥。**

`zero_sortie` の検出自体は正しい。ただし個々の `sortie_kit_empty_cancel` は、オーナー決定に基づく局所的には正当な防御である。実欠陥はその先の**統合状態**にある。

1. 機械計画の波状攻撃は、在庫台帳が未確定だと通常の暫定開幕札（現行コードでは `INTERIM_CARDS = ('イッテツーン', 'イッテツーン')` 系）を含む非空の携行計画を作る。
2. 出撃時の `card_select` が読めないと、通常出撃は 2026-11-10 オーナー方針どおり「計画の切り札無しで出撃を続行」へ進む（`card_list_unreadable_depart`）。
3. 実際に札を選べず確認画面の実測 kit が空だと、2026-11-24 オーナー方針どおり「携行札なしの出撃」を拒否して order を failed にする（`sortie_kit_empty_cancel` → `order_failed`）。
4. したがって「card_select が読めない」「確認画面は空と読める」という同じ状態では、最初の fallback が作った空 kit を後段の guard が必ず拒否する。`card_list_unreadable_depart` は非空計画の出撃を成立させる経路として実効を持たない。
5. 失敗した step は `_release_stale_failures` / `plan_step_rearmed` で月内上限付きに pending へ戻るが、7 件の波状攻撃 sibling が同じ阻断条件を共有するため、step ごとの上限は波全体の反復を止めない。翌月には count がリセットされ、同じ失敗が再び走る。

issue の証拠はこの閉ループと一致する。月内の内訳は `order_start` 8 / `order_launched` 0 に対し、`card_list_unreadable_depart` 7 / `sortie_hold_abandoned` 7 / `sortie_kit_empty_cancel` 7 / `order_failed` 7 / `plan_step_rearmed` 7。前月の計画表では `A:25cf0f90` の 7 件（バタール、ゼウス、ポワソン、ミモザ、ブリオッシュ、ロックフォール、バトウラ）がいずれも ながはま→しまばら の attack で failed になっている。翌 5 分窓（early seal）でも `order_start` 3 / `order_failed` 3 / `card_list_unreadable_depart` 3 / `sortie_kit_empty_cancel` 3 で発射 0、回復 no。さらに issue コメントには ch8 `8-3` でも `order_start` 5 / 発射 0 / 回復 no の再発が記録されている。

これは「合法な出撃源がない」月ではない。月末の在城は ながはま(11) / あるまむーん(6) / しらかわ(4) / まつやま(3) などで、少なくとも出撃元の人員不足は主因ではない。実際に `order_start` には繰り返し到達し、将軍一覧では計画の将軍も見えている。停止点は出撃確認の携行札である。

## fingerprint / occurrence の式照合と再計算結果

現行 `src/docich/hanjuku/month_audit.py` の zero_sortie 系 builder では:

- `_incident_id`（L3516-L3518）:
  `sha256(f"{runtime_id}|{generation}|{chapter}|{month}".encode('utf-8'))[:20]`
- `_fingerprint`（L3521-L3523）:
  `sha256(f"{chapter}|{kind}|{'+'.join(signals)}".encode('utf-8'))[:16]`
- `_build_incident`（L3697-L3700）:
  `primary = _primary_signal(signals)` として `_fingerprint(chapter, kind, [primary])` を使う。
- 本件の signals は issue JSON で `['order_start_without_launch', 'monthly_scan_hold', 'battle_grind', 'unread_hold']`。`PRIMARY_SIGNALS`（L267-L295）では `order_start_without_launch` が `departure_failure` に属し、issue の `signature` も `departure_failure` なので、fingerprint の signals 部分は `departure_failure` 1 要素である。

対応する入力 raw は次の通り。

| 項目 | 入力 raw | issue の値 | 独立再計算 |
| --- | --- | --- | --- |
| fingerprint | `8\|zero_sortie\|departure_failure` | `2a53671cf459ccc5` | **未計算** |
| occurrence / incident_id | `g1190-ch1v9\|1190\|8\|8-1` | `d2c3a4099c0ac0b284c2` | **未計算** |

再計算用に次の `python3` 実行を試みたが、本環境では承認却下された。

```sh
python3 - <<'PY'
import hashlib
for raw in ['8|zero_sortie|departure_failure', 'g1190-ch1v9|1190|8|8-1']:
    print(raw, hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20], hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16])
PY
```

したがってハッシュ一致は**未検証**とする。式と入力値は上記の通りなので、検品ゲート側で再計算してほしい。chikujou 系の式は使っていない。

### 検品ゲートによる再計算（2026-11-28 06:47 JST）

現行 main の `month_audit.py` L3516-L3518（`_incident_id`）、L3521-L3523（`_fingerprint`）、L3697-L3700（`_build_incident`、primary を `_primary_signal` で決定）で式を確認し、ゲート側の環境で `python3` により再計算した。

- fingerprint：`sha256("8|zero_sortie|departure_failure")[:16]` = `2a53671cf459ccc5` — **issue の値と一致**。
- occurrence：`sha256("g1190-ch1v9|1190|8|8-1")[:20]` = `d2c3a4099c0ac0b284c2` — **issue の値と一致**。

## order_start 8 / order_launched 0 の経路

### 1. `sortie_kit_empty_cancel` 自体は局所的には正当だが、前段 fallback と矛盾する

現行 `src/docich/hanjuku/flows/deploy.py` の関連経路:

- `_empty_kit_blocks_departure`（L947-L955）: `want` が非空で `carried == []` なら出撃を阻害する。コメントは docich#5353 / オーナー方針「resources before losses」を明示する。
- `sortie_confirm` 処理（L2938-L2953）: 読み取れた確認画面で計画札があるのに実測 kit が空なら、`sortie_kit_empty_cancel` を記録し、`_finish_order(..., 'failed', deviation_reason='carried_kit_empty')` で order を failed にする。
- これは `tests/test_hanjuku_empty_kit_departure.py` で意図どおり固定されている。したがって「読み取れた空 kit を拒否した」こと自体は設計どおりである。

一方で同じファイルの `card_select` 処理（L2713-L2748）は、通常出撃で一覧が読めない場合 `NORMAL_CARD_UNREADABLE_LIMIT` 後に `card_list_unreadable_depart` を記録し、「計画の切り札無しで出撃を続行する」。最初の試行では B で一覧を閉じ、抜けない場合は A で強調行の札を取る。札を選べず空のまま確認へ進めば、後段の empty-kit guard が必ず取消す。

本件では確認画面が「空」と読めている（`sortie_kit_empty_cancel` が 7 件ある）ため、blind approve に逃げず guard が発火した。つまり、**一覧を読めないことへの fallback と、空携行を拒否する guard が、同じ入力状態で互いに打ち消し合っている**。局所設計はどちらも正当だが、統合後の出撃成功率が 0 になるのは実欠陥である。

### 2. `card_select` を読めなかった直接原因はログ欠落で特定できないが、 reader は厳格で失敗しやすい

`_measured_card_select`（`src/docich/hanjuku/os_layer/readers.py` L1112-L1209）は、ヘッダ `きりふだセレクト`、残り枠、説明文、4 行までのカード名・数量、余分な文字がないこと、カーソル位置まで全て一致した場合だけ inventory を返す。1 つの UNKNOWN セル、行ずれ、数量桁の読み違い、追加文字、カーソル不一致でも `None` になる。

本件の decision log は残っていないため、ながはまの一覧でどのセルが読めなかったか、本当に空一覧だったのか、OCR 構造不一致だったのかは特定できない。issue の抜粋にも `card_pick` / `card_dropped` / `card_zero_at_start` / `card_stock` が無く、この点は未決である。

有力な未確認要因として、ながはまの城スプライトが右側出撃パネルを毎フレーム遮る既知問題がある（`tests/test_hanjuku_sortie_panel_occlusion.py` は sortie_confirm について固定している）。本件の全 attack が ながはま発なので、同じ遮蔽が card_select や確認の携行欄へも効いていた可能性はある。ただし card_select への遮蔽許容を示す証拠は現行コードにも issue にも無く、frame が無いため断定しない。

ただし結論には依存しない。実際に正数在庫があったかどうかにかかわらず、現行コードでは「一覧 unreadable → 空で進む → 空だから取消し」が deterministic に成立する。`card_census_wait` が 10 件あり `card_census_start` / 完了が無いことから、全所持の台帳再読も当月内には終わっていない。台帳が未確定のままでは `_stock_cards` は stock unknown を「空の証拠」とみなさず暫定札を残す（`src/docich/hanjuku/strategy/replan.py` L1018-L1064）ため、非空計画が作られ続ける。全所持が 0 と読めていれば計画を空にできた可能性、正数在庫が読めていれば実在札を選べた可能性があるが、issue の証拠だけではどちらとも断定しない。

### 3. `sortie_hold_abandoned` は終了させる欠陥ではなく、bounded backout。ただし文言は実態より広い

`_hold_deploy`（`src/docich/hanjuku/flows/deploy.py` L958-L987）は、出撃系画面の保留を step ごとに数え、`DEPLOY_HOLD_LIMIT = 12` を超えると `sortie_hold_abandoned` を記録して B/B で抜ける。`_hold_guarded_sortie`（L1892-L1909）は通常 step を failed ではなく pending に戻し、active を解放する。つまりこれ自体は月を永久占有しないための bounded cancel であり、設計方向は正当である。

本件の表示行は「出撃画面を分類できないまま」とあるが、issue JSON の `screen_kind` は `sortie_confirm` で、直前の `situation_held` 理由は「携行切り札の読取が不確実または計画と不一致」である。画面種別が未知だったわけではなく、**known な `sortie_confirm` で kit 不一致/読取不確実の保留が上限に達した**のが実態である。`_hold_deploy` は card_select・actor guard・kit mismatch を同じ counter に入れるため、ログ文言は実態より広い。

また `deploy_holds` は `_flow` 領域の step ごとのカウンタで、`_release_stale_failures` の `RELEASE_KEYS`（`src/docich/hanjuku/strategy/replan.py` L3083-L3085）には含まれない。issue の抜粋では初回確認直後に abandon しているように見えるが、これは過去/前段の保留カウントを引き継いだ可能性と整合する。完全ログが無いため確定はしないが、少なくとも abandon は発射を直接止めた主因ではなく、その後の empty-kit cancel が terminal failure である。

### 4. `plan_step_rearmed` は bounded retry として意図どおりだが、波全体の blocking は解除しない

`_release_stale_failures`（`src/docich/hanjuku/strategy/replan.py` L2641-L2699）は、失敗した非ボス step を `NONBOSS_STEP_REARM_LIMIT = 3` / step / month で pending に戻す。恒久的に failed のまま唯一の進路が止まる事態を避けるための意図的機構である。

本件ではこれ自体は誤動作ではない。問題は、7 件の wave order が同じ携行札条件で同じ失敗をする点にある。step ごとに上限があっても、波全体では最大 7 倍の試行が同じ月に入り、翌月にはリセットされる。さらに wave 生成では `cards` を先に一度だけ計算し、追加 wave order へ同じリストを入れる（`replan.py` L1951-L1984）。異なる将軍の同時波としては同種札を複数隊に配れるとは限らず、在庫未読では特に過大な非空計画になりやすい。この複製が empty-kit guard と組み合わさり、失敗の面を広げている。

### 5. 月次処理・戦闘は時間を消費したが、発射ゼロの根因ではない

issue の signals には `monthly_scan_hold` / `battle_grind` / `unread_hold` もある。counts では月次処理（`month_plan`/`month_done`/`month_sub_open`/`post_month_*`）、築城、商人、戦闘（`battles.decisions` 31）が存在し、197 秒の短い月では自由時間を削った可能性はある。`house_deferred_sortie` 8 件は、少なくとも order_start 後は launch priority が家来/状態探索を遅らせたことを示す。

ただし、月次処理は当月内に `post_month_sequence_finished` まで完了している。さらに翌 5 分窓でも同じ card 空kit 取消しが 3 回続き、ch8 `8-3` の再発でも回復していない。月次処理や戦闘がなければ多少早く試行できた可能性はあるが、各試行の終端が出撃承認ではなく empty-kit cancel である以上、時間不足は主因ではない。副次因・時間圧力としては認めるが、(b) の根拠にはならない。

## order_start 8 の内訳について分かること / 未決の点

issue JSON の `decisions.before` は末尾 30 件だけなので、8 件すべての `order_start` 行は保存されていない。確実に言えるのは次の範囲である。

- 計画表の attack 候補は 7 件で、全員が ながはま→しまばら の波状攻撃であり、月末には全件 failed。
- counts 上、`card_list_unreadable_depart` / `sortie_hold_abandoned` / `sortie_kit_empty_cancel` / `order_failed` / `plan_step_rearmed` は各 7 件。つまり 7 件の attack が同じ終端（空kit取消し）へ進んだ。
- `order_start` だけ 8 件なので、少なくとも 1 件は「rearm なしの当月開始」または「bounded backout 後の再入/前月からの継続に相当する余分な開始」を含む。抜粋ログでは `sortie_hold_abandoned` 後に新たな `order_start` が見えないまま同じ step が card/select へ戻るため、8 件目の正確な step は issue 内証拠だけでは特定できない。

この未決点は結論を変えない。8 件目がどの step でも、発射 0 の terminal は empty-kit cancel であり、翌月・翌々月に同型が続いた事実は変わらない。

## VERIFY-5759 との異同

VERIFY-5759（docich#5759、結論 **(b) 設計どおり**）は、ch2 `4-12` で `order_start` 0 / `order_launched` 0。全保有城の自由将軍が 1 名以下で、最後の守備将軍を空にしないオーナー決定により合法な出撃源が無かった。翌窓では募集/加入後に `order_start` 1 / `order_launched` 1 / 出撃 1 へ回復していた。つまり「開始可能な出撃が存在しない待機」だった。

本件は逆に `order_start` に 8 回到達し、出撃元 ながはま の在城も十分で、将軍一覧でも計画の将軍を読めている。blocking は出撃元の人員ではなく、携行札の読み取り・計画・承認 guard の統合矛盾である。翌窓でも回復せず、ch8 `8-3` で再発している。`monthly_scan_hold` が primary 候補の表示を補助する点は共通するが、どちらの件でも月次処理の存在自体は主因を証明しない。#5759 と同じ (b) にはできない。

## 他の判定を採らない理由

- **(b) 設計どおりではない**: 空 kit 拒否は設計どおりだが、unreadable-card fallback がその拒否を deterministic に踏み、bounded rearm が翌月も同じ失敗を再生する。結果として複数月にわたり出撃 0 になるのは、個々のポリシーの意図を超えた統合欠陥である。
- **(c) 誤検知ではない**: 月初から完全観測で、敵向け確定出撃・発射・`order_launched` が 0 だったのは事実。`order_start` 後に発射しない signature も事実に合う。
- **(d) 判定不能ではない**: 完全 decision log が無く、card_select が読めなかった画面単位の直接原因や 8 件目の step は不明だが、issue 内の counts / plan / after 窓と現行コードだけで、発射に至らない統合経路は特定できる。

## 残る未決事項

- ながはまの card_select でどの glyph/layout が unreadable だったか、実在庫が空だったのか台帳だけが未知だったのかは不明。本番 decision log / frames が必要だが、指示どおりローテーション済みとして探していない。
- `order_start` 8 件目の正確な step は issue の末尾 30 件だけでは特定できない。
- `card_census` が空き map で開始できなかった正確な blocker 内訳は、issue の表が `card_census_wait` の observed_metric を省略しているため不明。ただし census が完了していれば計画カードを修正できた可能性はあり、副次要因として残る。
- 現行 main でもこの統合矛盾は残っている。修正方針は本検証の範囲外だが、少なくとも「`card_list_unreadable_depart` で選べず空のまま進んだ通常出撃を empty-kit guard で同じ step のまま failed にする」のか、「一覧 unreadable のときは空計画として承認する/計画を空に正す」のかはオーナー判断が必要である。
