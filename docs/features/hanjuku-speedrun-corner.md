# 半熟英雄スピードランコーナー 設計書（ドラフト）

- 状態: 設計のみ。コード・設定・本番は未変更。
- 対象: 新コーナー `hanjuku-speedrun`。既存の半熟英雄コーナー `hanjuku-hero` を元にする。
- 方針: 個人プロダクトなので、まず動くものを出すことを優先する。厳格なデプロイガードは新しく足さない。
  そのかわり、元コーナーの挙動は変えない、ゲームの入力をスピードラン機能で止めない、の2点は必ず守る。
- `handoff.md` は `DOCICH_HANDOFF_PATH` が未設定のため読んでいない（AGENTS.md §1）。

## 0. やりたいこと（オーナー依頼 2026-11-25）

1. 半熟英雄コーナーの構成とボットを変えずに複製し、それを元に新コーナー「半熟英雄スピードラン」を登録する。
2. 起動は最初は手動だけにする。問題がなければ自動ローテーションに入れる。
3. 最初は普通にゲームクリアまで遊ばせる。目的はクリアまでの時間短縮。改善できるように、数ラン分の詳細なログを残す。
4. ログでVMの容量を圧迫しない。R2への退避やローテーションを使う。
5. コメントプロンプトにも対応する。
6. 配信画面上部の4分割ステータス欄にタイマーを出す。12話ごとのスプリットタイマーにし、ミリ秒まで表示する。
   自己ベスト（PB）、区間ベスト（gold）、PB時の区間タイムを保存する。色分けと、PB時の区間タイムとの差分を表示する。

## 1. 現状調査で分かったこと（根拠）

| 項目 | 現状 | 根拠 |
|---|---|---|
| ゲーム設定 | `config/games/hanjuku-hero.toml`（retroarch adapter, command brain `brains/hanjuku/bot.py`, `[hanjuku]` 各種） | 同ファイル |
| コーナー登録 | `config/docich.soren-live.toml` の `[corner_rotation].corners` に `{ id = "hanjuku-hero", adapter = "game", game = "hanjuku-hero" }` | 80行目 |
| 手動のみの仕組み | catalog行の `manual_only = true` で自動抽選から外れ、手動予約だけ受け付ける（puyo/trpg/chart で実績あり） | `src/docich/corner_catalog.py`, `corner_rotation.py` の `_eligible(include_manual_only=)` |
| 手動起動経路 | `python3 -m docich.hanjuku_corner`（`queue_manual('hanjuku-hero')`）、owner-only `corner-rotation-operator.yml` の `start-hanjuku` / `resume-hanjuku-latest`、WebUI の `_CORNER_QUEUED_MANUAL_GAMES` | `src/docich/hanjuku_corner.py`, `webui.py:1871` |
| ゲーム名の決め打ち | `'hanjuku-hero'` の文字列比較が src/brains に **134箇所・37ファイル**。`config/games/hanjuku-hero.toml` の直読みが6箇所 | `grep` 実測 |
| セーブの分離 | RetroArch の `saves/` `states/` は世代（runtime）ごとのディレクトリ。新しいruntimeはバッテリーセーブなしで始まる | `src/docich/adapters/retroarch.py` `retroarch_cfg_lines` |
| 共有の学習データ | `run/hanjuku_experience.json` を全世代で読み書きする | `brains/hanjuku/bot.py` `experience_path` |
| ログ | 世代ごとに `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl`。4MiBで `.previous` に1回だけ回すので、長いランでは古い部分が消える。完了済み世代は10世代を超えると削除（`hanjuku-hero` のみ対象） | `hanjuku_run.py` `append_log`/`MAX_LOG_BYTES`, `hanjuku_log_retention.py` |
| 章の区切り | 章末の「セーブをしゅうりょうして よろしいですかな?」を読むと `prompt` 記録（`strategy_variant='chapter_end_save'`）が出る。直後の先発隊画面で `chapter_seen`（evidence `chapter_end_save_party`）が出る | `hanjuku/flows/prompts.py`, `flows/starter.py`, `strategy/campaign.py` |
| クリア判定 | エンディングの「めでたし」を読むと `clear_ending_seen`。その後タイトルに戻ると `terminal_reason='game_clear'` | `hanjuku_run.py` `CLEAR_ENDING_TEXT` |
| 上部ステータス欄 | `overlays/direct_broadcast_overlay.html` の `#top-rail`（960×90、内側の `#summary` は2×2の4行・高さ73px）。作業中バナー > コメント生成中 > 4行サマリの順で出し分け、`topOverride` で差し替えられる | `renderTopRail()` |
| 表示データの経路 | Soren側サーバーが `/__soren_overlay/broadcast/state` を1秒ごとに返す。中身は docich の `overlays/direct_broadcast_overlay_state.mjs` が組み立てる。docich側のファイルは `next_corner.json` と同じく既定パスで読める | `DEFAULT_NEXT_CORNER_FILE` |
| コメントプロンプト | ゲームごとのプロンプト差し替えは `src/docich/comment/game_profile.py` が持つ（`HANJUKU = "hanjuku-hero"`） | 同ファイル |
| R2 | リポジトリ内で利用実績なし | `grep` 実測 |
| クリア所要時間 | 現行BOTの1回のクリア挑戦は数日単位（例: g1190 は 2026-11-16 に第1話から始め、11-24 時点で第12話） | `VERIFY-5448.md` ほか |

最後の行が設計に効く。**1回の試走が数日かかる**前提で、中断・再開、ログ量、タイマーの桁数（100時間超）を設計する。

## 2. 複製の方針

### 2.1 何を複製し、何を共有するか（推奨案）

| 種類 | 扱い |
|---|---|
| ゲーム設定 | `config/games/hanjuku-speedrun.toml` として**丸ごとコピー**する。違いは §2.3 の分離項目だけ |
| コーナー登録 | 新しいcatalog行 `hanjuku-speedrun` を足す（`manual_only = true`） |
| 実行時の状態 | ゲーム名が別になるので、世代・セーブ・ログは自然に分かれる。学習データなど共有ファイルは §2.3 で分ける |
| ボットの判断ロジック（`hanjuku_bot.decide` と `src/docich/hanjuku/` 一式） | **コピーせず共有する**。1行も変えない |
| ボットの入口 `brains/hanjuku/bot.py` | 判断は変えない。受け付けるゲーム名を増やし、スピードラン用の記録フックを足すだけ（§3.4） |

ロジックをコピーしない理由:

- 半熟ロジックは `src/docich/hanjuku*` 127ファイル＋パッケージ一式あり、毎日のように直っている（v1505〜v1509 が直近数日）。
  コピーすると、元コーナーの修正がスピードラン側に届かず、クリア自体が難しくなる。
- `hanjuku_hotload` でデプロイ中のランにも新しいロジックが入る仕組みがある。コピーすると、この仕組みも二重に持つことになる。
- 時間短縮の改善はスピードラン側にだけ入れたい。これは後から `[hanjuku.speedrun]` の設定や `profile == "speedrun"` の分岐で入れる。
  元コーナーでは既定で無効なので、元の挙動は変わらない（§8 Phase 4）。

代案（完全フォーク）: `hanjuku_speedrun/` にロジックを丸ごとコピーする。元コーナーからは完全に独立するが、
数万行の二重保守になり、元側の修正を手で移植し続けることになるため推奨しない。どうしても独立させたい場合は、
「改善を始める時点でロジックを凍結コピーする」段階を Phase 4 に置く。

### 2.2 `'hanjuku-hero'` 決め打ちの扱い

`src/docich/hanjuku/family.py` を新しく作り、ゲーム名の定義をここ1か所にまとめる。

```python
ORIGINAL = "hanjuku-hero"
SPEEDRUN = "hanjuku-speedrun"
GAMES = frozenset({ORIGINAL, SPEEDRUN})
def is_hanjuku(name) -> bool: ...
def config_path(root, game) -> Path: ...   # config/games/<game>.toml
```

134箇所を次の3種類に分けて直す。**元コーナーの挙動を変えない置き換えだけ**にし、既存の半熟テストスイートをそのまま通す。

| 分類 | 内容 | 主なファイル |
|---|---|---|
| A. エンジン系 → `is_hanjuku()` に置き換え | 半熟エンジンで動くかどうかの判定。スピードランでも同じ動きが必要 | `hanjuku_run.enabled`, `hanjuku_hotload`, `agent/brains.py`, `agent/loop.py`, `retro_corner.py`（20箇所）, `game_switch.py`, `presentation_recovery.py`, `hanjuku/identity.py`, `hanjuku/orchestrator.py`, `hanjuku_resume*.py`, `hanjuku_manual_*.py`, `hanjuku_queue_cancel.py`, `hanjuku_scene*.py`, `hanjuku_narration.py`, `soren_corner_stats.py`, `soren_corner_text.py`, `corner_rotation.py`（resume/new_game の許可）, `comment_classifier/active_game.py`, `brains/hanjuku/bot.py` |
| B. 元コーナー専用 → そのまま残す | 元コーナーの記録・予想・クリップ・改善 | `hanjuku_progress.py`, `hanjuku_predictions.py`, `record_clips.py`, `hanjuku_improve.py`, `hanjuku_log_retention.py`（`GAME` 定数）, `stream_category.PROGRESS_TITLE_GAME` |
| C. 設定ファイルの直読み → 実行中ゲームの toml を読む | `config/games/hanjuku-hero.toml` 固定の6箇所 | `bot.py` の `chart_edition_setting` / `approx_map_setting`, `hanjuku_scene.py`, `hanjuku/flows/battle_menus.py`, `comment/game_profile.py` |

再開（resume）の注意: `hanjuku_resume*` の「最新のクイックセーブ」探索は**同じゲーム名の世代だけ**を候補にする。
スピードランの再開で元コーナーのセーブを拾うこと、またはその逆を、テストで禁止する。

### 2.3 元コーナーと分ける共有資源

| 資源 | 元コーナー | スピードラン |
|---|---|---|
| 学習データ `run/hanjuku_experience.json` | 今まで通り | `run/hanjuku_speedrun_experience.json`。初回起動時に元のファイルを1回だけコピーして種にする。以後は元のファイルを読まない・書かない |
| 進行記録・チャネルポイント予想 `[hanjuku.predictions]` | 有効 | `enabled = false`（PB予想は Phase 4 で検討） |
| 新記録クリップ `record_clips` | 有効 | 対象外（PB更新クリップは Phase 4 で検討） |
| 試合後の自動改善 `hanjuku_improve` | 今まで通り | 無効。スピードラン用の改善は別経路（§8 Phase 4） |
| 監査Issueの自動起票（`fault_issue` / `month_audit`） | 今まで通り | 有効のまま。共有ロジックの不具合を見つけるのに役立つ。Issue本文に `game=hanjuku-speedrun` を入れて区別する |
| チャート版 `chart_edition` | `ab` | **`speedrun` に固定するのを推奨**。ランごとに版が入れ替わると時間を比べられないため（§10 確認事項 4） |
| Twitch | カテゴリ 21236、`[半熟英雄]` | 同じカテゴリ、`title_prefix = "[半熟英雄RTA]"` |
| コーナー表示名 | 半熟英雄 | 半熟英雄スピードラン（`corner_notice.py` の表示名表、`stream_category.VIEWER_GAME_NAMES`、`stream_title_context.py` の説明文に追加） |
| 左余白の観測カード・GAME STATSの半熟カード | 表示 | 同じものを表示する。AGENTS.md「半熟英雄は…他ゲームへ広げない」に `hanjuku-speedrun` を同じゲームとして追記する（要オーナー承認） |

## 3. スプリットタイマー

### 3.1 区間の定義（12話）

| 区切り | 判定（既存のボット記録・観測を使う） | 時刻 |
|---|---|---|
| 計測開始 | 新規ゲームで名前入力画面を初めて観測した時（「はじめから」確定の直後） | その観測の画像取得時刻 |
| 第1〜11話の終了 | `prompt` 記録の `strategy_variant == 'chapter_end_save'`（章末セーブ確認を実画面で読んだ） | 同上 |
| 　（予備） | 上を取り逃して先発隊画面で次章が確定した（`chapter_seen`, evidence `chapter_end_save_party`） | 同上。`source=fallback` と記録 |
| 第12話の終了＝計測終了 | エンディングの「めでたし」を初めて読んだ（`clear_ending_seen` が初めて true） | 同上 |

- 区切りの章番号は、現在の区間と一致するときだけ確定する。2つ飛んだら、間の区間は「結合」として扱う。
  結合した区間は区間ベストの更新に使わない。合計タイムは有効なので、PBの対象にはなる。
- 開始・終了の基準はログから後で計算し直せるように、電源ON・タイトル・名前入力の時刻も全部ログに残す（§4）。
  基準を変えたくなったら、過去ランもログから再計算できる。

### 3.2 計時の方式

- 時計はVMの壁時計（ミリ秒、epoch）。オーバーレイの Chromium も同じVM上にあるので、同じ時計を使える。
- **コーナーが動いている間だけ時間を進める。** 停止（WebUI / operator のセーブ付き停止）から再開までの時間は数えない。
  1回の試走が数日かかるので、中断と再開ができないと記録が取れない。
- 時間はセーブと結び付ける。定期セーブ（15分ごと）と停止時のslot 0保存のたびに、その保存のSHA-256と経過ms・区間番号を
  試走記録へ追記する。再開時は復元したセーブのSHAで経過msを戻す。クラッシュで古いセーブから戻った場合も、
  ゲームの状態と時間が一緒に巻き戻る。巻き戻った時間は `rolled_back_ms` としてログに残す。
  SHAが一致しないまま再開した試走は `time_unverified` にしてPBの対象から外す。
- 定期セーブ中の一時停止（数秒）やBOTの待ち時間は、実際の損失なので時間に含める。
- 精度: 表示はミリ秒だが、判定は観測の間隔（通常1.5秒、速い場面で0.5秒）に依存する。記録は「区切りが写った最初の画像の取得時刻」。
  同じ方式で比べ続ける限り、ラン同士の比較には使える。設計書と配信の説明にこの限界を書く。

### 3.3 データの置き場所と形式

`run-soren-live/speedrun/hanjuku-speedrun/` に置く。どれも小さなJSONで、原子的に書き換える。

| ファイル | 中身 | 更新タイミング |
|---|---|---|
| `timer.json` | 表示用の現在状態（下記）。オーバーレイはこれだけ読めば描ける | 状態変化時＋毎観測の生存確認（約1.5秒ごと、数百バイト） |
| `records.json` | PB（合計・各区間・各累計・試走ID・BOT版）、区間ベスト12個（ms・試走ID・日時）、区間ベストの合計（Sum of Best） | 区間確定時・試走終了時 |
| `attempts.jsonl` | 試走1回1行の履歴（ID、結果、各区間、BOT版の一覧、チャート版、中断回数、フラグ） | 試走終了時 |
| `attempts/<attempt_id>/` | 試走ごとの manifest・区間要約・ログの一時置き場（§4） | 随時 |

`timer.json` の形（案）:

```json
{
  "schema": 1, "game": "hanjuku-speedrun",
  "runtime_id": "g1300-abcdef", "attempt_id": "a0007-…", "attempt_no": 7,
  "status": "running",              // idle | pending | running | paused | finished | dnf
  "base_elapsed_ms": 4521234,       // 下の時刻時点の経過
  "base_wall_ms": 1795000000000,    // running のときだけ有効。表示側は now - base_wall_ms を足す
  "current_index": 3,               // 0始まり。走行中の区間
  "splits": [
    {"index": 0, "segment_ms": 2533111, "cumulative_ms": 2533111,
     "verdict": "gold", "delta_ms": -61022, "source": "chapter_end_save"}
  ],
  "comparison": {"pb_total_ms": 0, "pb_segments_ms": [], "pb_cumulative_ms": [],
                 "gold_segments_ms": [], "sum_of_best_ms": 0},
  "flags": {"segmented": false, "time_unverified": false},
  "heartbeat_at_ms": 1795000001234
}
```

色の判定（`verdict`）は記録を書くPython側で区間確定時に決め、ファイルに書く。表示側では計算しない
（走行中の区間と合計の色だけは、表示側が `comparison` を見て決める。§3.5）。

### 3.4 記録を書く場所

- `src/docich/hanjuku_speedrun/` パッケージを新しく作る（`timer.py` 区間判定とPB/区間ベスト、`store.py` ファイル入出力、`archive.py` ログ退避、`report.py` 分析）。
- 書き込みは `brains/hanjuku/bot.py` の、判断後に実行する副作用リスト（`persist` / `month_audit` などと同じ場所）に1行足す。
  `game == 'hanjuku-speedrun'` のときだけ動く。例外は他の副作用と同じく握りつぶしてstderrに1行出すだけで、**入力は絶対に止めない**。
  ネットワーク処理はしない。
- 試走の終了処理（`finished` / `dnf` / `paused`）は、ゲーム切替後の片付け（`hanjuku_log_retention` を呼んでいる場所）で行う。
  終端理由は既存の `terminal_reason` / `manual_saved_stop` などをそのまま使う。

試走の状態遷移:

```
corner開始(新規) ─→ pending ─(名前入力を観測)→ running ─(めでたし)→ finished
                                     │  ├─(game_over / screen_stalled / input_stalled)→ dnf
                                     │  └─(セーブ付き停止)→ paused ─(同じ試走を再開)→ running
                                     └─ 新規開始で上書きされた paused は dnf(abandoned)
```

- ゲームオーバーは Phase 1 では元コーナーと同じくコーナー終了とし、試走は `dnf`。
  完了した区間の区間ベストは保存する（ゲームオーバーまでの区間も改善の材料になるため）。PBは完走した試走だけで更新する。
- `screen_stalled` / `input_stalled` で終わった試走は、セーブがあれば「同じ試走の再開」を選べる（`dnf` → `paused` に戻す）。

### 3.5 色と差分のルール

ユーザー指定をそのまま判定表にする。「PB時の区間」は、現在のPB（最速で完走した試走）の同じ区間のタイム。

| 表示 | 条件 | 色 |
|---|---|---|
| 確定した区間 | 今回の区間タイムが、それまでの区間ベストより速い（初めての計測を含む） | **黄** |
| 確定した区間 | 区間ベストではないが、PB時の区間より速い | **青** |
| 確定した区間 | PB時の区間より遅い | **赤** |
| 確定した区間 | PB時の区間と同タイム、またはPBがまだ無く区間ベストでもない | 白 |
| 走行中の区間 | 経過がPB時の区間を超えた | 赤（超えるまでは白） |
| 合計（走行中） | 直前に確定した区間までの累計、または現在の経過が、PBの同じ時点の累計より遅い | 赤。先行中は青（提案）、PBなしは白 |
| 合計（完走時） | PBを更新した（初完走を含む） | **黄** |
| 合計（完走時） | PB以下で完走した | 赤 |
| 未到達の区間 | PB時の区間タイムを灰色で表示 | 灰 |

- 差分は各区間で「今回の区間タイム − PB時の区間タイム」を表示する（例 `−1:02.345` / `+0:15.210`）。PBが無い間は `—`。
- 結合した区間は、2区間分の和をPBの同じ2区間の和と比べる。区間ベストの判定はしない。

### 3.6 画面レイアウト（上部 960×90、内側 約944×73）

スピードラン実行中は、`#top-rail` の4行サマリをタイマー表示に置き換える。枠の位置と大きさは変えない（AGENTS.md の共通枠規則）。

```
┌──────────────────┬─────────────┬─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐
│ 半熟英雄RTA  #7   │1話  42:13.111│2話  38:01.500│3話 1:05:22.0│4話 ▶12:03.4  │5話   PB 51:10│6話   PB 48:22│
│  73:12:45.678    │  −1:01.022 黄│  +0:15.210 赤│  −0:40.002 青│              │              │              │
│ PB 81:00:12.345  ├─────────────┼─────────────┼─────────────┼─────────────┼─────────────┼─────────────┤
│ SoB 70:01:00.000 │7話   PB 55:01│8話   PB 1:10:│9話   PB 59:30│10話  PB 44:00│11話  PB 1:20:│12話  PB 2:05:│
│  ⚙作業中(小)      │              │              │              │              │              │              │
└──────────────────┴─────────────┴─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘
  左: 約220px                         右: 12区間を 6列×2行（1セル 約120×34px、等幅11〜12px）
```

- 左の大きな数字が合計タイマー（`h:mm:ss.mmm`。時間の桁は可変で100時間超も出せる）。下に PB合計 と Sum of Best。
- 各セルは1行目に「話番号＋区間タイム」、2行目に差分。色は §3.5。走行中のセルは枠を強調し、区間タイムが動く。
- 作業中バナーとコメント生成中の表示は、スピードラン中はタイマーを隠さず、左下の小さなチップに縮める
  （優先順位: スピードランタイマー ＞ 作業中 ＞ コメント生成中 ＞ 4行サマリ）。
- ミリ秒の描画: 状態は今まで通り1秒ごとに取得し、時刻の表示だけを `requestAnimationFrame` で約30fpsに間引いて更新する。
  書き換えるのは合計と走行中セルの2か所のテキストだけにして、VM（2 OCPU）の描画負荷を抑える。
- 表示する条件: `run-soren-live/game_switch.json` の active が `hanjuku-speedrun` で、`timer.json` の `runtime_id` と一致するときだけ。
  コーナーが終わったら4行サマリに戻す（AGENTS.md「状態表示は現在のゲームに紐づける」）。
- 色: 黄 `#facc15`、青 `#60a5fa`、赤 `#f87171`、白 `#e5e7eb`、灰 `#64748b`（配信上で読めるかは実機で確認して調整）。

実装する場所:

- `overlays/direct_broadcast_overlay_state.mjs`: `DEFAULT_SPEEDRUN_TIMER_FILE`（`next_corner.json` と同じ docich 既定パス）を読み、`speedrunTimer` として state に入れる。Soren側サーバーの改修は要らない見込み。
- `overlays/direct_broadcast_overlay.html`: `#top-rail` に `#speedrun-timer` を足し、`renderTopRail()` の優先順位に入れる。
- 反映は既存のオーバーレイ再読込経路（`ops/vm_actions/restart_direct_stream_overlay.sh` / shared overlay reload）を使う。

## 4. 詳細ログと保管

### 4.1 残すもの（試走ごと）

| ログ | 中身 | 目安 |
|---|---|---|
| 判断・イベントログ全量 | 既存の `hanjuku_decisions` / `hanjuku_events`。**回転で消える部分も含めて全部** | 1回転4MiB。gzipで1/10程度 |
| 観測トレース（新規） `speedrun_trace.jsonl` | 観測1回1行: 試走内経過ms・話・年月・phase・画面種別・送った入力・保留理由・観測間隔・判断にかかった時間 | 1行150B程度。1週間で約60MB（gzipで数MB） |
| 区間要約（新規） `segments.json` | 区間ごとの内訳: phase別の時間、戦闘数・出撃数・ゲーム内の月数、保留・停滞・LLM待ちの時間、BOT版 | 小さい |
| 画像 | 区切りの瞬間の画像と既存の `hanjuku_frames` リング、定期の画像（既定10分に1枚） | 1枚100KB程度 |
| manifest | 試走ID、BOT版の一覧、ロジック世代、チャート版、設定のハッシュ、docichのコミットSHA、開始・終了・結果 | 小さい |

時間短縮で見たいのは「どこで時間を使ったか」なので、観測トレースと区間要約を新しく足す。
既存の判断ログは「なぜそうしたか」の根拠として全量残す。

### 4.2 回転で消さない仕組み

- `hanjuku_run.append_log` の回転処理に1つ分岐を足す。スピードランの世代（runtimeに試走マーカーがある）だけ、
  `.previous` を上書きする前に、古い `.previous` を `attempts/<id>/spool/` へ移す。元コーナーの動きは変えない。
- 試走の終了時に、現在のログと `.previous` も spool に移す。

### 4.3 VMの容量を守る

- spool の上限を既定 1GiB とする（設定で変更可）。上限を超えたら、R2へ送り済みのものから消す。
  それでも超えるときは、定期画像 → 観測トレース → 古い判断ログの順に、まだ送っていないものでも捨てる。ゲームは止めない。
  捨てた量は manifest と診断に残す。
- 送り終わったものはVMから消す。VMに残すのは `records.json`、`attempts.jsonl`、直近3試走の manifest と区間要約だけ。
- スピードランの世代ディレクトリは、試走のログを送り終えたら、直近3試走ぶん（再開に使うセーブを含む）を残して既存の削除処理と同じ考え方で消す。
  元コーナー用の `hanjuku_log_retention` は変えない。

### 4.4 R2への退避

- 送る処理は別のプロセスにする: `python3 -m docich.hanjuku_speedrun.archive sync`。
  `scripts/systemd/docich-speedrun-archive.{service,timer}` を足し、5分ごとに動かす（`docich-corner-rotation.timer` と同じ配備方式）。
  BOTとゲームはネットワークを待たない。
- 圧縮（gzip）もこのプロセスで行う。
- S3互換APIへの署名付きPUT（SigV4）を標準ライブラリだけで書く（約100行）。新しい依存は足さない。
  送った後に HEAD でサイズを確認してから、ローカルのファイルを消す。
- 置き場所: `r2://<bucket>/hanjuku-speedrun/attempts/<attempt_no>-<attempt_id>/{manifest.json, segments.json, decisions-0001.jsonl.gz, …, frames-ch03.tar}`。
  PBの試走と、区間ベストを持っている試走は `hanjuku-speedrun/keep/` にもコピーする。
- 保持: `attempts/` は R2 のライフサイクル規則で90日、または直近20試走。`keep/` は消さない。
  容量は1試走（約1週間）で数百MB以内を目安にする。R2の無料枠（保存10GB/月）に収まる想定だが、料金は着手時に公式で確認する。
- 資格情報: オーナーがR2のバケットと、そのバケットだけ読み書きできるAPIトークンを作り、VMの
  `~/.config/docich/r2.env`（権限600）に `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` を置く。
  リポジトリ・ログ・Issue・Actions出力には書かない（AGENTS.md §3）。退避プロセスのログは固定のコードだけを出す。
  未設定なら送らずに spool の上限管理だけ行う（試走は普通に続く）。
- 診断: 既存の diagnostics collector に「spoolのバイト数、最後に送れた時刻、最後のエラーコード」だけを足す（AGENTS.md §11 の checklist）。

### 4.5 分析

- `python3 -m docich.hanjuku_speedrun.report --attempt <id> [--compare <id>]` で、R2から取ってきて区間ごとの内訳と
  2試走の差を Markdown で出す。改善の目星（長い保留、繰り返しの出撃失敗、月数の多い章）を一覧にする。
- 後で owner-only の workflow から実行できるようにする（Phase 4）。

## 5. コメントプロンプト

`src/docich/comment/game_profile.py` に `hanjuku-speedrun` のプロフィールを足す。

| 差し替える枠 | 中身 |
|---|---|
| 一言説明（blurb） | 「半熟英雄スピードラン（SFCエミュレータ、BOTがクリアタイムの短縮に挑戦中）」 |
| ゲーム状態 | `timer.json` / `records.json` から作る、計測した事実だけの短い文。例: 「第5話を走行中。経過 73:12:45。第4話終了時点でPB比 −4:12。直前の第4話は区間ベストを更新」。古い・読めないときは何も書かない |
| ルールのメモ | 12話の区切り方（章末のセーブ確認で区切る、最後は「めでたし」）、PB・区間ベストの意味、色の意味（黄=区間ベスト/PB更新、青=PBの区間より速い、赤=遅い）、中断中の時間は数えないこと、計測精度は約1秒であること |
| BOTの仕組み | 元コーナーの `HANJUKU_HOW_IT_WORKS` をそのまま使い、「このコーナーは同じBOTでクリアタイムを縮めるための計測コーナー」を1行足す |

あわせて `comment_classifier/active_game.py` の `GAME_KINDS` と `comment/sorengame_context.py` の blurb 対応表に追加する。
数字は推測で補わない。タイマーのファイルが無いときは「計測情報なし」として扱う。

## 6. 起動とローテーション

### Phase 1: 手動のみ

- catalog 行: `{ id = "hanjuku-speedrun", adapter = "game", game = "hanjuku-speedrun", manual_only = true }`
- 手動起動:
  - CLI: `python3 -m docich.hanjuku_corner --game hanjuku-speedrun`（既存CLIにゲーム指定を足す。既定は今まで通り `hanjuku-hero`）。同じ試走の再開は `--resume-latest`
  - owner-only workflow: `corner-rotation-operator.yml` に `start-hanjuku-speedrun` / `resume-hanjuku-speedrun-latest` を足す
  - WebUI: `_CORNER_QUEUED_MANUAL_GAMES` と `_MANUAL_RESERVATION_CORNERS` に追加
- 停止は元コーナーと同じセーブ付き停止を使う。試走は `paused` になる。

### Phase 2: 自動ローテーション

- 確認すること: 1回以上の完走、または数日分の連続走行でタイマー・ログ・R2退避・表示が崩れないこと。
- `manual_only` を外す。決めること（§10）: 1試走が数日かかるので、
  (a) 元コーナーと同じく終わるまで枠を使う、または (b) 1枠を N 時間にし、セーブ付き停止で中断して次の枠で再開する（時間は数えないので記録は続く）。
  (b) なら他のコーナーも回る。

## 7. 変更しないもの

- `config/games/hanjuku-hero.toml` と、元コーナーのcatalog行・予想・クリップ・改善・ログ削除の挙動。
- 半熟の判断ロジック（`hanjuku_bot.decide` と `src/docich/hanjuku/` の判断部分）。
- 共通の配信基盤（エンコーダ・音声・通知枠・ステータス枠の位置と大きさ）。

## 8. 段階計画（PRの分け方）

ローンチ優先なので、PR 1〜3 は1本にまとめてもよい（まとめれば、手動起動の初回からタイマーが出る）。

| PR | 内容 | 完了の確認 |
|---|---|---|
| 1. 複製 | `hanjuku/family.py`、決め打ち134箇所の分類と置き換え、`hanjuku-speedrun.toml`、catalog（manual_only）、手動起動3経路、学習データなどの分離、再開の同一ゲーム限定、表示名・Twitchタイトル | 既存の半熟テストスイートが無修正で通る。スピードランを手動で起動し、新規ゲームでプレイが進む |
| 2. タイマー記録 | `hanjuku_speedrun/timer.py`・`store.py`、bot.py の副作用フック、試走の状態遷移、セーブとの結び付け、PB・区間ベストの更新 | 合成した判断記録で12区間・結合・DNF・再開・巻き戻しの単体テスト |
| 3. タイマー表示 | mjs の state 追加、上部の描画、色・差分、優先順位、rAF | 合成 `timer.json` での表示テスト。実機で上部の枠内に収まり、ゲーム枠・周りの枠が変わらないこと |
| 4. ログとR2 | 観測トレース、区間要約、回転時の退避、spool上限、`archive sync`、systemd timer、診断、R2の手順書 | 退避と上限の単体テスト。実機で1回転分がR2に届き、VMから消えること |
| 5. コメント・実況 | game_profile のプロフィール、区間確定の通知（「第3話 区間ベスト更新」など。読み上げは既存の場面実況に乗せるか別途判断） | profile の単体テスト |
| 6. 自動ローテーション | `manual_only` を外す（§6 の決定に従う） | 自動抽選で開始・終了・復帰すること |
| 以後 | 時間短縮の改善: `[hanjuku.speedrun]` 設定と `profile == "speedrun"` 分岐で、タイトルの「つづき」試行を省く等。PB予想・PB更新クリップ・分析 workflow | report の比較で区間が縮むこと |

## 9. テスト方針

- ローカルでは変更に関係するテストファイルだけを流す（AGENTS.md §13）。共通モジュール（`retro_corner.py` / `game_switch.py` / `webui.py` など）を触るPRは CI がフルスイートを流す。
- 新しいテスト:
  - `tests/test_hanjuku_speedrun_timer.py`（区間判定、色、差分、PB/区間ベスト、結合、DNF、再開、巻き戻し）
  - `tests/test_hanjuku_speedrun_isolation.py`（学習データ・予想・クリップ・改善・再開のセーブ候補が元コーナーと混ざらない）
  - `tests/test_hanjuku_speedrun_archive.py`（回転時の退避、spool上限、SigV4署名の固定ベクタ、資格情報がログに出ない）
  - オーバーレイ: 合成 `timer.json` の表示テスト。新しい HTML view は作らないので `test_broadcast_view_standard.py` の対象追加は不要（既存の配信オーバーレイの中に足すため）
- 元コーナーが変わっていないことは、既存の半熟テスト（`tests/test_hanjuku*.py`）を書き換えずに通すことで確かめる。

## 10. オーナーに決めてほしいこと

| # | 質問 | 推奨 |
|---|---|---|
| 1 | 複製の単位: 判断ロジックは共有し、設定・ゲームID・状態を複製する案でよいか（完全フォークにしない） | 共有 |
| 2 | 計測開始: 名前入力画面の出現／電源ON／タイトルで「はじめから」を押した入力 | 名前入力画面 |
| 3 | 区間の終わり: 章末のセーブ確認／ボス撃破 | 章末のセーブ確認 |
| 4 | チャート版: `speedrun` に固定／元コーナーと同じ `ab` のままコピー | `speedrun` に固定（比較のため） |
| 5 | ゲームオーバー: その試走を終了（DNF）／ゲーム内の「つづき」で同じ試走を続ける（時間は流れ続ける） | Phase 1 は DNF |
| 6 | 中断した時間: 数えない（セーブと結び付けて再開）でよいか | 数えない |
| 7 | 合計タイマーが先行しているときの色: 青（提案）／白 | 青 |
| 8 | R2: バケット作成とAPIトークン発行をオーナーが行う（希望があれば、接続済みの Cloudflare ツールでバケット作成までは代行できる） | — |
| 9 | 自動ローテーション時の枠: 終わるまで使う／N時間ごとに中断して再開 | N時間ごと |
| 10 | AGENTS.md の半熟専用の例外（左余白カード等）に `hanjuku-speedrun` を含めてよいか | 含める |

## 11. リスクと対策

| リスク | 対策 |
|---|---|
| 決め打ちの置き換え漏れで、スピードランが半熟エンジンとして扱われない、または元コーナーの判定が変わる | 分類表（§2.2）でレビューする。既存テストを無修正で通す。`hanjuku-speedrun` で新規ゲームを始める統合テストを足す |
| 再開で元コーナーのセーブを拾う | 候補探索をゲーム名で限定し、テストで固定する |
| ホットロードで試走の途中にBOT版が変わる | 区間ごとにBOT版を記録する。比較のときは同じ版どうしを選べるようにする |
| ミリ秒表示だが精度は約1秒 | 記録方式を固定して比較に使う。コメントのルールメモにも書く |
| ログ量が予想より多い | spool上限で必ず止まる。Phase 1 の最初の1日で実測し、定期画像の間隔とトレースの項目を調整する |
| R2が使えない・資格情報が無い | 送らずに上限管理だけ行う。ゲームとタイマーは影響を受けない |
| 1試走が数日かかり、元コーナーを長く止める | Phase 1 は手動なので、オーナーが起動・停止を決める。Phase 2 で枠の長さを決める（§10-9） |
