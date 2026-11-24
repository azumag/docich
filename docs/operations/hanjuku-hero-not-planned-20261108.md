# 主人公を出撃計画の主体にしない（v522、2026-11-08）

## 事象（オーナー指摘）

> しゅじんこうはアルマムーンから実質動けない設定になってるはずだが、出撃計画が立つのはおかしい

g1153 2026-11-08 17:55:42 JST、調整チャート `A:1de5438c:J2` が
`general=どうし / source=アルマムーン / target=四季王城(南西)` として採用され、
実況も「どうし将軍をアルマムーンから四季王城(南西)城へ向かわせる計画」と読んだ。
しかし実行は 17:56:00 に `hero_priority_selected`（デュオニソス、deviation
「計画のどうしに代わりデュオニソスを出撃させる」）で ネクスドリア へ飛び、
17:56:34 にデュオニソスが 四季王城(南西) へ出撃した。
計画の主体（どうし）も出撃元（アルマムーン）も実行と一致していなかった。

## 原因

- `deploy` は `order['general'] == NAME` の注文を必ず代役で答える
  （`_order_hero_alternatives` → `general_override`）。ボス注文は
  `_boss_companion_source` が出撃元も選び直す。つまり主人公は実運用で
  アルマムーンから動かない。
- 一方、計画の検証 `chart_adjust.startable()` は主人公を特別扱いせず、
  駐留に記録されていれば採用していた。LLM調整チャートと基準チャートの
  主人公注文がそのまま「計画」として立ち、実況もそれを読み上げていた。
- 機械計画の中継（`replan._interim_source`）も、基準チャートの主人公注文を
  そのまま `general=どうし` で複製しうる状態だった。

## 変更（v522 `hanjuku-chart-v522-hero-not-planned`）

- `chart_adjust.startable()`: `general == chart.HERO` は
  `hero_not_dispatchable` として採用しない。調整チャートの注文は保存時に
  落ち、主人公を名指しした回答だけなら `no startable order` として破棄される。
- チャート生成プロンプト（`hanjuku_chart_worker`）: 「主人公（どうし）は
  出撃させない。general に どうし を選ばず、主人公の城から出す場合も他の
  駐留将軍を general にすること。代役の名前を推測せず、その城に記録された
  将軍がいなければその指示自体を出さない」を追加。
- 実況（`hanjuku_commentary`）: 主人公が主体の `order_start` は、主人公や
  出撃元を約束しない。「主人公は動かさず、〇〇城へは代わりの将軍を
  向かわせる計画です。持たせる切り札は…」と読む。
- 機械計画の中継（`replan._interim_source`）: 基準チャートの主人公注文を
  中継に複製するときは、その出撃元で実測した余剰将軍を主体にする
  （余剰が無ければ実一覧に選ばせる）。主人公を主体にした中継は作らない。
- 版番号と診断allowlistを v522 へ更新（同じ日の v521 は召喚獣メニューの修正が使用）。

## 変えないもの

- 基準チャートの注文データ（`1-A1` など）は変更しない。実行時の代役経路
  （`deploy`）と、ボス注文で出撃元を選び直す挙動も変更しない。
- `HERO_CASTLE_GUARD`（主人公の城に2人残す）と「城を空にしない」規則。

## 検証

- `tests/test_hanjuku_confirmed_source_actor.py`: 主人公は
  `hero_not_dispatchable`、主人公注文の中継は実測の余剰将軍になる。
- `tests/test_hanjuku_home_label.py`: 通常将軍の実況は従来どおり出撃元の
  実名（アルマムーン）を読む。主人公注文は主人公を主体に読まない。
- `tests/test_hanjuku_chart_adjust.py`: 主人公を名指しした調整チャートは
  保存されない（`adjusted_orders_dropped` に `hero_not_dispatchable`）。
- 関連する半熟の計画・ボス・中継・実況テスト（chart_adjust / chart_bot /
  boss_* / off_chart / plan_* / commentary / script など）を実行して緑。

## 未確認・残件

- 実ランでの確認は未実施。デプロイ後の次の調整チャート要求で、
  `adjusted_orders_dropped` に `hero_not_dispatchable` が出ること、
  実況が「主人公は動かさず…」に変わることを実測する。
- 基準チャートの主人公注文（`4-B1`〜`4-B4` など）は計画上は今も
  `general=どうし` のまま実行経路に渡る。実況と採用計画からは主人公が
  消えるが、判断記録 `order_start` の `general` は実行時の実測（代役確定前）
  なので、必要なら次の段階で記録側も代役確定後に更新する。
- 試合後の振り返り（`summarize_recap`）は記録に残った `order_start` を
  そのまま読むため、基準チャートの主人公注文があった回は「作戦では
  どうし将軍を…」と言いうる。これは事後の記録説明であり、立てる計画では
  ない。実ランで気になる場合は同じ規則を振り返り側へ広げる。
