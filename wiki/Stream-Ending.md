# 配信の明示終了

FFmpeg から Twitch へ RTMP 配信している環境で、配信を止める際に FFmpeg や通信を
単純に強制切断すると、Twitch 側が「意図的な終了」ではなく「回線断」と判断し、
配信がすぐ OFF LINE にならない問題がある。ここでは FFmpeg を **kill せず正常終了**
させ、RTMP の終了処理 (`FCUnpublish` / `deleteStream`) を確実に走らせる方法をまとめる。

> これは Soren 本番 (`soviet_now` の `lib/direct_stream.py`) で実装・実測した手順を
> docich の知見として共有したものである。docich 自体の停止コマンド
> (`docich stop` / `docich down`) が内部で何をしているかは「運用」を見てほしい。

## なぜ FFmpeg に `q` を送るのか

FFmpeg は通常コンソール入力を監視しており、**stdin に `q` を送ると正常終了する**。
この正常終了処理の中で RTMP 出力が `rtmp_close()` され、送信側ストリームに対して

- `FCUnpublish` (サーバー側にストリームの破棄を通知)
- `deleteStream` (RTMP ストリームを削除)

が生成・送信される。これが Twitch 側に「配信終了」として伝わり、即座に OFF LINE になる。

一方、`kill -9` / SIGKILL / ソケットだけ閉じる等で強制終了すると、この正常終了処理が
飛ばされ、Twitch は「回線断」とみなす。Disconnect Protection 等の待ち時間が発生し、
LIVE 状態が長く残る原因になる。

## 停止シーケンス（優先順位）

1. **stdin に `q` を送る** (第一選択)
2. 一定時間終了しなければ **SIGINT** (または SIGTERM)
3. それでも終了しなければ **SIGKILL** (最終フォールバックのみ)

```pseudo
async function stopStream() {
    if (!ffmpegProcess) return;

    // 1. FFmpeg へ正常終了要求
    ffmpegProcess.stdin.write("q\n");

    // 2. プロセスの正常終了を待つ
    if (await waitForExit(qTimeout)) return;

    // 3. 応答しない場合のみ SIGTERM 等
    sendSIGTERM(ffmpegProcess);
    if (await waitForExit(timeout)) return;

    // 4. 最終手段のみ強制終了
    sendSIGKILL(ffmpegProcess);
}
```

### stdin を利用できるか

`q` を使うには次の条件が必要。

- FFmpeg 起動時に **`-nostdin` を付けない** (付けると stdin 操作が無効化される)
- 停止処理が FFmpeg の **stdin pipe を保持**している (PIPE で起動する)
- **stdin をメディア入力に使っていない** (`some | ffmpeg -i pipe:0` のような構成では
  `q` を送れないため、信号方式にフォールバック)

docich の `build_ffmpeg_cmd` は `-nostdin` を使っていないため、stdin を PIPE で
起動すれば `q` で停止できる。Soren の `lib/direct_stream.py` は stdin を PIPE 化し、
停止時に `q\n` → SIGINT → SIGKILL の順で停止するよう実装済み。

## 実測結果 (Soren 本番)

`lib/direct_stream.py` の `_graceful_stop_ffmpeg` で `q` を送ったところ、Twitch の
公開 GQL (`user(login: ...) { stream }`) で確認して **約15秒で `live=False`** になった。
強制切断の場合は Disconnect Protection の最大90秒待ちが観測されることがあり、
`q` による正常終了はこれよりもはるかに速い。

## 受け入れ条件

1. 「配信終了」操作で FFmpeg を直接 kill しない。
2. stdin が利用可能なら `q\n` で終了する。
3. stdin が利用できない場合は SIGINT/SIGTERM を利用する。
4. 一定時間終了しなかった場合のみ強制終了をフォールバックとして使用する。
5. 正常終了時に FFmpeg プロセスが完全終了する。
6. RTMP の正常 close 処理が呼ばれることをログ等で確認する。
7. Twitch 上で配信が速やかに OFF LINE になるか実測する。
8. `q` 終了でも LIVE が残る場合は、FFmpeg 以外のリレー・再接続・Twitch 状態反映
   部分を追加調査する。

## docich での停止コマンドとの関係

- `docich stop`: 現在のゲーム (`game`/`agent` ウィンドウ) だけを止める。**配信は継続**。
- `docich down`: tmux セッションごと全ウィンドウを落とす。`supervise.py` は子プロセスへ
  SIGTERM → SIGKILL の順で停止する。
- `docich up`: `display` / `audio` / `stream` を (無ければ) 作る。

「配信だけを明示終了したい」場合は、上記の `q` 方式を stream プロセスへ適用する。詳細は
[[日常運用|Operations]] の「停止と再起動」も参照。
