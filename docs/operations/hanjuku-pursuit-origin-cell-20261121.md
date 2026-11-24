# 半熟英雄: 追撃ジャンプの起点カーソルから将軍の実位置を取る（v1280、2026-11-21）

## 背景

オーナー指摘（2026-11-21）「そもそも全然軌道上に配置できてない」。
v1277 の `unit_world` は SELECT後の map フレームの `mem['cursor']` を使うが、
ボス領域には城の屋根が無くカメラをアンカーできないため `cursor` が None になり、
実測が一度も取れなかった。

## 変更

- `os_layer/navigation._y_jump_step`: 追撃（step=PURSUIT）のYジャンプで、
  **移動前の最初のワールドマップカーソル**を `jump['origin_cursor']` と
  `boss_pursuit['origin_cursor']` に保存する。Yビューのカーソルはフィールド視点の
  中心（＝SELECTで飛んだ将軍）に初期配置されるため、これが将軍の実位置になる。
- `flows/boss_pursuit._redirect_metric`: `origin_cursor` を Y ビューのオフセットで
  セルへ変換し、`unit_world`（実位置）と `unit_to_aim`（狙いとの差）を記録。
- `BOT_VERSION` v1280、hot-load許可リストへ追加。

## 検証

- `tests/test_hanjuku_boss_unit_world.py` に1件追加: `(163.5,93.5)` → `(804,368)`、
  狙い `[770,853]` との差 `[-34,485]`。
- 追撃系・ジャンプ・キー台帳 34 passed。

## 次

- 実機の `unit_to_aim` で、待ち伏せ将軍が軌道セルに居るかを判定する。
  大きく外れていれば、同じ待ち伏せセルへ再いどうする処理を追加する。
