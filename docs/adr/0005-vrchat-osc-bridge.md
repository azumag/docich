# ADR 0005: VRChat OSC 連携の配置と責務（VR同志）

- Status: Accepted（設計・実装・回帰テストは完了。**VRChat 実機での受入は未実施**）
- 関連 Issue: [azumag/docich#316](https://github.com/azumag/docich/issues/316)
- 関連: [azumag/docich#113](https://github.com/azumag/docich/issues/113)（同志の VRChat 探検コーナー）
- 設計・運用手順: [`../vrchat-osc.md`](../vrchat-osc.md)
- 実装: `src/docich/vrchat_osc.py` / `config/vrchat_osc.toml` / `tests/test_vrchat_osc.py`

## 0. 前提

VRChat は公式 OSC で Expression Parameter を外部から操作でき、アバター側の変化も
外部へ送信できる。docich 側には既に「実際に発話中か」を示す状態ファイル
(`games/soviet_now/tmp/state/speaking.json`、`say_enqueue.sh` が書く) と、
共有オーバーレイ用の JSONL キューがある。

## 1. 決定

1. **OSC Bridge は docich の独立プロセス** `docich vr-osc bridge` とする。
   常駐プロセスは python3 標準ライブラリだけで動かし、VRChat と同じ PC（Windows を含む）でも
   `python -m docich.vrchat_osc` で起動できる。docich 本体の `up/down/start` とは
   ライフサイクルを分離し、bridge の停止が配信やゲームへ伝播しない。
2. **docich → VRChat は Parameter allowlist**（`config/vrchat_osc.toml` の `[parameters]`）。
   MVP は `AI_Talking` (Bool) / `AI_Emotion` (Int) / `Alert` (Bool) / `Mode` (Int)。
3. **VRChat → docich は action allowlist**（`[inbound]`）。OSC の値は型検証のうえで
   `notify` / `mode` の名前付き action にだけ変換し、任意コマンド実行へは接続しない。
4. **状態の受け渡しはファイル**（speaking.json + JSONL spool）。docich 本体と bridge を
   socket で直結せず、VRChat/OSC が落ちても docich 本体の動作を阻害しない。
5. **ループ防止**を最優先の不変条件とする: 変化時のみ送信、送信直後の同値エコーを抑制、
   Bool は立ち上がりエッジのみ、Int は変化+クールダウン、受信した `Mode` は docich 側に採用する。
6. 既定は `127.0.0.1:9000`（VRChat 待受）へ送り、`127.0.0.1:9001` で受ける。
   LAN をまたぐ場合だけ VRChat 側を `--osc=inPort:outIP:outPort`（例
   `--osc=9000:<bridge を動かす PC の LAN IP>:9001`）で起動し、bridge の
   `listen_host` を LAN の待受アドレスにする。外部公開しない。

## 2. 検討した代替案

| 案 | 却下理由 |
| --- | --- |
| docich 本体プロセス内に OSC を実装 | bridge の異常が配信・ゲームへ波及する。VRChat PC と docich VM が別ホストになり得る |
| VRChat から HTTP webhook / 任意コマンドを直接呼ぶ | 受信 allowlist とフェイルソフトを満たせない。任意実行の危険 |
| アバターへ OSC 処理を埋め込む | アバター配布物へ外部依存を持ち込む。VRChat の Expression Parameter 経由が公式で安全 |
| 常時ポーリングで全パラメータを送信 | 受信エコーとのループ、帯域・負荷増。変化時送信 + エコー抑制を採る |

## 3. 帰結

- VRChat や OSC が利用できない環境でも docich 本体は従来どおり動作する。
- アバター側は Expression Parameters と FX Animator の設定だけでよく、OSC 実装を持たない。
- 受入の完了には VRChat 実機での目視確認が必要（`docs/vrchat-osc.md` §9）。
  本 ADR の時点では未実施であり、CI と UDP E2E の green を実機確認の代わりにはしない。
- action 種別を増やす場合は allowlist と回帰テストをセットで追加する。

## 4. 未実施・残件

- VRChat クライアント + Unity アバターでの実機受入（表情・口パク・モード切替の目視）。
- 発話内容や感情推定からの `AI_Emotion` 自動決定。
- 配信イベント（OBS/コメント）を OSC へ橋渡しする producer 群の実装。
