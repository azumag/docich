# パスワード画面の掃引後を無期限保留から有界リセットへ (v1353)

## 症状（docich#4993）

ローカルドライバがパスワード画面で停止した。

```
bot=hanjuku-chart-v1351-duel-dim-mash step=1007077 phase=concert screen=password_entry
chapter=11 month=62-5  last=None | log: Map_LoROMMap
```

- `step` が 60 秒間（5 サンプル）不変。
- `/frame.png` が 3 秒あけて 2 回取得してバイト一致（md5 `c28ad9cb…`）。
- 画面は扉前のテキストボックスに「パスワードヲ ジュンバンニ イレテクダサイ。」のみ。
  パーサは `kind=password_entry`、`hand=None`、単語行は読めないグリフ 5 行。

## 原因（コード側の寄与）

`flows/password.py` は語の掃引を `_MAX_CYCLES = 6` 回まで行い、その後は
`return []` で**無期限に保留**する。画面が変わらない限り入力は出ず、
ドライバ側の screen_stalled 終端に落ちるまで停止し続ける。

## 変更

- `_MAX_RESETS = 2` を追加。掃引サイクルを使い切ったら **B を 1 回**送って
  パネルを閉じ、先頭からやり直す（B は「パスワードヲ…」の再タイプで、
  入力済みの進行は消えないと実測手順に記録されている）。
- やり直しの予算（`resets` / `held_recorded`）は再掃引の `st.update(...)` で
  保持する（旧 `st.clear()` は予算まで消していた）。
- 予算を使い切ったら `situation_held` を**1 回だけ**記録して保留する
  （B 連打にはしない）。
- 掃引上限の境界でも、保留の前にまずリセットを打つ（保留の空観測を挟まない）。

## 変えないこと

- 語の掃引そのもの（先頭まで up → A+down を `_SWEEP_PICKS` 回）。
- `_TOP_PRESSES` / `_SWEEP_PICKS` / `_MAX_CYCLES` の値。
- 失敗時の再入力の考え方（順序は問われない）。

## 検証

- `tests/test_hanjuku_password_sweep_limits.py` を新契約へ更新
  （境界で即 B、予算は掃引をまたいで保持、2 回で打ち止め、以降は
  記録付きの保留 1 回だけ）。
- `tests/test_hanjuku_password_sweep.py` / `tests/test_hanjuku_password_entry.py`
  と合わせて **7 passed**。

## 残件（コード外）

ドライバ自体の停止（step が進まない）は bot 側からは検出・復旧できない。
`wiki/Hanjuku-Local-Driver-Viewer.md` の定期監視に「step が N 秒進まない」を
加え、外部 watchdog で SIGTERM→再起動できるようにするのは follow-up。
