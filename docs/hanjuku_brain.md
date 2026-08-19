# 半熟英雄 brain 設計 (Phase 2)

`config/games/hanjuku-hero.toml` の `[agent]` から起動される **外部 brain コマンド**の設計書。
docich 本体との境界は architecture.md §6 (CommandBrain) のまま変えない:
**stdin = Observation JSON 1件、stdout = Action JSON、毎サイクル新規プロセス**。
記憶・知識・LLM 呼び出しはすべて brain スクリプト側の責務である。

- 【確認済】= このリポジトリ/コンテナで裏取り済み
- 【要検証】= VM 実機 (claude CLI 認証・ROM あり) での検証が必要

## 0. 置き場所の決定

multi_repo_plan.md §5.3 の未決事項を次のとおり決定する:
**brain 本体は docich 側 `brains/hanjuku/` に置き、知識データはサブモジュール
`games/hanjuku-sfc-speedrun/` を読み取り専用で参照する**。

理由: brain は docich の Observation/Action 契約 (docich が所有・変更する) に強く依存する一方、
hanjuku-sfc-speedrun は RTA チャート・ゲーム機構データの純粋な知識リポジトリとして保つため
(soviet_now と同じく docich からは変更しない)。

```
brains/hanjuku/
├── brain.py            # エントリポイント (python3 標準ライブラリのみで動作)
└── __init__.py         # (空。テストからの import 用)
run/brain/hanjuku/      # brain の状態 (gitignore 済みの run/ 配下)
├── state.json          # {"stage": 1, ...} 進行状態
├── notes.md            # LLM 自身の申し送りメモ (末尾8000字で切り詰め)
├── brain.log           # 1サイクル1行の JSONL (ts/backend/latency/actions/err)
└── fake_cursor         # fake モードの JSONL 読み出し位置
```

## 1. docich との契約 (変更点は最小)

- 入力: Observation JSON (`kind="screenshot"`, `screenshot=<PNGパス>`)。【確認済】retroarch アダプタは
  `run/screenshots/latest.png` に保存したパスを渡す。
- 出力: `{"actions": [...]}` のみを stdout へ (docich 側 `parse_actions` がそのまま解釈する)。
  note / state_patch は brain 内部で消費し、stdout には出さない。
- 終了コード: 0=正常 (actions 空も可) / 2=入力不正 / 3=LLM バックエンド失敗 / 4=応答解析失敗。
  非0 のとき docich CommandBrain は警告ログ + 空アクションで継続する (fail-soft。【確認済】brains.py)。
- **docich 側の変更は1点だけ**: CommandBrain が brain を `cwd=repo_root` で起動する
  (`procs.run` に `cwd` パラメータを追加)。tmux セッションの作業ディレクトリに依存せず、
  `command = ["python3", "brains/hanjuku/brain.py"]` の相対パスと、brain 内の相対パス参照を安定させる。
- brain は docich のスキーマを複製しない: 先頭で `sys.path.insert(0, "<repo>/src")` して
  `docich.actions.parse_actions` を再利用し、自分の出力を送出前に自己検証する。

## 2. LLM バックエンド (3系統)

環境変数 `DOCICH_BRAIN_LLM` で選択。docich 本体は関与しない (brain の内部事情)。

| 値 | 動作 | 用途 |
|---|---|---|
| `claude-cli` (既定) | `claude -p` を子プロセスで実行。プロンプトは stdin 渡し、スクリーンショットは**プロンプト中に絶対パスを記載**し claude 側の Read ツールに読ませる (画像対応)。argv: `[$DOCICH_BRAIN_CLAUDE_BIN, "-p", "--model", $DOCICH_BRAIN_MODEL, "--output-format", "text"]` | VM 本番 (claude CLI 認証済み環境)。【確認済】`-p` モードで **cwd (リポジトリ) 配下**の Read は無承認で通り、claude-opus-5 が RetroArch メニューのスクリーンショットを認識して fail-soft 判断まで一連動作した (2026-08-15 コンテナ実測、1サイクル約11秒)。**制約**: `state_dir` をリポジトリ外へ移すとスクリーンショットが cwd 外になり Read が権限拒否される (その場合も brain は note に理由を残し actions 空で正常終了する)。既定の `state_dir = "run"` のまま使うこと |
| `api` | Anthropic Python SDK (`anthropic`)。**選択時のみ関数内 import** し、無ければ導入手順を stderr に出して exit 3 (docich 本体の stdlib-only 制約を破らない)。画像は base64 の image ブロックで渡し、応答テキストは claude-cli と同一の解析経路に通す (v1 では `output_config` 構造化出力を使わず経路を1本に保つ。将来の改善候補) | SDK を入れた環境・レイテンシ比較用 |
| `fake:<path>` | LLM を呼ばず `<path>` の内容を応答として返す。`.json` = 毎回同一応答 / `.jsonl` = 1サイクル1行を順番に消費 (`fake_cursor` で継続、末尾で先頭へ戻る) | ユニットテスト・スモーク・E2E (ネットワーク不要) |

環境変数一覧 (すべて brain 内でのみ解釈):

| 変数 | 既定 | 意味 |
|---|---|---|
| `DOCICH_BRAIN_LLM` | `claude-cli` | バックエンド選択 |
| `DOCICH_BRAIN_MODEL` | `claude-opus-5` | モデル ID (claude-cli / api 共通) |
| `DOCICH_BRAIN_CLAUDE_BIN` | `claude` | claude CLI の実行ファイル |
| `DOCICH_BRAIN_LLM_TIMEOUT_S` | `100` | LLM 呼び出しの内部タイムアウト (docich の brain_timeout_s=120 より短く) |
| `DOCICH_BRAIN_MAX_ACTIONS` | `8` | 1サイクルに送出する行動数の上限 (超過分は切り捨ててログ) |
| `DOCICH_BRAIN_THINKING` | `off` | api モードのみ。`adaptive` で拡張思考を有効化 |

thinking を既定 off にする理由: 5〜10秒周期の行動ループではレイテンシが支配的で、
1手ごとの深い思考より周期の安定が優先。腰を据えた判断はメモ (notes.md) の蓄積で担う。

## 3. プロンプト構成

毎サイクル、以下を1つのユーザーメッセージに組み立てる (claude-cli / api で共通の組み立て関数):

1. **役割**: SFC「半熟英雄」を RTA チャートに沿って自動プレイする。SNES パッドの意味ボタン
   (`a/b/x/y/l/r/start/select/up/down/left/right`) を JSON で返す。
2. **操作契約**: 応答は JSON のみ。`hold_ms` 既定100ms、メニュー操作は1〜3手ずつ確実に。
   画面が読めない・判断できないときは `actions: []` か短い `wait` を返す。
3. **知識** (games/hanjuku-sfc-speedrun から選択注入。全536KBは注入しない):
   - 常時: `README.md` (約5KB。卵落ち判定・切り札の基礎) + `charts/overview.md` (約1KB)
   - 進行連動: `charts/{stage}.md` (state.json の `stage`、既定1。約3〜10KB)
   - `data/` の卵落ちテーブル (196KB)・キャラ表は v1 では注入しない (将来: brain が Python で
     CSV から該当行だけ抽出して注入する retrieval を追加)
4. **申し送りメモ**: `notes.md` の末尾 (これまでの LLM 自身の観察・方針)
5. **進行状態**: `state.json` の内容
6. **スクリーンショット**: claude-cli = パス記載 + 「Read で確認せよ」/ api = image ブロック
7. **応答形式**:

```json
{"note": "画面の要約と次の方針 (1-3行)", "state_patch": {"stage": 2}, "actions": [{"type": "pad", "buttons": ["a"], "hold_ms": 120}]}
```

- `note` (必須): 毎回 notes.md へタイムスタンプ付きで追記 → 末尾8000字に切り詰め。
- `state_patch` (任意): state.json へ浅いマージ (ステージ進行の自己申告)。
- `actions` (必須): docich の行動スキーマ。brain 側で `hold_ms ≤ 2000`・`wait ms ≤ 10000` に
  丸め、上限個数で切り捨ててから送出する。

## 4. 設定・配線

`config/games/hanjuku-hero.toml`:

```toml
[agent]
enabled = false                # 有効化はユーザー判断 (認証・ROM・コスト確認後)
brain = "command"
command = ["python3", "brains/hanjuku/brain.py"]
interval_ms = 7000             # LLM レイテンシ (3〜6秒) を織り込んだ周期
```

`enabled = true` にして `docich start hanjuku-hero` すれば agent window が
observe → brain → act を回す。手動デバッグは
`bin/docich obs hanjuku-hero | DOCICH_BRAIN_LLM=fake:tests/fixtures/hanjuku_fake_brain.jsonl python3 brains/hanjuku/brain.py`。

## 5. 検証計画

1. **ユニットテスト** (`tests/test_hanjuku_brain.py`、ネットワーク・X 不要):
   知識選択 (stage → チャート)、プロンプト組み立て、応答解析 (素の JSON / ```json フェンス / 不正)、
   行動の上限切り捨てと hold_ms/wait 丸め、notes 追記+切り詰め、state_patch マージ、
   fake の json/jsonl+cursor、claude-cli の argv 構築 (subprocess をモック)、api モード未導入時の
   エラーメッセージ、CommandBrain の cwd=repo_root (docich 側の追加テスト)。
2. **スモーク** (`scripts/smoke_brain.sh`、コンテナ実行可【確認済】の要素のみで構成):
   一時設定 (display :96 / audio off / stream null) → `up` → `obs hanjuku-hero` (ROM 不要で
   スクリーンショット観測が返る) → fake brain に流して actions を検証 (2回実行で cursor 前進も確認)
   → retroarch があれば ROM なしメニューを `dbus-run-session -- retroarch` で起動し、fake brain の
   出力を `docich send` で注入 → 前後スクリーンショットのバイト差で入力到達を確認 → 後始末
   (pkill -x retroarch、`down`、Xvfb :96 残存なし)。
3. **VM 実機**【要検証】: claude-cli モードで ROM 実プレイ (認証・ROM 配置・enabled=true が前提。§6)。

## 6. VM 側に残る作業 (ユーザー側)

1. `games/roms/hanjuku-hero.sfc` に自己吸い出し ROM を配置
2. VM 上で `claude` CLI が認証済みであること (`claude -p "ping"` が返る)
3. `hanjuku-hero.toml` の `[agent] enabled = true` に変更
4. (任意) `DOCICH_BRAIN_MODEL` でモデル変更、`DOCICH_BRAIN_LLM=api` + `pip install anthropic` で SDK 経路
