"""Regression tests for standalone castle-name chapter resume.

CASTLE_STATUS needs the full 「<城> … LvN しょうぐん」 row, so the castle menu
(which only names a castle) never resolved the chapter and the chart stayed
unavailable for hours. A known castle name alone on a line is the same proof.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.flows import prompts
from docich.hanjuku.os_layer.screen import Screen


def _observe(monkeypatch, text, pending=True, chapter=None):
    seen = []
    monkeypatch.setattr(prompts, "observe_chapter_castle", lambda mem, name: seen.append(name))
    mem = {"tick": 1}
    if pending:
        mem["resume_chapter_pending"] = True
    if chapter:
        mem["chapter"] = chapter
    prompts.observe_events(Screen([], None, text, kind="unknown"), mem)
    return seen


def _names():
    return list(dict.fromkeys(n for group in chart.CASTLE_NAMES.values() for n in group if n))


def test_single_standalone_name(monkeypatch):
    name = _names()[0]
    assert _observe(monkeypatch, name) == [name]


def test_two_names_are_ambiguous(monkeypatch):
    names = _names()
    assert len(names) >= 2
    assert _observe(monkeypatch, "\n".join(names[:2])) == []


def test_substring_is_not_evidence(monkeypatch):
    assert _observe(monkeypatch, "prefix" + _names()[0] + "suffix") == []


def test_repeated_same_name_is_unambiguous(monkeypatch):
    name = _names()[0]
    assert _observe(monkeypatch, name + "\n" + name) == [name]


def test_without_pending_or_chapter_needs_evidence(monkeypatch):
    # No resume flag and a chapter already known: the standalone fallback stays
    # quiet so a status panel cannot rewrite a running chapter.
    assert _observe(monkeypatch, _names()[0], pending=False, chapter=4) == []


def test_chapterless_running_state_uses_the_fallback(monkeypatch):
    # A restart that lost the chapter (resume flag cleared but no chapter) still
    # needs the chapter, matching the full-row branch's condition.
    name = _names()[0]
    assert _observe(monkeypatch, name, pending=False, chapter=None) == [name]
