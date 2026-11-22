"""Early resource rescue opens only for a measured losing trade with a real resource."""

from docich import hanjuku_policy as p
import docich.hanjuku.judgment.battle as judgment
from docich.hanjuku_screen import Battle, Screen


def _battle(hp=70, enemy_hp=75, start_hp=100):
    return {
        'ally': 'カシュー', 'enemy': 'チコリ',
        'ally_hp': hp, 'enemy_hp': enemy_hp,
        'start_ally_hp': start_hp, 'start_enemy_hp': 90,
        'side': 'attack', 'planned_cards': [], 'own_egg_positive_observed': True,
        'cards_used': [], 'cards_selected': [], 'cards_missing': [],
        'card_evidence_version': 1,
        'card_soldiers_current': True, 'ally_soldiers': 0, 'enemy_soldiers': 0,
    }


def _memory(cur, egg_uses=2):
    eggs = {'カシュー': egg_uses} if egg_uses is not None else {}
    return {'chapter': 1, 'battle': cur, 'egg_uses': eggs}


def _set_balance(monkeypatch, ally_score=80, enemy_score=100):
    original = judgment._melee_balance

    def measured(cur):
        result = original(cur)
        result['ally_melee_score'] = ally_score
        result['enemy_melee_score'] = enemy_score
        return result

    monkeypatch.setattr(judgment, '_melee_balance', measured)


def test_early_rescue_requires_30_percent_hp_loss_and_a_measured_disadvantage(monkeypatch):
    _set_balance(monkeypatch)
    cur = _battle(hp=71)
    assert not judgment._early_resource_rescue_needed(_memory(cur), cur)
    cur = _battle(hp=70)
    assert judgment._early_resource_rescue_needed(_memory(cur), cur)

    _set_balance(monkeypatch, ally_score=110, enemy_score=100)
    cur = _battle(hp=70)
    assert not judgment._early_resource_rescue_needed(_memory(cur), cur)


def test_early_rescue_requires_a_confirmed_egg_or_damage_gated_carried_card(monkeypatch):
    _set_balance(monkeypatch)
    cur = _battle(hp=70)
    assert not judgment._early_resource_rescue_needed(_memory(cur, egg_uses=None), cur)

    monkeypatch.setattr(judgment, '_confirmed_carried_kit', lambda mem, battle: ['エンジェリン'])
    monkeypatch.setattr(judgment, '_card_damage_gate',
                        lambda mem, battle, card: {'allowed': False, 'needs_live_list': True})
    assert judgment._early_resource_rescue_needed(_memory(cur, egg_uses=None), cur)
    near_floor = _battle(hp=5)
    assert judgment._early_resource_rescue_needed(_memory(near_floor, egg_uses=None), near_floor)
    floor = _battle(hp=4)
    assert not judgment._early_resource_rescue_needed(_memory(floor, egg_uses=None), floor)

    monkeypatch.setattr(judgment, '_card_damage_gate',
                        lambda mem, battle, card: {'allowed': False, 'needs_live_list': False})
    assert not judgment._early_resource_rescue_needed(_memory(cur, egg_uses=None), cur)


def test_early_rescue_preserves_chart_boss_event_and_critical_policies(monkeypatch):
    _set_balance(monkeypatch)
    cases = []
    planned = _battle(hp=70); planned['planned_cards'] = ['クースカン']; cases.append(planned)
    boss = _battle(hp=70); boss['enemy'] = next(iter(judgment.chart.BOSSES.values())); cases.append(boss)
    event = _battle(hp=70); event['event_duel'] = True; cases.append(event)
    egg = _battle(hp=70); egg['egg_battle'] = True; cases.append(egg)
    critical = _battle(hp=4); cases.append(critical)
    spent = _battle(hp=70); spent['survival'] = {'early_resource_triggered': True}; cases.append(spent)
    for cur in cases:
        assert not judgment._early_resource_rescue_needed(_memory(cur), cur)

    normal = _battle(hp=70)
    monkeypatch.setattr(judgment, '_boss_tactics_allowed', lambda mem, battle: True)
    assert not judgment._early_resource_rescue_needed(_memory(normal), normal)


def test_battle_step_opens_the_bounded_early_rescue_before_melee(monkeypatch):
    _set_balance(monkeypatch)
    cur = _battle(hp=70, enemy_hp=75)
    mem = _memory(cur)
    screen = Screen([], None, '', kind='battle',
                    battle=Battle(cur['enemy'], cur['enemy_hp'], cur['ally'], cur['ally_hp']))

    actions = p.battle_step(screen, mem)

    assert actions == [p.pad('b')]
    assert mem['battle']['survival']['early_resource_triggered'] is True
    assert mem['battle']['survival']['opens'] == 1
    assert mem['_records'][-1]['decision'] == 'battle_survival_early_resource'


def test_early_egg_retry_shares_the_existing_hard_limit_and_requires_new_hp_loss(monkeypatch):
    _set_balance(monkeypatch)
    cur = _battle(hp=70)
    cur['survival'] = {'egg_attempted': True, 'egg_attempts': 2, 'egg_attempt_hp': 80}
    assert judgment._early_resource_rescue_needed(_memory(cur), cur)

    cur['survival']['egg_attempts'] = judgment.EGG_ATTEMPT_HARD_LIMIT
    assert not judgment._early_resource_rescue_needed(_memory(cur), cur)

    cur['survival']['egg_attempts'] = judgment.EGG_ATTEMPT_LIMIT
    cur['survival']['egg_attempt_hp'] = 60
    assert not judgment._early_resource_rescue_needed(_memory(cur), cur)


def test_early_card_probe_obeys_the_shared_spend_attempt_limit(monkeypatch):
    _set_balance(monkeypatch)
    cur = _battle(hp=70)
    cur['cards_spend_attempts'] = {'エンジェリン': judgment.CARD_SPEND_ATTEMPT_LIMIT}
    monkeypatch.setattr(judgment, '_confirmed_carried_kit', lambda mem, battle: ['エンジェリン'])
    monkeypatch.setattr(judgment, '_card_damage_gate',
                        lambda mem, battle, card: {'allowed': False, 'needs_live_list': True})
    assert not judgment._early_resource_rescue_needed(_memory(cur, egg_uses=None), cur)
