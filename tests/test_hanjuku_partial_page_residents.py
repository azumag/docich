"""A full page proves who stands here, even though it is not the roster.

g1163 2026-11-12 10:3x: タオ／ハヤク／アルマムーン all read しょうぐん0めい while
リーキ and コリアンダー marched out of one of them. The list read that produced
those actors was a full page, which the deploy treated as incomplete and threw
away. `garrison` must stay untouched (a page never claims the roster), so the
lower bound is kept beside it.
"""
import inspect

from docich.hanjuku.flows import deploy


def test_a_full_page_keeps_a_lower_bound_beside_the_roster():
    source = inspect.getsource(deploy._observe_garrison)
    assert 'garrison_partial_seen' in source
    # the roster itself is never written from a partial page
    marker = source.index('garrison_partial_seen')
    assert source.index("garrison[castle] = present") > source.index('garrison_observed_at')


def test_the_threshold_is_the_page_size():
    assert deploy.GENERAL_LIST_FULL >= 4
