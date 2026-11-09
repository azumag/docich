# 半熟英雄: ボス戦の読めない画面で固まらない（v557、2026-11-09）

## 症状

g1163 第6話 2026-11-09 13:09:40 JST。第6話ボス（完熟秘密基地／ハードマン HP456）へ
デーメーテールが突入しましたが、`egg_battle_menu` で「たまごをつかうが使えない」→
こうげきへ戻した直後から **13:12:44〜13:16:37 の約4分間**、読めない画面で
`situation_held` を繰り返し、**一度も入力を送れず**に戦闘が成立しないまま
`battle_entry_expired`（管理画面へ戻る）になりました。

## 原因

既存の `unknown_hold_advance` は「**同一 digest** の読めないフレームが
`UNKNOWN_HOLD_ADVANCE`(6) 回続いたら A を1回」でしたが、

1. 戦闘画面はアニメーションするため digest が毎回変わり、6回の同一判定に届かない。
2. さらに `phase` が `title_or_intro` 等のときは、この救済が丸ごと無効だった。

## 修正（v557 `hanjuku-chart-v557-battle-unknown-advance`）

- 戦闘中（`mem['battle']` がある）は、**digest が変わっても** 連続する読めないフレームが
  `UNKNOWN_BATTLE_ADVANCE`(10) 回に達したら A を1回だけ送る（`unknown_hold_advance`）。
  カウンタは送信でリセット。
- `phase` が `transition`/`title`/`title_or_intro` でも、**戦闘中なら**同じ救済を通す。
- 戦闘外の挙動（同一 digest 6回）は従来どおり。

## 確認

- `tests/test_hanjuku_unknown_hold.py` に2件追加: アニメーションする戦闘フレームでも
  10回で1回だけ A、戦闘外の変化する画面は押さない。
- 保留・チャート・出撃 443件 passed。
- 本番反映後: ボス突入時に `unknown_hold_advance(battle: true)` が出て、
  戦闘が数分で固まらず進むことを実測する。
