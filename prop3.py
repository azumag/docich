from pathlib import Path
p = Path('src/docich/hanjuku_commentary.py')
t = p.read_text()
t = t.replace("""            'game_over': '今回はゲームオーバーとなりました。',""",
"""            'game_over': '今回はゲームオーバーとなりました。',
            'game_clear': '今回はクリアエンディングまで到達しました。',""", 1)
t = t.replace("""    elif terminal_reason in {'game_over', 'screen_stalled', 'input_stalled', 'manual_saved_stop', 'manual_forced_stop'}:""",
"""    elif terminal_reason in {'game_over', 'game_clear', 'screen_stalled', 'input_stalled', 'manual_saved_stop', 'manual_forced_stop'}:""", 1)
p.write_text(t)
print('commentary ok')
