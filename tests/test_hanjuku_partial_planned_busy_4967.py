"""Issue #4967: actors picked from garrison_partial must be booked in planned_busy."""
from docich.hanjuku.strategy import replan


def test_partial_roster_spares_are_booked_across_retake_targets():
    # g1190-ch1v4 46-7: アルマムーン had an unread complete garrison (None)
    # while garrison_partial held multiple measured generals; all 5 retakes
    # used to bind the single strongest actor ('ブカティーニ').
    mem = {
        'chapter': 10,
        'month': '46-7',
        'tick': 937616,
        'captured': ['トルストリア', 'マカリア'],
        'lost': ['ファルゲ', 'コッカーニュ', 'カロナック', 'ケントルム', 'ジュアム'],
        'orders': {},
        'sorties': {},
        'launched_orders': {},
        '_records': [],
        'house_eggs': {},
        'general_location_unknown': [],
        'world': {
            'garrison': {
                'トルストリア': ['ジェラート'],
                'マカリア': ['バジル'],
            },
            'garrison_partial': {
                'アルマムーン': [
                    'どうし', 'ブカティーニ', 'クランベリー', 'キッシュ',
                    'ピスタチオ', 'タルタル', 'ビーツ',
                ],
            },
        },
    }

    candidates = list(replan.interim_candidates(mem).values())
    retakes = [o for o in candidates if o.get('purpose') == 'retake' and o.get('source') == 'アルマムーン']
    generals = [o['general'] for o in retakes]

    # Once partial spares from アルマムーン are exhausted, no duplicate actor is reused.
    assert len(generals) >= 4
    assert len(generals) == len(set(generals)), f'duplicate actors in retakes: {generals}'
    assert 'ブカティーニ' in generals
