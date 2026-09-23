	【配信UI説明メモ】
	- 左のグラフウィンドウ: show_status_g.sh（内部で status_dashboard.py を表示）
	  主な内容: Header, Score Timeline, Score Distribution, Strategy Comparison, Decision Patterns
	- 右のステータスウィンドウ: show_status.sh
	  主な内容: loop/worker稼働, improve状態, キュー負荷, コメント生成/再生状態, live state/score/pieces
	- 通常時はメリケンAIは動いていない
	- メリケンAI（アメリカ製AI）は、中華AIが戦略改善に入った時だけ代打として起動する
	- その改善中だけ、メリケンAIがメイン画面でソ連ゲーム91（対戦版）をプレイする
	- 視聴者がメリケンAIについて聞いてきたら「通常時はいま待機中で、改善時だけ出てきます」と説明すること