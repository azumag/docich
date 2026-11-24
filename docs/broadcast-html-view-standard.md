# 配信HTML view 標準（ブラウザ翻訳の抑止と session capability）

配信フレームに映る docich の HTML view は、視聴者向けの固定表示であり、単一言語で
意味が確定している。ブラウザの翻訳コントロールが右上に出ると、枠・予報パネル・
ランキングの上に重なり、翻訳された表示は「上位予想者のみ集計」のような文言の意味まで
変えてしまう。そこで、docich が配信のために開く Chromium には翻訳を出さないことを
**リポジトリ標準**とする。定義は `src/docich/broadcast_view.py` に一本化し、
`tests/test_broadcast_view_standard.py` が全 view に対して固定する。

## 1. Chromium 起動標準

すべての配信 HTML view は `adapters/program.py` の
`broadcast_browser_command(...)` を使い、`BROADCAST_TRANSLATE_ARGS` を必ず渡す。

- `--disable-translate`
- `--disable-features=Translate,TranslateUI,BackForwardCache`

Chromium は版によって legacy の `--disable-translate` と新しい `TranslateUI` feature
gate のどちらかしか見ないことがあるため、**両方**を落とす。片方だけの view を増やさない。

日本語ページはこれに加えて `--lang=ja-JP`（`JAPANESE_LANG_ARG`）を渡す。docich の
`paper-view` / `market-view` / `weather-view` / `tsuitate-view` / `yosou-view` /
`doushi-view` / `mystery-view` はすべて日本語ページである。

## 2. 配信HTMLの宣言

日本語ページは HTML の先頭で次を宣言する（`JAPANESE_PAGE_MARKERS`）。

```
<html lang="ja" translate="no">
<meta name="google" content="notranslate">
```

ブラウザが翻訳を提案するかどうかを見るのはページ側のこの2つであり、Chromium 起動引数
だけでは足りない。逆にページ側だけでも足りない。両方を満たす。

これは「翻訳させない」という宣言であって、閲覧者がブラウザ設定で翻訳することを禁じる
ものではない。配信フレームの既定表示から翻訳UIを外すだけである。

## 3. view session の capability

view は GameSwitch の世代session（tmux）で動く。**tmux は session の環境を server から
作り、これを起動した process の環境を継承しない。** したがって view の command が
必要とするものは `_game_session_env()` で明示的に渡す。

- import path: `broadcast_view_session_env()` が `PYTHONPATH=<checkout>/src` を必ず入れる。
  無いと pane が `ModuleNotFoundError` で即死し、`create_game_session_owned` が
  `#{session_id}` を読む前に session が消えて `no such session: $N` で switch が rollback する
  （Issue #1992）。
- capability: view 自身が制御API等を呼ぶ場合、`capability_keys=` で**レビュー済みの key だけ**を
  渡す。beta-control は `CAPABILITY_ENV_KEYS`（`DOCICH_BETA_CONTROL_SECRET` /
  `DOCICH_BETA_CONTROL_URL`）の2つだけで、WebUI の operator/viewer token は渡さない。
  無いと view は `ok:false` を返し続け、adapter の readiness が成立せず switch が
  deadline 超過で rollback する。

渡した値は tmux の session 環境（`-e`）に置かれ、session の終了とともに消える。同一ユーザー
からは `tmux show-environment` と `/proc` で読める点は、従来から同じ経路で渡している
ownership tag と同じ露出として許容する。増やす capability はレビュー済みの key だけに限り、
secret をログ・HTML・API 応答・子コーナー環境へは出さない。

## 4. 適用範囲と例外

- 対象: docich が配信のために開く HTML view（`ProgramViewAdapter` 系の
  `paper-view` / `market-view` / `weather-view` / `tsuitate-view` / `yosou-view` /
  `doushi-view` / `mystery-view`）。
- NetHack tiles GUI（`nethack_tiles_supervisor.py`）はゲーム自身のUIなので翻訳抑止の
  2引数だけを適用し、`--lang=ja-JP` は付けない。
- Soren91 の視聴は ffplay（動画）でブラウザではないため対象外。

## 5. 検証

`python3 -m pytest -q tests/test_broadcast_view_standard.py`

新しい HTML view を追加したら `BROADCAST_VIEWS` と `JAPANESE_PAGES` に足す。標準から
外れた view はこのテストで fail する。
