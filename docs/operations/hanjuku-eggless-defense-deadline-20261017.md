# 半熟英雄: 卵なし城防衛の召喚前救済期限を実測チャンクで有界化（2026-10-17）

Issue: [azumag/docich#2040](https://github.com/azumag/docich/issues/2040)

## オーナーの目的

2026-10-11 のロックフォール防衛敗北（g684-f0eaef20 / v151）の follow-up。実メニューで自軍の卵・切り札・退却が使えず奥の手だけが有効な城防衛で、敵の召喚が始まる前に救済を開く。単純なHP閾値の前倒しではなく、実測した敵HPの減少幅で期限を有界化する。奥の手の自傷、主人公、他の資源、敵卵なしの否定条件を分けて検証し、健全な通常戦へ無条件の賭けを足さない。

## コードで確認した問題

`src/docich/hanjuku/judgment/battle.py` の `_defender_egg_window` は、防衛戦の敵が `HP < 話数+3` で卵を使う規則（gcgx ai.html、城攻撃時の敵は思考1）に対し、`open_at_hp = 話数+2 + 4 + max(4, 直近の観測間ダメージ)` を期限としていた。margin に入るのは**連続する2観測の差だけ**で、確定した開幕パネルがすでに最大HPを下回っている分（開幕の交戦で失った分）は使っていなかった。

g684 の実測では chapter 1・アルテミス（最大HP49）で期限が `1+2+8 = 11`。最初の確定パネルが敵HP19、次の観測で敵HP5（14減）となり、期限を1観測で飛び越えた。`due` になった観測では敵はすでに「たまごをつかう」を選んでおり、召喚アニメーションを味方奥の手メニュー未読の待機として8観測消費した。ドラゴンフライ HP263 の召喚後、使える卵・札がないロックフォールは通常攻撃で敗北した（HP0対1、general_loss は unclassified、死亡は未確認）。

## 変更

`_defender_egg_window` の margin に、確定した開幕パネルで既に観測している敵HPの減少分を加える。連続観測の差と同じ種類の実測値であり、「この敵が自らの壁トリガへどれだけ近いか」を表す。

```
opening_drop  = enemy_max - enemy_start        # 確定開幕パネルで観測済みの減少分
measured_burst = max(observed_drop, opening_drop)
margin        = EGG_WALL_DAMAGE + max(EGG_WALL_DAMAGE, measured_burst)
```

- g684: `opening_drop = 49-19 = 30`、`margin = 34`、`open_at_hp = 37` となり、敵HP19のパネルで `due`。
- 満HPで読めた敵: `opening_drop = 0` なので期限は従来のまま（chapter 1 なら `open_at_hp = 11`）。
- `observed_metric` に `opening_hp_drop` と `measured_hp_burst` を追加し、期限が動いた根拠を診断で区別できるようにする。既存の `observed_hp_drop` / `input_margin_hp` / `open_at_hp` は意味を変えない。

「卵なし単独守備」の根拠は既存の `okunote_only_observed`（実メニューで `たまごをつかう` / `きりふだ` / `たいきゃく` が無く、`おくのて` だけが有効という観測）をそのまま使う。将軍メタデータは実メニューの代わりにしない（既存方針どおり）。

## 否定条件

- 敵卵なし（`has_egg is False`）と敵卵情報不明: 期限を作らない（既存ガードを維持）。
- 自傷: 奥の手の選択は既存 `okunote_step` の経路を通り、`OKUNOTE_MAX_SELF_DAMAGE = 88` と候補順（全候補が自傷なら最小被害）を変更しない。
- 主人公: v131 のとおり防衛の召喚前救済は主人公も対象。主人公をこの期限から外す変更はしない。保護は候補順と既存のHP判定で行う。
- 他の資源: 実メニューで卵・札・退却が使える場合は `okunote_only_observed` が立たず発火しない（既存の解除経路も維持）。
- 位置: `clash_position_measured is False` と `clash_internal_x_threshold = 話数*4+32` を維持し、接触位置を校正済みとは主張しない。
- 有界性: `DEFENDER_EGG_OPEN_LIMIT = 3` を増やさない。`melee_forced` も増やさない。

## 検証

`tests/test_hanjuku_defender_egg_preempt.py` に、g684 相当の期限再現、満HP敵の期限不変、他の資源・敵卵なし・ボス・攻撃側・未読城では発火しないこと、主人公の救済と最小被害候補、開封上限と位置未校正の維持を追加した。合成パネルと保存stateのテストであり、自然ランの勝利や死亡回避の証明ではない。

`BOT_VERSION` を `hanjuku-chart-v231-eggless-defense-deadline` へ上げ、`ops/vm_actions/collect_diagnostics.py` の固定版許可リストと `docs/hanjuku_script_bot.md` を追従させた。新worker/queue/provider・入力経路・本番操作は追加しない。

## 反映と残る受入条件

protected main への通常PR、必須CI、owner-only VM operations の既存配備、観測単位のホットロードを使う。ゲーム・配信・共通基盤を再起動しない。

**未達**: Issue #2040 の受入条件である native 画面の再現回帰（召喚直前の実メニュー、敵 caption/召喚アニメーション、B の decision_id / input_sent 対応、後続画面）は原証拠待ちで未実施。原証拠は次の自然発火で取得し、`Hanjuku completed evidence export`（Issue #1339 経路）で取り出してから、この変更の実機受入と (b) 敵召喚開始の識別を別工程で行う。

この作業環境では非公開正本 `DOCICH_HANDOFF_PATH` が未設定のため handoff は未読。作業中バナー・音声は VM/webui の運用経路が無いため未実施（本変更は配信投影に触れない）。
