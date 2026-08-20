# リポジトリルール（作業再開・引き継ぎ）

このリポジトリでの作業には、Claude Code / Codex いずれのエージェントも以下を守ること。

## 1. 作業再開時は handoff.md を読む

このディレクトリで新しいセッション・新しいタスクを始める際は、まず `./handoff.md` を読んで
現在の状況（目標・進行中の作業・既知の問題・次にやること）を把握してから着手する。

- `handoff` スキルがある場合は `/handoff load` を使ってよい。
- `handoff.md` の内容は鵜呑みにせず、`git status` やサービス状態など実際の状態と突き合わせてから
  作業を再開する（グローバル規律「検証してから報告する」に従う）。
- `handoff.md` に書かれた「完了」「反映済み」は、実測で裏取りできるまでは仮の情報として扱う。

## 2. 一段落したら handoff.md を更新する

タスクが一段落した（区切りのよい完了・大きな意思決定・セッション終了が近い等）タイミングで、
`handoff.md` を更新する。

- 既存の内容を丸ごと消さず、読んでから差分を反映する（新しいセクションとして追記でよい）。
- 事実ベースで書く。デプロイしただけ・テストが緑なだけを「完了/直った」と書かない。未検証は
  未検証と明記する。
- `handoff` スキルがある場合は `/handoff`（保存モード）を使ってよい。

## 3. 機密情報を書かない

ストリームキー・OAuth token・API キー・秘密鍵・push target などの機密情報は `handoff.md` は
もちろん、このリポジトリのどのファイルにも書かない。

## 4. handoff.md とメモリは適宜棚卸しする

`handoff.md` は追記が続くと肥大化する。一段落した節が古くなった・重複した・もう参照されない
と判断したら、消さずに要約に圧縮する、関連する節同士をまとめる、決着済みの調査は結論だけ残す
などして整理してよい（ただし「検証してから報告する」規律により、実測で確認した事実は
軽々しく削らない。要約する場合も結論の根拠が追えなくならないようにする）。Claude Code の
持続メモリ（`~/.claude/projects/.../memory/`）についても、内容が古くなった・間違っていた
と分かったら訂正・削除し、`MEMORY.md` の索引を同期させる。

## 5. VM 反映とリポジトリ同期は同時に行う

`/home/ubuntu/soren`（Oracle VM、git 管理外）へ本番反映する変更は、同時に `azumag/soviet_now`
の作業ブランチへコミット・push する。コミット前は「反映済み」と報告しない。VM とリポジトリが
乖離した場合は、新しい側（実際に動いている側）を正として同期し直す。

## 6. soviet_now の config.sh 既定値変更は worker 完全再起動で反映する

`/home/ubuntu/soren` の worker（radio/chat/improve_daemon）は起動時に `core/config.sh` の
`VAR="${VAR:-default}"` 既定値をシェル環境に取り込むため、**config.sh の既定値を変更しただけ
では USR1/HUP reload で反映されない**（設定済み値が優先される罠。2026-08-20 に prepass が
共通チェーンを無視し続けた実例あり）。変更時は必ず worker を完全再起動
（`kill -TERM` → supervisor 自動 respawn）し、ログ（`prepass agents=` 等）で実測確認する。
詳細は soviet_now の AGENTS.md「config.sh 既定値の変更は worker 完全再起動で反映する」を参照。

## 7. 作業中は codex_work_indicator.sh で粒度細かく進捗を報告する

エージェント種別を問わず（Codex / Claude Code / その他）、人手による調査・実装・検証・デプロイ等の**プロジェクト作業中は、進捗を粒度細かく可視化**するため `codex_work_indicator.sh`（VM では `/home/ubuntu/soren/codex_work_indicator.sh`、ローカルでは `games/soviet_now/codex_work_indicator.sh`）または webui `Overlay→作業中バナー`（`PUT /api/overlay/work_banner`）で作業中バナーを制御する。どのエージェントを使っても作業中はオーバーレイ表示する。

- **開始時**: `codex_work_indicator.sh start "タイトル" "本文"` でバナーを有効化。タイトルは 80字・本文は 240字以内。`start` はフェーズが変わるたびに再実行してタイトル/本文を更新する（例: `解析中 → 実装中 → 検証中 → デプロイ中`）。粗く `start` したまま放置しない。
- **終了時**: 検証・再起動確認まで含めて作業が完全に終わったら `codex_work_indicator.sh stop`（または webui で `無効化`）で必ず消灯する。最終応答・制御を返す前に消し忘れがないか確認する。
- **対象**: エージェントによるプロジェクト作業（調査・実装・検証・デプロイ）全て。自動の戦略改善ループ（strategy_runner）の進捗表示とは別であり、作業中バナーは `eventOverlay` の HTML のみを更新し `systemMsg` の表示/非表示は操作しない。エージェント種別（Codex か Claude か等）で表示有無を変えない。
- **VM 反映時**: `soviet_now` の変更を VM へ反映する場合も、VM 側で `codex_work_indicator.sh` を実行するか、webui の作業中バナーで同等の表示を行う。詳細は `soviet_now/AGENTS.md`「OBS Working Indicator」を参照。

## 8. 作業中はVMの読み上げキューにも進捗を入れる

`codex_work_indicator.sh` と連動し、**VM 側の `audio-worker` に作業内容を適切な丁寧さ（です・ます調で簡潔、過度なへりくだりは避ける）で読み上げさせる**。`7` の作業中バナー表示と同時に、適宜 VM の読み上げキューへ進捗を enqueue する。

- **自動**: `codex_work_indicator.sh` は `start` 時に `現在、タイトルの作業を進めています。詳細：本文。進捗があり次第お知らせします。`（240字丸め）、`stop` 時に `タイトルの作業が完了しました。ご確認ください。` を `lib/outbound_queue.sh` の `enqueue_audio_text` で `work_indicator` として呼ぶ。VM 上では `/home/ubuntu/soren/tmp/.comment_queue`、ローカル実行時は SSH（`ubuntu@129.146.54.105`）経由で VM 側にも enqueue する。へりくだりすぎる定型句（「お待たせしております」「何卒よろしくお願い申し上げます」「でございます」の連発等）は使わない。
- **粒度（大くくり）**: バナーは粒度細かく（フェーズごと）だが、**音声は大くくり**。同一タイトルは 300s 以内、タイトルが包含される軽微な更新は 180s 以内ならスキップ（`tmp/state/work_audio_last.json` で判定）。`enqueue_audio_text` の 300s dedup でも二重抑止。`stop`（完了）は常に読む。短時間に何度も呼ばれても spam にならない。
- **手動（直接 enqueue したい場合）**: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105 "cd /home/ubuntu/soren && source lib/outbound_queue.sh && enqueue_audio_text \"現在、…の作業を進めています。…\" work_indicator"`。ローカルの `audio_worker` が動いていなくても VM 側の `audio_worker` が再生する。手動でもです・ます調で簡潔に書く。過度なへりくだりは避ける。
- **対象**: `7` と同じく全てのプロジェクト作業。戦略改善ループ等の自動プロセスの進捗は対象外。
