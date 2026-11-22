"""docich#5130: 監査の即死帯しきい値はゲーム側の安全弁と一致していること。"""
from docich.hanjuku import month_audit as audit
from docich.hanjuku.judgment import battle as rules


def test_the_audit_floor_tracks_the_retreat_floor():
    assert audit.SAFETY_FLOOR_HP == rules.RETREAT_RESOURCE_FLOOR_HP
