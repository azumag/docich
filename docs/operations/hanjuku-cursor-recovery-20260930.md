# 商人確認画面の入力保留（#1369）

終了済みruntime `g478-a7d65415` の `hanjuku_frames/frame-006.png` は、商人の
「ふかくは きかねえよ。なにか かってかね〜かい?」と「うむッ! / いかんッ!」を表示する。
手の実測bboxは `(163,177,180,190)`。商人スプライトにも同じオレンジ色があり、
旧find_handは両者を一つのbboxへまとめて拒否し、yes_no_stepは無言で入力を保留した。
画面アニメーションが続くため同一フレーム停滞判定も発火しなかった。

v84は従来の単一bbox認識を維持し、広がる場合だけ8近傍の連結成分を測定する。
40画素以上、幅差12〜26・高さ差8〜18の候補が一つの場合だけ手として返す。
複数候補なら不明を維持し、確認画面で決定入力を推測しない。選択位置不明の場合は
situation_heldを記録する。実画像でkind=yes_no、selected=うむッ!、A計画を確認した。

回帰画像はownerが監視・ゲーム画面取得を承認した既存SSH補助経路で取得したゲーム画面のみ。
秘密情報やROM/saveは含まない。既存Actions証拠のlabels.jsonとは取得元が異なるため、
そのmanifestへ混ぜず専用テストで扱う。ログ再生・合成画像の成功は実機進行や勝率の証明ではない。

新worker/queue/providerは追加しない。runtime manifest/registry/health/queue契約は不変。
既存situation_heldとbot_versionを使うためtelemetry/diagnosticsの形式も不変。
入力計画のbot_versionで反映を照合し、共通配信を再起動しない。
