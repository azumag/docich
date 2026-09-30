# 採用済みの実行不能チャートからナキューメラ候補を再評価する

SSHの限定読取でactive g514-8937051b / generation514 / readyを再取得した。
修正前のv113、tick9422/9533でナキューメラだけ未攻略、同城の予約なし、
active/house/recallなし。失った城はナキューメラ、未知所在はガルバンゾー。

採用済みchart_planとchart_adjustのrequest_idは双方8a1a091ce57a4e53。
K1はカストーラのココットでナキューメラへ攻撃するが、
after=[captured, ナキューメラ]。K2はall_captured待ち。
現在の状態からどちらも実行不能で、_plan_pendingはfalse。
それでも_off_chartは採用IDが要求IDに一致するだけでreturnし、
interim_wanted=falseを維持していた。これは守備不足によるholdより前の阻害条件。

固定fixtureはその後のtick9646/月3-2の限定実状態。
この時点では通常のhouse scanがstatus段階に進んでいたことも保持した。
旧コードの純粋関数でplan_pending=false、候補retake_1は
本城のヴィーナス→ナキューメラ、move_2はスペンソニア→空のゴーメン。
古い出撃は400観測を超えており予約に数えられない。
本城・スペンソニアの配置は未知で、そこに2人いることは未証明。
fixtureは秘密情報・save・ROM・プロンプトを含まず、採用計画を含むゲーム状態だけ。

v114は停止gateを_plan_pendingだけに限定する。
同じ採用planを二重適用しない条件は既存のadoption条件に残る。
既に保存された矛盾planでも、次の通常off-chart判断から候補を再評価できる。
target攻略後に別部隊が合流する条件として有効な場合もあるため、validator契約は変更しない。
所有・勝利・配置は推測変更しない。house/recallの入力所有も維持する。

回帰は実fixtureのrequest_id一致、既存矛盾plan、候補復活、二重採用なし、
実一覧2人なら守備1人を残せること、1人/読取不能ならAを拒否、
実行可能plan・最近の目標予約なら既存抑止を維持することを確認する。
これは候補と方策の検証であり、実出撃・奪還成功はcanonical配備後に別途確認する。
募集人数の固定6は別PRで扱う。
