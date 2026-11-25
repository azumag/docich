# VERIFY-5570 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: イッテツーン (ch11 ネペンテ)

- 対象: [docich#5570](https://github.com/azumag/docich/issues/5570) / 監査 `card_miss`（confidence `high`）
- fingerprint `0c054324bdd79993` / occurrence `3cdb7fcc435e3145f7a1`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1504-ch10-approach`
- ch11 / 月 13-2 / 未使用の携行札: **イッテツーン**（携行札候補: イッテツーン, イッテツーン）
- 戦闘: シュゼット vs エダム / 城: ネペンテ / side: attack / 時刻 08:35:15Z
- 画面 `egg_battle_menu` / scrolls `0` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `a3970ddc754e8e70a7c6d3f1772d77bc4ddcc804`（`BOT_VERSION` `hanjuku-chart-v1521-stall-60s-issue`）
- 観測版の静的照合: `hanjuku-chart-v1504-ch10-approach` は commit `64d5112a1e05c2f15e4159b7b9c623150e9254e8` の marker。`git show 64d5112a1:src/docich/hanjuku/flows/battle_menus.py` / `git show 64d5112a1:src/docich/hanjuku/month_audit.py` で確認した。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 (`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`) は VM 側のため直接参照せず、issue 本文・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 設計どおり・既存修正でカバー済み — 観測版 v1504 では scrolls=0 の凍結 `card_flow` 型の実欠陥だったが、現行 main では PR #5619 / v1520 が本経路をカバーする**

本件は監査誤検知ではない。v1504 のコードでは、召喚戦メニューへ置き換わった後に進行不能な `card_flow` が真値で残ると、`_summon_card_row_hidden` が残り携行札の揭示スクロールを返さず、reopen ゲートも閉じたまま `こうげき` を確定できた。issue の値はその経路と一致する。

ただし現行 `origin/main` には PR #5619（squash commit `f2409ad08fd74c0e698396a8b7890f69c7b10d19`、`hanjuku-chart-v1520-summon-card-flow-hold`）が ancestor として入っている。現行コードは `read_kit` flow を即座に fail-closed 解放し、`announce` / `selection_planned` flow が召喚メニューで滞留している間は `return []` で攻撃を保留し、3観測で `_card_use_unclassified` へ解放して同じ step から揭示スクロールへ戻る。したがって、v1504 で本件を起票した経路は現行 main では再現しない。新規実装は不要であり、検証記録のみを残す。

## 1. issue 本文の値と hash の照合

issue 本文の画面・scrolls・cursorY・携行札候補・卵状態・表示文言は依頼文どおりであり、API から取得した本文でも一致した。

- fingerprint raw: `11|card_miss|イッテツーン+ネペンテ`
  - sha256 先頭16: `0c054324bdd79993` — issue と一致
- 時刻 2026-11-25 08:35:15Z は epoch `1795595715`。
- occurrence raw: `g1190-ch1v9|11|1795595715|イッテツーン`
  - sha256 先頭20: `3cdb7fcc435e3145f7a1` — issue と一致

`empty_egg=false` / `summon_egg_unavailable=false` は「卵が使える」という肯定証拠ではなく、卵空・召喚不可の measured evidence が無いという意味に過ぎない。issue 本文の「卵なし・奥の手不可」は card_miss の固定テンプレート文であり、本検証では measured evidence として扱わない。もっとも、後述の最終分岐は卵状態に依存せず `egg_battle_menu` + `こうげき` 文言で reveal を起動するため、この false 値は結論を変えない。

## 2. `scrolls: 0` の亜型判定

本件は **#5556/#5558/#5564 と同じ scrolls=0 の凍結 `card_flow` 亜型**で、#5600/#5560/#5614/#5617 の scroll 枯渇型ではない。

| グループ | 代表 issue | 共通証拠 | 切り分け |
| --- | --- | --- | --- |
| 凍結 `card_flow` 亜型 | #5556 / #5558 / #5564 / 本件 | `egg_battle_menu` / `scrolls: 0` / `cursorY: 176` / `こうげきもうこうげき` | reveal counter が開札後の 0。scroll 枯渇ではない |
| scroll 枯渇型 | #5600 / #5560 / #5614 / #5617 | `scrolls: 3` / 同じく `cursorY: 176` | 上限3回の揭示 down を消費したが `きりふだ` 行を読めなかった型 |

v1504 での `scrolls` の意味は次の通りである。

1. `month_audit._bounded_int` は int だけを保持する。未設定なら issue では `不明` と描画され、明示の `0` は実際に `battle['summon_card_scroll']` が int 0 だったことを意味する。
2. `_summon_card_row_hidden` が true を返すと、揭示 down のたびに counter は 1, 2, 3 へ進む。
3. v1504 では counter を 0 へ戻す経路は、実際に `きりふだ` 行へ到達して `battle_summon_card_open` を記録したときだけである（`battle_menus.py` v1504 L3212）。
4. したがって `scrolls=0` は「一度も scroll しなかった」でも「scroll し尽くした」でもなく、**召喚戦で一度切り札を開いた後に箱が先頭ページへ戻った post-open 状態**を示す。

## 3. v1504 で攻撃へ進んだ機構

v1504 の `egg_battle_step` では、attack 分岐先頭の `interrupted_flow` 救済が次の条件だけだった（v1504 L3138-L3153）。

- `stage in ('menu', 'down', 'list')`
- `selection_planned` が false
- `read_kit` が false

この条件に合う未選択 flow だけ `summon_card_resume` へ退避して `card_flow=None` にする。逆に、次の flow は v1504 では解放されない。

- `read_kit=true` の防衛用 read-only kit probe
- `stage='announce'` の選択済み・消費未確定 flow
- `selection_planned=true` の flow

一方 `_summon_card_row_hidden` の先頭は `not cards_left or battle.get('card_flow') or 'きりふだ' in screen.text` なら false を返す（v1504 L2844-L2845）。本件の表示文言には `きりふだ` が無く、`cards_left` は2件ある。画面種別は `egg_battle_menu` で文言は `こうげき` を含むため、`card_flow` が無ければ最終分岐で hidden=true となり reveal scroll へ進むはずである。それでも scroll せず `こうげき` が確定した以上、emit 時点には真値の `card_flow` が残っていたとコード上判断できる。

emit は `move == 'here' and label == 'こうげき'` の実測カーソル一致後にだけ行われ、条件は `missed` 非空、`cards_left` 非空、`cards_used` 空、1戦闘1件未記録である（v1504 L3335-L3361）。`cursorY=176` はこの `こうげき` 行一致と整合する。

携行札候補が `イッテツーン, イッテツーン` のまま残っている点も重要である。`cards_left` は `cards_selected` / `cards_used` / `cards_unclassified` / `cards_missing` を名前で差し引くため、少なくとも emit 時点では **イッテツーン自体は1枚も選択済みにも使用済みにもなっていなかった**。したがって残っていた blocker は、先に開いた別札の選択済み flow、または `read_kit` probe のいずれかだった可能性が高い。v1504 は `card_flow` を監査 issue へ投影していないため具体名は確定できないが、どちらも現行 PR #5619 の対象内である。

## 4. 現行 main での検知機構・経路の差分

v1504 `64d5112a1` から現行 `origin/main` までの該当差分を確認した。

- card_miss の検知条件は変わっていない。現行でも `move == 'here' and label == 'こうげき'`、`missed`、`cards_left`、`cards_used` 空、1戦闘1件の条件で `battle_boss_card_missed` を emit する。
- fingerprint / occurrence の計算式は変わっていない。現行 `_track_card_miss` も runtime/chapter/int(at)/card と `[card, castle]` を使う。
- `_summon_card_row_hidden` の判定本体は変わっていない。`card_flow` が真値なら揭示を返さない構造自体は残る。
- `battle_boss_card_missed` の emit には `card_flow`（card/stage/selection_planned）の証拠投影が追加された。`month_audit` も `_bounded_card_flow` で保持し、issue 本文へ `card_flow:` 行を描画する。これは診断性の追加であり、検知条件の変更ではない。
- scroll 枯渇型には PR #5615 / v1515（commit `8b7fd9232e0604ffd857f4690c6a5e22dcfc2cc3`）の `_summon_card_rearm` が入っている。これは `scrolls>=3` の再提示で、本件の `scrolls=0` 型を直接治すものではない。
- 凍結 flow には PR #5619 / v1520（commit `f2409ad08fd74c0e698396a8b7890f69c7b10d19`）が入っている。両 commit とも現行 `origin/main` の ancestor である。

## 5. PR #5619 が本件経路をカバーする理由

現行 `origin/main` の `egg_battle_step` では、attack 分岐の先頭で次の順に処理する。

1. `read_kit` flow は `_defense_kit_read_unread` で `read_done=True` + `card_flow=None` に閉じる。これにより v1504 で救済対象外だった read-only probe は即座に揭示経路へ戻る。
2. 未選択の `menu` / `down` / `list` flow は従来どおり `summon_card_resume` へ退避してクリアする。
3. `stage='announce'` または `selection_planned=true` の flow は、`SUMMON_CARD_FLOW_STALL_LIMIT=3` まで入力を保留する。保留中は `return []` であり、`こうげき` 移動・確定も `battle_boss_card_missed` emit も起きない。
4. 3観測目に `_card_use_unclassified` で `cards_unclassified` へ計上し `card_flow=None` に解放する。同じ step の後段で `_summon_card_row_hidden` が true を返し、残り札の揭示 down が再開する。
5. `きりふだ` 行が読めれば reopen ゲートが残り `cards_left` から札を開く。

この挙動は現行 main の回帰テストでも固定されている。ローカルで次を実行し、2件とも pass した。

```text
PYTHONPATH=src:$HOME/py311shim $HOME/.pyenv/shims/python3 -m pytest -q \
  tests/test_hanjuku_boss_egg_row_log.py \
  -k 'frozen_announce_flow_holds_attack_then_releases or interrupted_defense_kit_read_is_closed_before_reveal'
# 2 passed, 16 deselected
```

`test_a_frozen_announce_flow_holds_attack_then_releases` は、`summon_card_scroll=0`、選択済み `announce` flow、残り札ありという本件と同じ構造で、2観測は攻撃保留・3観測目に解放・次に残り札を開くことを確認する。`test_an_interrupted_defense_kit_read_is_closed_before_reveal` は `read_kit` blocker の解放と即時 reveal を確認する。v1504 で issue に投影されなかった blocker が announce 系でも read_kit でも、現行 main の対象経路である。

## 6. 判定できない範囲

- VM 側の生 decisions / frames を参照していないため、実際に最初に開いた札名、凍結 flow の `stage`、その直前の画面遷移フレームは確定できない。v1504 の issue は `card_flow` を描画しないため、issue 単体でも確定できない。
- ただしこの未確定性は結論を変えない。`scrolls=0`・表示文言・残り札・攻撃確定の組み合わせから v1504 での実欠陥発火は説明でき、コード上到達可能な blocker 種別は現行 PR #5619 がカバーする。
- 本検証は現行 main の静的カバー判定である。実機 runtime で v1520 以降の同一局面を再観測したわけではない。

## 検証メモ

- 実行した確認: issue API 本文照合、sha256 再計算、`git show 64d5112a1:...` による観測版確認、`git diff 64d5112a1..origin/main`、PR #5619 の merge/ancestor 確認、関連 issue の scrolls 値比較、現行回帰テスト2件の実行。
- ドキュメントのみの追加であり、コード変更・本番 SSH・ゲーム入力は行っていない。
- `agent-guards/secret_scan.py` はこの環境に未配置。投稿前に `ops/vm_actions/secret_scan.py` で本ファイルと投稿文を走査し、clean を確認する。
