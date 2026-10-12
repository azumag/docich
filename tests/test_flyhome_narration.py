import json, threading, time
from types import SimpleNamespace
import pytest
from docich.flyhome.narration import AudioClient, SpeechJournal, LiveNarrator


def test_speech_does_not_wait_for_transport_and_preserves_order(tmp_path):
    started, release = threading.Event(), threading.Event()
    class Client:
        def send(self, text):
            started.set()
            assert release.wait(2)
            return {'ok': True, 'dedup': False}
    journal = SpeechJournal(tmp_path / 'speech.jsonl', Client())
    try:
        before = time.monotonic()
        journal.publish('9面を始めます。', 'start', 9)
        assert time.monotonic() - before < .2
        assert started.wait(1)
        journal.publish('通路を進みます。', 'progress', 9)
        assert journal.metrics()['audio_accepted'] == 0
    finally:
        release.set()
        journal.close()
    events = [json.loads(l) for l in journal.path.read_text(encoding='utf-8').splitlines()]
    assert [e['kind'] for e in events] == ['start', 'progress']
    assert len({e['id'] for e in events}) == 2
    assert journal.accepted == 2


def test_transport_failure_does_not_claim_delivery_or_log_exception_text(tmp_path):
    class Client:
        def send(self, text):
            raise TimeoutError('sensitive server diagnostic')
    journal = SpeechJournal(tmp_path / 'speech.jsonl', Client())
    journal.publish('9面を始めます。', 'start', 9)
    journal.close()
    receipt = journal.path.with_suffix('.receipts.jsonl').read_text(encoding='utf-8')
    assert 'sensitive' not in receipt
    assert json.loads(receipt)['status'] == 'pending'
    assert journal.accepted == 0 and journal.failed == 1


def test_unconfigured_transport_only_records_pending_local_speech(tmp_path):
    journal = SpeechJournal(tmp_path / 'speech.jsonl')
    journal.publish('9面を始めます。', 'start', 9)
    journal.close()
    assert journal.metrics()['audio_delivery'] == 'local_pending'
    assert not journal.path.with_suffix('.receipts.jsonl').exists()


def test_narration_only_speaks_explicit_findings_and_strategy_updates(tmp_path):
    journal = SpeechJournal(tmp_path / 'speech.jsonl')
    narrator = LiveNarrator(journal, 15)
    st = SimpleNamespace(t=10, attempt=1, phase='playing', x=40, y=80)
    for attempt in range(1, 5):
        st.attempt = attempt
        for phase in ('playing', 'dead', 'cleared'):
            st.phase = phase
            narrator.step(st)
    assert journal.published == 0
    text = '15面の低い通路は横倒しで滑って通ります。'
    event_id = narrator.note(text, 'strategy')
    assert event_id
    assert narrator.note(text, 'strategy') is None
    with pytest.raises(ValueError):
        narrator.note('試行を始めます。', 'level_start')
    journal.close()
    events = [json.loads(l) for l in journal.path.read_text(encoding='utf-8').splitlines()]
    assert len(events) == 1
    assert events[0]['kind'] == 'strategy'
    assert events[0]['attempt'] == 4


@pytest.mark.parametrize('url', ['http://user:secret@vm/', 'http://vm/?token=secret', 'file:///tmp/audio'])
def test_audio_url_does_not_embed_credentials(url):
    with pytest.raises(ValueError):
        AudioClient(url)
