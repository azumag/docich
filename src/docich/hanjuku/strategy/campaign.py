"""戦略層: chapter progression and castle-label normalisation.

Detects chapter changes from observed castles and generals, resets
chapter-scoped state and repairs legacy home-castle labels.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart
from ...hanjuku_egg_reference import general_debut_chapter
from ..decision_log import _record
from ..os_layer.readers import STATUS_NAMES


def _enter_chapter(mem, chapter, *, reason, evidence=None):
    previous = mem.get('chapter')
    from ..os_layer.roster import fresh
    previous_roster = fresh(mem)
    if previous_roster and previous_roster.get('complete') is True:
        mem['recruit_payroll_pending'] = list(previous_roster['names'])
    # Route state belongs to the measured map of one chapter. Keep
    # run-wide counters/name evidence, never carry coordinates/orders.
    for key in ('map_registration', 'map_discovery', 'boss_victories', 'approx_fallback', 'approx_localize_miss',
                'active', 'anchor', 'goal_anchor_lock', 'attack', 'battle', 'battle_seen',
                'captured', 'card_override', 'rare_card_kit', 'strong_card_kit', 'dropper_card_kit', 'castle_defenders', 'boss_stock_restart', 'boss_stock_cancelled', 'rare_scan', 'rare_scan_month', 'cursor', 'egg_battle',
                'expect_menu', 'general_override', 'launched', 'menu_miss', 'month_exit', 'month_sub',
                'month_nav', 'house_nav', 'starter_party',
                'nav_last', 'nav_search', 'nav_search_leg', 'orders', 'picked', 'retries', 'retry_context', 'shop',
                'source_override', 'uncertain', 'month', 'soldiers_seen', 'soldiers_seen_key',
                'soldiers_seen_basis', 'soldiers_seen_scope', 'army_count_check',
                'order_context', 'sortie_general', 'sortie_actor_miss',
                'boss_defeated', 'boss_entry_hold',
                'chart_adjust', 'chart_plan', 'launched_orders', 'sorties', 'sortie_attempt',
                'garrison', 'unplaced_refuted', 'unplaced_refuted_castles', 'general_location_unknown', 'source_location_rechecks', 'recruit_verification', 'lost', 'owner_streak', 'source_miss', 'card_drop', 'card_miss',
                'nav_prev', 'nav_still', 'nav_pressed', 'unverified', 'off_castle',
                'target_miss', 'target_cancel', 'menu_hold', 'card_scroll', 'card_unreadable',
                'sortie_confirm_miss',
                'world_map_tick', 'world_map_due', 'world_map_wait', 'home_lost',
                'y_jump', 'y_jumps', 'y_jump_return', 'y_jumped', 'boss_absent', 'recall', 'recall_skip', 'recall_verification', 'retreat_rechecks', 'retreat_anonymous_budget',
                'near_goal', 'align_steps', 'unanchored', 'select_tick',
                'house', 'house_scan_tick', 'house_scan_month', 'house_field_scan', 'house_eggs', 'hero_repair_deferral',
                'roster_survey', 'roster_status_walk', 'roster_survey_scope', 'house_status_recheck', 'egg_zero_owners',
                'recruit_roster', 'recruit_roster_floor', 'recruit_roster_recheck', 'recruit_roster_attempts', 'recruit_field_scan_attempts', 'recruit_month_scan_attempts', 'castle_income', 'castle_ownership',
                'select_used', 'castle_verified', 'last_castle_hold',
                'egg_action', 'egg_key', 'egg_menu_stage', 'indep_menu',
                'indep_menu_key', 'indep_menu_action',
                'monster_menu_key', 'monster_menu_cursor', 'monster_menu_hold',
                'monster_menu_choice', 'monster_menu_choice_key', 'monster_menu_choice_scope', 'monster_menu_cursor_recheck', 'monster_panel'):
        mem.pop(key, None)
    mem['chapter'] = chapter
    mem['chapter_evidence'] = evidence or 'header'
    mem['variant'] = 'chart' if chart.orders(chapter) else 'chart_unavailable'
    _record(mem, 'chapter_seen', previous_stage=previous,
            observed_metric={'chapter': chapter, **({'evidence': evidence} if evidence else {})},
            resulting_stage=chapter,
            reason=reason)


def observe_chapter_castle(mem, castle):
    """Advance the chapter on a castle name that only the next chapter has.

    The month header reads 「2ねん5のつき」 (year, month) and never shows the
    chapter, so the header branch never fired: g421 beat クイーン at 14:41,
    fought at アルマムーン from 14:47 and navigated chapter 2 with chapter 1
    coordinates for 80 minutes (every Y jump and roof check missed).
    A name absent from this chapter and present in the next one is proof.
    """
    chapter = mem.get('chapter') or 0
    if (not chapter and mem.get('resume_chapter_pending') is True and castle
            and castle not in {'アルマムーン', 'あるまむーん', 'ほんじょう', 'ボス'}):
        # A restored party picker has no chapter label. Resolve from a fresh
        # full castle name unique across all chapters, never from the old bot.
        matches = [n for n, names in chart.CASTLE_NAMES.items()
                   if castle in names or castle in {STATUS_NAMES.get(x, x) for x in names}]
        if len(matches) == 1:
            mem.pop('resume_chapter_pending', None)
            _enter_chapter(mem, matches[0], evidence=castle,
                           reason='再開後の実城名が一つの章だけに存在するため章を確定する')
        return
    if not castle or not chapter:
        return
    here = set(chart.CASTLE_NAMES.get(chapter, ())) | {chart.home_castle(chapter), chart.boss_castle(chapter)}
    # The home castle is アルマムーン in every chapter (あるまむーん in 8), and the
    # chart now labels it the same way, so it can never read as "only the next
    # chapter has it" (g436 21:33: a defense of アルマムーン in chapter 1 advanced
    # the bot to chapter 2 cells and parked the cursor at sea).
    here |= {STATUS_NAMES.get(label, label) for label in here}
    if castle in here:
        return
    nxt = chapter + 1
    if castle in chart.CASTLE_NAMES.get(nxt, ()) or castle == chart.home_castle(nxt):
        _enter_chapter(mem, nxt, evidence=castle,
                       reason='次章にしかない城名を確認したため章を進め、前章の座標・出撃・購入状態を初期化')


def observe_chapter_general(mem, general):
    """Advance the chapter on an enemy general that only later chapters have.

    g454 08:24: どうし defeated クイーン, but the bot stayed on chapter 1 while
    ピオーネ and ヘラ (char.csv 話=2) attacked its castles. No chapter-2 castle
    name ever appeared, so ``observe_chapter_castle`` had no evidence and the
    bot navigated chapter 1 coordinates for 30+ minutes. The battle panel's
    enemy general is a chapter fact; a debut chapter later than the bot's
    current one proves the game advanced.
    """
    chapter = mem.get('chapter') or 0
    debut = general_debut_chapter(general)
    if not chapter or debut is None or debut <= chapter:
        return
    # The immediate next chapter's roster is proof by itself; a further jump
    # (a missed transition) needs this chapter's boss defeated as context.
    if debut != chapter + 1 and mem.get('boss_defeated') != chapter:
        return
    _enter_chapter(mem, debut, evidence=general,
                   reason='次章以降に初登場する敵将軍を確認したため章を進め、前章の座標・出撃・購入状態を初期化')


def _castle_label(mem, name):
    """The chart label for a castle name read from a message.

    The chart uses the game's own names, so this is the identity unless
    ``STATUS_NAMES`` holds a genuine alias; it existed because chapter 1's
    home castle was read as アルマムーン while the chart said ほんじょう.
    """
    cells = chart.castles(mem.get('chapter') or 0)
    if name in cells:
        return name
    return next((label for label, real in STATUS_NAMES.items() if real == name and label in cells), name)


def _repair_home_name_chapter(mem):
    """Undo the chapter 2 switch that only a defense of アルマムーン caused (v44-v60).

    Chapter entries now keep their evidence; a chapter 2 memory without it
    predates this fix, and the only such live game (g436, 21:33) was still
    in chapter 1. Its route state is reset for chapter 1 once.
    """
    # Only a game that v60 already saw in chapter 1 (its one-time repair flag)
    # and that then entered chapter 2 without recorded evidence.
    if ((mem.get('chapter') or 0) != 2 or 'chapter_evidence' in mem
            or not mem.get('home_alias_repaired')):
        return
    mem['chapter_evidence'] = 'reverted_home_name'
    _enter_chapter(mem, 1, evidence='reverted_home_name',
                   reason='本城の実名アルマムーンを次章の証拠と誤認して第2章にしていたため第1章へ戻す')
    mem['chapter_evidence'] = 'reverted_home_name'


def _repair_home_alias_failures(mem):
    """Undo order failures caused by the home castle chart/screen name check (v53-v59).

    g436 (21:19): the status check refused chapter 1's home castle as a
    mismatch three times per order and failed 1-A1, 1-V1, 1-C1... before any
    of them left. Orders from the home castle that failed without ever
    launching go back to pending once.
    """
    if mem.get('home_alias_repaired') or (mem.get('chapter') or 0) != 1:
        return
    mem['home_alias_repaired'] = True
    status = mem.get('orders') or {}
    launched = mem.get('launched_orders') or {}
    home = chart.home_castle(1)
    restored = []
    for order in chart.orders(1):
        step = order['step']
        if status.get(step) == 'failed' and order['source'] == home and step not in launched:
            status.pop(step, None)
            for key in ('source_miss', 'source_override', 'general_override'):
                (mem.get(key) or {}).pop(step, None)
            restored.append(step)
    if restored:
        mem['active'] = None
        _record(mem, 'orders_restored', observed_metric=restored,
                reason='本城の城名確認の誤判定（旧チャートラベルと画面実名の不一致）で失敗扱いになった指示を未実行に戻す')
