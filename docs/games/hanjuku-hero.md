# 半熟英雄 (SFC) を docich で動かす

半熟英雄 (SFC) は docich の `retroarch` アダプタ (RetroArch + snes9x コア) で動かす。
本書は ROM 配置からの起動手順とトラブルシュートをまとめる。設計の背景は
`docs/architecture.md` §4.1 / §9 を参照。

## 表記ルール

- 【確認済】: 一次情報 (パッケージリポジトリ・`docs/architecture.md`・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 1. 準備: ROM の配置

1. 自己吸い出しした ROM ファイルを `games/roms/hanjuku-hero.sfc` に置く。
   - docich は ROM の取得・配布に一切関与しない。著作権ポリシーは `games/roms/README.md` を参照。
   - ファイル名は `config/games/hanjuku-hero.toml` の `[retroarch] rom` と一致させる
     (既定値は `games/roms/hanjuku-hero.sfc`)。
   - 「リポジトリ外」はGit追跡対象外の意味。配置先は変更しない。
     VMでは `/home/ubuntu/docich/games/roms/hanjuku-hero.sfc` を前提にする。
     `.gitignore` 対象であり、ROM本体をcommit・Git bundle・PRへ含めない。
2. `config/games/hanjuku-hero.toml` の内容を確認する (既定値):

   ```toml
   [game]
   name = "hanjuku-hero"
   title = "半熟英雄 (SFC)"
   adapter = "retroarch"

   [retroarch]
   rom = "games/roms/hanjuku-hero.sfc"
   core = "auto"

   [agent]
   enabled = false     # 認証・費用・実機ゲートの確認まで維持
   brain = "command"
   command = ["python3", "brains/hanjuku/brain.py"]
   interval_ms = 7000

   [lifecycle]
   require_round_boundary = true
   boundary_timeout_s = 300
   ```

---

## 2. 起動

本変更はオフライン実装段階。起動は §9 のゲート確認後、別途許可された検証環境で行う。
`start hanjuku-hero` は `[display] viewport_width/height` が未設定ならpreflightで拒否する。
配信と同じ配置を検証する設定は `viewport_x=0`, `viewport_y=90`,
`viewport_width=960`, `viewport_height=540`。共通displayはこの矩形を内包する必要がある。
既存配信へ接続する場合、ゲーム起動のために共通display・配信serviceを再起動しない。

---

## 3. コア選択 (`core = "auto"`)

`core = "auto"` の場合、`/usr/lib/*/libretro/` を以下の優先順位で探索し、最初に見つかったコアの
`.so` を使う (`docs/architecture.md` §4.1)。

| 優先順位 | コア | apt パッケージ | 状態 |
|---|---|---|---|
| 1 | snes9x | `libretro-snes9x` | 【確認済】Ubuntu 24.04 universe に arm64 版 (1.61) が存在 (packages.ubuntu.com で確認)。RetroArch 本体 (1.18.0) も同様に存在 |
| 2 | bsnes_mercury_performance | `libretro-bsnes-mercury-performance` | 【確認済】arm64 版 (094+git20220807) が存在 (packages.ubuntu.com で確認) |
| 3 | bsnes_mercury_balanced | `libretro-bsnes-mercury-balanced` | 【確認済】同上 |

snes9x は軽量で Oracle A1 (2 OCPU) に適するため既定の第一候補になっている。コアを固定したい
場合は `core` に `.so` の絶対パスを直接指定する。

音声は `audio_driver = "pulse"` で生成 cfg に固定され、`PULSE_SINK=docich_sink` 環境変数で
docich 用の sink にルーティングされる (soren の既定 sink は変更しない。`docs/architecture.md` §0)。

---

## 4. ra-cmd (RetroArch へのコマンド送信)

`docich ra-cmd` は RetroArch の Network Command インターフェース (UDP。生成 cfg で
`network_cmd_enable = true` になっている) 経由でコマンドを送る。
coordinator runtimeは `55355 + generation % 1000` の世代別ポートを使う。
固定55355はcanonical stateが存在しないlegacy経路だけ。

```bash
bin/docich ra-cmd SAVE_STATE      # 現在の状態を保存
bin/docich ra-cmd LOAD_STATE      # 直前のセーブステートを復元
bin/docich ra-cmd PAUSE_TOGGLE    # 一時停止の切替
```

配信中に落ちた際の復帰や、brain 開発時の巻き戻しデバッグに使う。

---

## 5. 操作確認

入力とスクリーンショットの単発確認:

```bash
bin/docich send hanjuku-hero '{"type":"pad","buttons":["start"]}'
bin/docich snap                                  # run/screenshots/ に保存
```

`pad` の意味ボタン (`a`/`b`/`x`/`y`/`l`/`r`/`start`/`select`/方向) → 物理キーの対応は
`docs/architecture.md` §3.3 を参照。

---

## 6. トラブルシュート

### 入力が効かない

- 生成された `run/retroarch/retroarch.cfg` に **`input_driver = "sdl2"`** が入っているか確認する。
  【確認済】既定の `udev` ドライバは Xvfb + xdotool からの XTEST 入力を受け付けず、`"x"` ドライバも
  sdl2 ビデオドライバと組むと初期化されない。`"sdl2"` で XTEST キーが届くことを RGUI メニュー操作で
  実証済み (`docs/architecture.md` §9-1)。
- フォーカスの取得方法に注意する。**WM (ウィンドウマネージャ) の無い Xvfb では
  `windowactivate` (EWMH 依存) は機能しない。** docich は `windowfocus --sync`
  (XSetInputFocus を直接叩く) でフォーカスしてから XTEST (`keydown`/`keyup`) を送る実装になっている
  【確認済】(`docs/architecture.md` §9-2)。
- **押下時間にも注意**。RetroArch はフレーム毎のキー状態ポーリングのため、瞬間的な press/release は
  取りこぼされる【確認済】。docich の `pad`/`key` アクションの `hold_ms` (既定 100ms) を
  下げすぎないこと (`docs/architecture.md` §4.1)。
- それでも改善しない場合の代替案: RetroArch Network Remote (UDP パッド) 経由の入力。【要検証】

### 起動直後に落ちる (Aborted)

- 【確認済】セッション D-Bus が無い環境では GameMode 統合が abort する。docich は
  `dbus-run-session -- retroarch ...` で包んで起動する設計になっている
  (`docs/architecture.md` §4.1, §9-1b)。`dbus-daemon` パッケージ (メタパッケージ `dbus`) が
  入っているか確認する。

### 映像が出ない / 真っ黒

- `video_driver = "sdl2"` になっているか確認する。Xvfb は GLX が無い/不安定なことがあり、
  `"gl"` だと失敗し得る (§9-3)。SNES コアはソフトレンダなので sdl2 で十分な性能が出る想定。
- それでも出ない場合は mesa (llvmpipe) 経由の `"gl"` を試す選択肢がある。【要検証】

---

## 7. brain の実装と検証状態

`brains/hanjuku/brain.py` と攻略資料注入は実装済み (`docs/hanjuku_brain.md`)。
fake backendの契約テストと実ROM・課金backendでの動作保証は別。
`[agent] enabled = false` を維持し、認証・費用承認・観測→推論→入力の実機E2Eは未確認。

## 8. レトロコーナー登録と実行資格

`config/docich.soren-live.toml` の `retro_corner.games` に登録しているが、
**実行可能な抽選候補にはまだ入らない**。ゲーム側 `[retro_corner] enabled = false` により
自動選択から除外し、`agent.enabled = false` も維持する。`unattended = false` は
無人運転を検証済みと扱わないための明示で、自動コーナーの有効化スイッチではない。
登録・ユニットテストは本番反映やROM実プレイの証拠ではない。

登録数は無効なゲームも含むため、rolling間隔は `24h / 7`（約3時間26分）になる。
既存6ゲームが全て24時間のクールダウン中なら、その間は開始しない。
旧6件からの設定変更時には `next_due_at` が再計算され、最初のtickで抽選し得る。
日次モードへ戻す場合は無効な登録をgamesから外すこと（日次startは対象検証で拒否する）。

実行有効化には設定変更だけでなく、次の実装と別途検証が必要。

1. `RetroCornerManager._validate_games` と手動コーナーは現在CLI限定。
   RetroArchを許可する前に、ROM/core/brainの準備不足を選択前に除外する契約を追加する。
   `requires` は実行ファイルの存在検査のみで、ROM/core/CLI認証の検査ではない。
2. `RetroArchCoordinatorAdapter` の明示保存境界を §9 のとおり実装した。
   自動試合終了検出・自動checkpoint復元・無人の境界確認は未実装で、実行資格には使わない。
3. viewport設定時は専用Xvfbでゲーム本来のwindowを描画し、既存 `presentation.py` で
   `(0,90,960,540)` へcontainする。実装と合成画像の四辺テストは実ROMの表示証拠ではない。
   実機で四辺と周囲枠、共通配信PID維持、旧ゲーム子プロセス終了を確認する。
4. 自己吸い出しROM、libretro core、`retroarch`、`dbus-run-session`、`python3`、
   共通基盤のtmux/X11/ffmpeg/xdotoolを用意する。既定brainは `claude` CLIを使うため、
   認証と課金承認は別途必要。実行ファイルが存在するだけでは承認済みと扱わない。
5. fake backendによる契約テストと、実ROMでの観測→brain→入力→保存→復帰を分けて検証する。
   `retro_corner.enabled` と `agent.enabled` をtrueにするだけではCLI制限を通過しない。

今回の登録ではROM取得・有料LLM実行・本番操作を行わない。
オフライン検証: `python3 -m pytest -q tests/test_hanjuku_retro_registration.py tests/test_hanjuku_brain.py`

## 9. 明示保存境界と配信containの契約（オフライン実装）

### 停止・切替・再起動

RetroArchはcoordinatorの `switch` / `restart` に加えて `stop` もdrainingへ入る。
`requires_stop_boundary` は今回RetroArchだけが公開するcapabilityで、他adapterのstop手順は変更しない。
writer lockを解放して待つ間はactive identityを保持し、agentと手動入力を継続する。
`run/runtimes/<runtime_id>/retroarch_boundary.json` に要求ID・ゲーム・世代・leaseを束縛した
`waiting` を原子的に保存する。既定300秒で確認できなければ要求を失敗にし、
自分のwaitingだけをcancelする。ゲーム・agent・共通基盤を停止せず、ユーザーのpauseも解除しない。

安全境界は **運用者が明示確認する保存完了済み・一時停止状態**。試合終了画面を推測しない。
別途許可された実機検証時、次の順序で確認する（本実装タスクでは未実行）。

1. stop/switch要求のdrainingと要求IDを確認する。
2. 対象runtimeのゲームを明示的に一時停止し、`ra-cmd GET_STATUS` で対象ROMの `PAUSED` を確認する。
   `PAUSE_TOGGLE` はトグルなので、既にpause中なら送らない。
3. `ra-cmd SAVE_STATE` でslot 0へ保存し、runtime内 `states/hanjuku-hero.state` の保存完了を確認する。
   UDP送信成功や無応答を保存完了の証拠にしない。
4. 同じconfigで `ra-boundary --request-id <要求ID> --checkpoint hanjuku-hero.state` を実行する。
   canonicalのdraining identity、runtime window所有権、対象ROMのPAUSED応答、要求後の非空checkpoint、
   fsync前後のSHA-256一致を確認し、`reached` / `outcome=suspended` を保存してからackする。

確認と入力はruntime別flockで直列化する。reached後はpad/keyおよび `ra-cmd` の変更コマンドを拒否し、
次の試合開始・pause解除・state上書きを防ぐ。GET_STATUSだけは読み取りを継続できる。
境界待ち側でも保存hashとpauseを再確認し、agent停止→ゲームwindow停止→子プロセス終了確認の順に進む。
不正JSON・stale要求・別世代・異なるROM・欠損/空/古い/変更中checkpoint・所有権不一致はfail-closed。
確認後のcancelや、確認済みruntimeの無条件再起動は拒否する。保存データは世代directoryに残し、
明示的な復元と継続確認を要する。**自動復帰・次世代へのsave移行はまだ実装していない**。

PAUSED応答形式は [RetroArch v1.18.0 command.c](https://github.com/libretro/RetroArch/blob/v1.18.0/command.c)
に合わせる。別形式や判定不能は成功扱いしない。

### 描画・入力・プロセス

viewport経路のRetroArchはwindowed / 標準 `video_scale=3.0` / coreのaspect維持 / overscan crop無効。
ゲームwindowを配信枠へresizeせず、private Xvfb上の実window寸法を取得して全体をx11grabする。
dbus-run-sessionの子がwindowを持つため、private display内でRetroArchの唯一のwindowを検査する。
既存のffplay presenterが映像だけを縦横比維持・黒余白・中央配置する。
正方形は540×540＋左右210px、4:3は720×540＋左右120px。encoder・共通display・音声busは操作しない。
AI観測は同じprivate window全体、入力は同じprivate display/windowへ向け、配信座標へ変換しない。

`presentation.json` は `starting → ready → stopped`（異常時 `presentation_failed` / `cleanup_failed`）を記録する。
投影のffplayだけが終了した場合、native game/Xvfbと入力経路は保持する。TERM/HUPは通常どおり応答する。
停止時はwrapperが所有するXvfb・dbus/RetroArch・ffplayのプロセスグループだけを終了し、
leaderをwaitした上でグループ消滅を確認する。tmux pane消滅・deadだけでは終了成功にしない。
終了記録が欠ける/子が残る場合は次ゲームを起動せず復旧待ちにする。
OSからの強制終了や別sessionへ離脱する子まで救済する仕組みではなく、その実機検証も有効化ゲート。

### 残る実機ゲート・runtime checklist

- 実ROM/coreでの四辺・スコア・操作案内と周囲枠、common encoder/display/audio PIDの維持。
- 実ROMの観測→brain→入力、pause応答、saveの完了/ロード/再開、試合結果の継続性。
- dbus/RetroArchとその子、Xvfb、ffplayの正常停止・異常終了・資源解放・旧runtime非復活。
- runtime registryは既存retroarch adapterを使用し、新しい常駐worker/queue/model/providerは追加しない。
  世代別manifestとhealth契約を追加。新しいAI telemetryはなし。
- 固定VM diagnostics collectorへの詳細manifest収集は未追加。canonicalの既存phase/error収集に加え、
  有効化前に `presentation.json` / `retroarch_boundary.json` のsanitized収集をレビューする。
  ROM/state本文・brain本文・credentialsは公開ログや診断へ出さない。
- deployは未実施。`retro_corner.enabled=false` / `agent.enabled=false` / CLI候補制限を維持する。
  この実装・ローカルテスト合格だけで自動抽選/無人運用へ昇格させない。

契約テスト: `tests/test_retroarch_safe_boundary.py`、`tests/test_presentation.py`。
ROM取得、実機起動、課金brain、VM操作、push/PR/merge/deployは本タスクでは実施しない。
