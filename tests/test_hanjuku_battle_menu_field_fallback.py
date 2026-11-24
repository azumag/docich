"""A frozen battle command menu in the field phase is closed, not confirmed.

g1190 2026-11-16 (local ch9): ブラウニー (5 HP, a rotten egg) fought
ジェラート (49 HP). The rescue menu (たまごをつかう/きりふだ/たいきゃく) stayed
open after the rescue budget ran out; the phase was back to ``field`` and the
no-input fallback pressed the field's generic A every 101 observations. A on
the open menu changed nothing, and the run ended ``screen_stalled``. B closes
the menu and the melee resumes (measured on the saved state: the fight ran
to its end).
"""
from docich.hanjuku import orchestrator


def test_field_phase_battle_menu_fallback_is_b():
    assert orchestrator.field_fallback_actions('battle_menu') == [orchestrator.pad('b')]
    assert orchestrator.field_fallback_actions('egg_battle_menu') == [orchestrator.pad('b')]


def test_other_field_fallbacks_are_unchanged():
    assert orchestrator.field_fallback_actions('map') == [orchestrator.pad('select')]
    assert orchestrator.field_fallback_actions('map_target') == [orchestrator.pad('b')]
    assert orchestrator.field_fallback_actions('text') == [orchestrator.pad('a')]
