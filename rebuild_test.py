from pathlib import Path
pr = Path('/tmp/pr-test.py').read_text()
main = Path('/tmp/main-test.py').read_text()

# 1) main's _run_boss + live_sortie support
main = main.replace(
    "def _run_boss(runtime, count=401, *, battle_at=None, hp=False):",
    "def _run_boss(runtime, count=401, *, battle_at=None, hp=False,\n"
    "              live_sortie=False, sortie_age=10):", 1)
main = main.replace(
    "        if hp:\n            value['policy']['boss_hp_seen'] = {'hp': 50, 'castle': 'ボス', 'tick': index}\n",
    "        if hp:\n            value['policy']['boss_hp_seen'] = {'hp': 50, 'castle': 'ボス', 'tick': index}\n"
    "        if live_sortie:\n"
    "            value['policy']['tick'] = index + sortie_age\n"
    "            value['policy']['world']['sorties'] = {\n"
    "                'boss-en-route': {\n"
    "                    'status': 'en_route', 'general': 'G1', 'source': 'S',\n"
    "                    'target': 'ボス', 'purpose': 'attack', 'tick': index,\n"
    "                },\n"
    "                'boss-unconfirmed': {\n"
    "                    'status': 'launched_unconfirmed', 'general': 'G2', 'source': 'S',\n"
    "                    'target': 'ボス', 'purpose': None, 'tick': index,\n"
    "                },\n"
    "            }\n"
    "            value['policy']['world']['launched_orders'] = {\n"
    "                'boss-unconfirmed': {\n"
    "                    'target': 'ボス', 'source': 'S', 'general': 'G2',\n"
    "                    'purpose': 'attack', 'tick': index,\n"
    "                },\n"
    "            }\n", 1)

# 2) new test functions from the PR
def grab(src, name):
    start = src.index('def ' + name)
    nxt = src.find('\ndef ', start + 1)
    block = src[start:] if nxt < 0 else src[start:nxt]
    return block.rstrip() + '\n'

extra = []
for name in ('test_boss_phase_with_live_boss_attack_sorties_is_deferred_until_they_settle',
             'test_chapter_four_season_king_battle_counts_as_boss_evidence'):
    try:
        extra.append(grab(pr, name))
    except ValueError:
        print('missing', name)

main = main.rstrip() + '\n\n\n' + '\n\n'.join(extra)
Path('tests/test_hanjuku_month_audit.py').write_text(main)
print('rebuilt with', len(extra), 'extra tests')
