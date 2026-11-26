# VERIFY-5531 — [半熟英雄] 卵と切り札を残して敗北: ch7 レモン（battle_loss_with_resources）

- Issue: azumag/docich#5531。`battle_loss_with_resources`（confidence: high、
  fingerprint `3f08ea563092d0ff` / occurrence `ea9239c83a34363d4194`）。
  初回発行: ch7 7-1、観測 1094回 / 231.7秒、ラン `g1190-ch1v9` /
  generation 1190 / `hanjuku-chart-v1497-boss-audit-live-sortie-defer`。
  以後の同一 fingerprint コメントは再発記録であり、本検証では初回発行を
  incident として扱う。
- 戦闘: レモン vs シフォン @ ロキュータ、side=attack、outcome=loss、
  HP ally 0 / enemy 62。卵残数 4 / 切り札 選択 [`ノリウツール`] 使用 [] 残り
  [`ノリウツール`] / pending None / 退却選択 False。
- 検証 base: 現行 main `757f24fcd`（`hanjuku-chart-v1509-card-at-hp1-audit`）。
  観測版 v1497 は `2fe1b66f8`（`hanjuku-chart-v1497-boss-audit-live-sortie-defer`）。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。read-only 検証であり、
  本番 SSH・本番入力・production code 変更は行っていない。

## 結論: **(c) 誤検知 — 卵は stale 台帳値、切り札は消費未確認の検知限界**

「敗北時に使用可能な卵4回とノリウツール1枚が残っていた」という high confidence の
主張は、incident に内包された証拠では成立しない。卵の `egg_uses=4` は戦闘中の
消費を差し引かない最終観測台帳値で、切り札の `残り ['ノリウツール']` は
「選択済みだが使用確定なし」を used/selected の差分で機械的に残存扱いした値にすぎない。

むしろ決定列には、卵側では味方召喚獣ターンを示す `monster_skill_stall`:3 と、
03:39:09Z の `egg_battle_menu` で「たまごをつかうが使えない表示」へ進んだ記録がある。
`monster_skill_stall` は現行 main `flows/battle_menus.py:3458` の `owner != 'ally'`
早期 return により味方召喚獣ターンでのみ emit される。本 incident で選択された
ノリウツールは召喚札ではないため、これは卵が既に召喚へ使われ、その後に卵行が
灰色・使用済み・未読として白OCRから外れた状態と整合する。#5470 と同型である。
切り札側も `battle_card_selected` / `battle_card_candidate` /
`battle_card_announce_retry` が「消費を確定できない」ことを明示しており、
未消費の live 在庫を直接測ったものではない。

したがってこれは「卵・切り札を使い切る前に退却した」実欠陥ではなく、
観測・監査の検知限界を実残数と読み違えた誤検知である。なお本件は side=attack であり、
#5505 で留保された `summon_egg_unavailable` latch は v1497 / 現行 main とも
`side == 'defense'` のときだけ設定される（v1497 `flows/battle_menus.py:3244-3248`、
現行 main 同所）ため、その live-count 失敗型にも帰属しない。

---

## 1. fingerprint / incident_id の再計算

### fingerprint

現行 main `src/docich/hanjuku/month_audit.py:3312-3314`:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

battle resource incident では `month_audit.py:1509` が
`_fingerprint(chapter, event['kind'], [event['kind']])` を使う。v1497でも同一式
（v1497 `month_audit.py:3169-3171`、builder は :1376）。本件の raw は:

```text
7|battle_loss_with_resources|battle_loss_with_resources
```

Python3 で sha256 を再計算したところ先頭16hexは `3f08ea563092d0ff` で、
issue の fingerprint と一致した。

なお issue の `signals` 配列には `battle_loss_with_resources_with_egg` などが
並ぶが、fingerprint へ渡すのはその配列全体ではなく `[event['kind']]` だけである。
`7|battle_loss_with_resources|battle_loss_with_resources+...` で計算すると別値に
なるが、それは実装の入力ではない。

### incident_id / occurrence

battle resource incident は月次一般の `_incident_id(identity, chapter, month)`
（現行 main `month_audit.py:3307-3309`）を使わず、builder 固有の raw を使う
（現行 main `month_audit.py:1504-1507`、v1497 :1371-1374）:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSONの値を代入した raw は:

```text
g1190-ch1v9|1190|battle_loss_with_resources|egg,card|レモン|A:7da6ec3f:M73131a|1795577959
```

再計算した先頭20hexは `ea9239c83a34363d4194` で、issue の occurrence /
incident_id と一致した。`now` は issue JSONの `created_at`
（`1795577959.5649412`）から整数部を使った。

## 2. 資源使い切り・即死帯例外のコード上の位置づけ

- owner方針の即死帯は `RETREAT_RESOURCE_FLOOR_HP = 4`
  （現行 main `judgment/battle.py:1401`、v1497 :1383）。
  `_retreat_immediate_loss` は `0 < ally_hp <= 4` で切り札待ち・消費遠回りを止める
  （現行 main :2179-2188、v1497 :2038）。
- 一方、HP4より上では `_retreat_cards_to_spend` が未確認切り札へ有界な再試行を与える
  （現行 main :2247-2261、v1497 :2106）。本件の 03:39:14Z
  `battle_cards_before_retreat` はまさにこの経路で、「使用未確認の切り札が残るため、
  退却より先に救済メニューで1回だけ使用を試行 (#4999)」と記録されている。
- 最終HPは0だが、途中HPは issue の decision projection に出力されない。
  ただし `battle_cards_before_retreat` が発火している以上、その時点では
  immediate-loss 例外で遮断されていない。方針どおり先に切り札の使用を試みた後、
  その確認ができないまま戦闘が終わった、と読むのが整合的である。
- 現在は退却自体も一律禁止だが、`battle_hero_retreat_open` は可否確認・救済メニュー
  分岐の古い名前を残す。実際の退却選択は False であり、敗北まで戦っている。

## 3. 卵残数は live count ではない

`battle_result` の metric は現行 main `flows/battle.py:1881-1886`で
`egg_uses: (mem.get('egg_uses') or {}).get(cur.get('ally'))` をそのまま載せる。
v1497も同じ入力（v1497 `flows/battle.py:1796-1801`）である。ここには
戦闘中に卵を選んだ回数や召喚消費の減算は入らない。`_own_egg_positive` の
docstring も「選択や勝利から取得・回数を推測しない」と明示し、選択後は
`egg_recheck` で古い回数を dirty 扱いするだけである（現行 main
`judgment/battle.py:3038-3048`）。

本件では 03:39:09Z、まだ `battle_result` より10秒前の `egg_battle_menu` で、
「たまごをつかうが使えない表示のため卵を諦めてこうげきで応戦する」が記録されている。
この分岐は `action == 'use_egg'` でも白い `たまごをつかう` 行が `screen.text`
に無い場合に入り、灰色・使用済み行は OCR から落ちることを前提に attack へ戻る
（現行 main `flows/battle_menus.py:3116-3147`、v1497 :3103付近）。
白い使用可能行を見ながら選ばない経路ではない。

さらに incident counts には `monster_menu_choice`:15、
`monster_skill_stall`:3 がある。`monster_skill_stall` は owner が `ally`
でなければ emit されない（現行 main `flows/battle_menus.py:3458-3460`）。
カウントだけでは個別行の将軍名までは復元できないが、最後の30行が同じ
`A:7da6ec3f:M73131a` の連続した戦闘であり、選択札が召喚札でないことから、
味方召喚獣＝卵使用済みだった #5470 型と読むのが自然である。

したがって `egg_uses=4` は #5393 / #5318 と同じ stale ledger artifact であり、
#5505 で留保された defense 限定 latch の live-count failure ではない。

## 4. 切り札「残り」は未消費の直接証拠ではない

監査側 `_battle_resources` は `cards_selected` から `cards_used` を引いた差分を
`cards_remaining` とする（現行 main `month_audit.py:1270-1308`）。
ノリウツールは卵落札ではない（`egg_drop: 0`）が、使用告知署名が未校正だと
`cards_used` に入らない。実際の decision 列は:

1. 03:39:13Z `battle_card_selected` — 「消費は未確定」
2. 03:39:13Z `battle_card_candidate` — 「告知署名が未校正のため消費を確定せず画像収集」
3. 03:39:14Z `battle_card_announce_retry` — 「消費を断定せず同じ札をもう一度選択する」
4. 03:39:14Z `battle_cards_before_retreat` — 退却より先に救済メニューで使用試行

であり、少なくとも1回は選択し、消費確認だけができていない。カード効果が実際に
発動したかは issue だけでは確定できないが、「一度も使用を試みなかった」「使用可能な
まま意図的に温存した」という主張は decision 列と矛盾する。

観測版 v1497 では `battle_result` metric に `cards_attempted` / `cards_spend_retry`
がなく、監査の `cards_unconfirmed_only` も `cards_unclassified` と卵落札だけを見る
旧式だった（v1497 `month_audit.py:1277-1279`）。このため、試行済み・消費未確認の
ノリウツールが high confidence の「残存切り札」に化けた。

現行 main では PR #5392（#5198、v1461 marker）により、survival-list の試行を
`battle_result` metric の `cards_attempted` / `cards_spend_retry` へ出し、
`_remaining_cards_are_unconfirmed` が試行済み残存を検知限界として数える
（現行 main `flows/battle.py:1887-1899`、`month_audit.py:1252-1267` / :1410-1412）。
PR #5392 は観測版 v1497 には入っていないが現行 main には入っている。
ただしこれは監査の降格修正であり、本件の卵台帳そのものを live count にするものではない。

## 5. v1497 と現行 main の差分評価

- fingerprint / incident_id の式: v1497 と現行 main で同一。再計算は両方とも一致。
- 卵台帳の意味: 現行 main でも `battle_result` は raw の `mem['egg_uses']` を出す。
  したがって台帳値だけから「敗北時に卵4回が使えた」と結論する根拠は現行 main にもない。
- 切り札の監査: 現行 main は PR #5392 で試行済み・消費未確認を `cards_attempted`
  として残す。同型の新規 incident では、少なくとも切り札側を「未試行の残存資源」と
  読む材料は減っている。
- 戦闘判断: 現行 main には v1505 / v1507 の前倒し切り札判断や v1506/v1509 の
  HP1資源温存監査も入っているが、本件の結論はそれらへの依存ではない。
  本件ではそもそも使用試行と卵召喚の痕跡があり、残留主張を支える live evidence がない。

## 6. 対応方針

結論が (c) のため、ゲーム動作の修正は不要である。再発防止の監査改善候補は、

1. `battle_result` metric に `egg_recheck` / 味方召喚獣ターン有無 / `egg_battle_row_dead`
   の所属を出し、卵の raw ledger と「この戦闘で消費した形跡」を監査へ渡す。
2. 卵のみ、または卵＋未確認切り札の incident では、raw `egg_uses>0` を high confidence の
   live残数として扱わず、少なくとも medium へ降格する。
3. `battle_survival_unavailable` / `situation_held(egg_unavailable)` の labels を
   issue projection へ bounded export し、灰色行 OCR 脱落と実在庫を切り分けられるようにする。

これらは検知精度の follow-up であり、#5531 初回 incident を実欠陥へ翻す根拠ではない。
