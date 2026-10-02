"""Keyless synthetic tests; no live display, model, chat, or account access."""
from __future__ import annotations

import base64
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.llm.contracts import AgentSpec, DispatchRequest, DispatchResult, ProviderResult
from docich.llm.images import ImageAttachment, image_capable, user_content, validate_images
from docich.comment.screen_context import CaptureConfig, ScreenContextProvider, ScreenFrame, bounded_capture, read_scene
from docich.comment.screen_reply import generate_screen_reply, required_indices


@pytest.fixture
def image():
    return ImageAttachment.from_rgb(bytes([10, 120, 240]) * 6, 3, 2)


def row(index=1, need='required', status='jev', confidence=.9, category='general_question'):
    return {'index': index, 'screen_need': need, 'screen_status': status,
            'screen_confidence': confidence, 'category': category}


def config(tmp_path):
    return CaptureConfig(':99.0', 1280, 720, tmp_path / 'game_switch.json')


def scene_value():
    return {'schema_version': 2, 'phase': 'ready', 'revision': 9,
            'operation': None, 'request_id': None, 'candidate': None, 'previous': None,
            'active': {'game': 'nethack', 'generation': 4, 'runtime_id': 'game-4'}}


@pytest.mark.parametrize('shape', [(0, 1), (1, 0), (1281, 1), (1, True)])
def test_bad_rgb_dimensions(shape):
    with pytest.raises(ValueError):
        ImageAttachment.from_rgb(b'', *shape)


def test_image_body_reaches_the_multimodal_message(image):
    message = user_content('question', (image,))
    assert message[0] == {'type': 'text', 'text': 'question'}
    url = message[1]['image_url']['url']
    assert base64.b64decode(url.split(',', 1)[1]) == image.data
    assert user_content('question', ()) == 'question'
    assert base64.b64encode(image.data).decode() not in repr(image)
    assert 'data=' not in repr(image)


@pytest.mark.parametrize('value', [[], (object(),), (None,), ('x', 'y')])
def test_invalid_images(value):
    with pytest.raises(ValueError):
        validate_images(value)


@pytest.mark.parametrize('data,mime,w,h', [(b'not jpeg','image/jpeg',1,1),
                                         (b'x', 'image/png',1,1),
                                         (b'x' * 1048577,'image/jpeg',1,1)])
def test_bad_image_contract(data,mime,w,h):
    with pytest.raises(ValueError):
        ImageAttachment(data,mime,w,h)


def test_mime_and_dimensions_must_match(image):
    with pytest.raises(ValueError):
        ImageAttachment(image.data, 'image/jpeg', 2, 3)


def test_capability_does_not_add_or_guess_models():
    local = AgentSpec('local:vision', 'local', 'vision')
    amd = AgentSpec('amd:vision', 'amd', 'vision')
    assert not image_capable(local, {})
    assert image_capable(local, {'DOCICH_LLM_IMAGE_AGENTS':'local:vision'})
    assert not image_capable(amd, {'DOCICH_LLM_IMAGE_AGENTS':'amd:vision'})
    assert not image_capable(local, {'DOCICH_LLM_IMAGE_AGENTS':'local:other'})


def test_scene_projection(tmp_path):
    path=tmp_path/'scene.json'
    path.write_text(json.dumps(scene_value()))
    assert read_scene(path)==('nethack',4,'game-4',9)


@pytest.mark.parametrize('mutate', [lambda s:s.update(schema_version=True),
                                  lambda s:s.update(phase='starting'),
                                  lambda s:s.update(revision=True),
                                  lambda s:s.update(active=None),
                                  lambda s:s.update(candidate={}),
                                  lambda s:s['active'].update(generation=True),
                                  lambda s:s['active'].update(runtime_id='bad\nvalue')])
def test_untrusted_scene_rejected(tmp_path,mutate):
    path=tmp_path/'scene.json'; state=scene_value(); mutate(state)
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError): read_scene(path)


def test_scene_symlink_and_oversize(tmp_path):
    path=tmp_path/'scene.json'; path.write_text('{}')
    link=tmp_path/'link'; link.symlink_to(path)
    with pytest.raises(ValueError): read_scene(link)
    path.write_bytes(b'x'*65537)
    with pytest.raises(ValueError): read_scene(path)


def test_duplicate_scene_keys_rejected(tmp_path):
    p=tmp_path/'scene.json'; p.write_text('{"phase":"ready","phase":"ready"}')
    with pytest.raises(ValueError): read_scene(p)


def capture_env(tmp_path):
    return {'COMMENT_SCREEN_SOURCE':'direct_x11', 'COMMENT_SCREEN_CAPTURE_APPROVED':'1',
            'COMMENT_SCREEN_DISPLAY':':99.0', 'COMMENT_SCREEN_SIZE':'1280x720',
            'SOREN_DIRECT_STREAM_DISPLAY':':99.0', 'SOREN_DIRECT_STREAM_SIZE':'1280x720',
            'COMMENT_SCREEN_SCENE_FILE':str(tmp_path/'scene.json')}


@pytest.mark.parametrize('key', ['COMMENT_SCREEN_SOURCE','COMMENT_SCREEN_CAPTURE_APPROVED',
                                 'COMMENT_SCREEN_DISPLAY','COMMENT_SCREEN_SIZE',
                                 'SOREN_DIRECT_STREAM_DISPLAY','SOREN_DIRECT_STREAM_SIZE',
                                 'COMMENT_SCREEN_SCENE_FILE'])
def test_capture_requires_explicit_matched_config(tmp_path,key):
    env=capture_env(tmp_path); del env[key]
    with pytest.raises((ValueError,TypeError)): CaptureConfig.from_env(env)


def test_capture_command_is_video_only_and_bounded(tmp_path):
    cfg=CaptureConfig.from_env(capture_env(tmp_path)); cmd=cfg.command()
    assert cmd[0]=='ffmpeg'
    assert cmd[cmd.index('-i')+1]==':99.0+0,0'
    assert cmd[cmd.index('-frames:v')+1]=='1'
    assert '-an' in cmd and 'pulse' not in cmd
    assert 'rtmp' not in ' '.join(cmd)
    assert replace(cfg,width=3840,height=2160).output_size==(1280,720)


def test_provider_takes_only_one_image_and_detects_change(tmp_path):
    cfg=config(tmp_path)
    read=Mock(side_effect=[('n',1,'a',1),('n',2,'b',2)])
    capture=Mock(return_value=bytes(1280*720*3))
    provider=ScreenContextProvider(cfg, reader=read, capture=capture, clock=lambda:1.0)
    with pytest.raises(ValueError, match='scene_changed'): provider.take()
    capture.assert_called_once()


def test_freshness_and_backwards_clock(tmp_path,image):
    now=[10.0]; read=Mock(return_value=('n',1,'a',1))
    provider=ScreenContextProvider(config(tmp_path),reader=read,clock=lambda:now[0])
    frame=ScreenFrame(image,('n',1,'a',1),100.0,10.0)
    assert provider.current(frame)
    now[0]=15.1; assert not provider.current(frame)
    now[0]=9.9; assert not provider.current(frame)
    now[0]=10.1; read.side_effect=ValueError('state inaccessible')
    assert not provider.current(frame)


def test_process_timeout_and_large_output_are_bounded():
    with pytest.raises(ValueError, match='capture_timeout'):
        bounded_capture([sys.executable,'-I','-S','-c','import time; time.sleep(5)'],3,.1)
    with pytest.raises(ValueError, match='capture_oversize'):
        bounded_capture([sys.executable,'-I','-S','-c','import sys; sys.stdout.buffer.write(b"x"*100)'],3,.5)
    assert bounded_capture([sys.executable,'-I','-S','-c','import sys; sys.stdout.buffer.write(b"abc")'],3,.5)==b'abc'


LOCAL=AgentSpec('local:vision','local','vision')
TEXT=AgentSpec('amd:text','amd','text')
ENV={'COMMENT_SCREEN_CONTEXT_ENABLED':'1','DOCICH_LLM_IMAGE_AGENTS':'local:vision'}


class RecordingDispatcher:
    def __init__(self, replies=()):
        self.requests=[]; self.budgets=[]; self.replies=list(replies)
    def dispatch(self, request, *, overall_timeout_sec):
        self.requests.append(request); self.budgets.append(overall_timeout_sec)
        if self.replies: return self.replies.pop(0)
        return DispatchResult(0, output='reply', last_agent=request.agents[0].raw,
                              images_sent=len(request.images))


def factory(image):
    provider=Mock()
    provider.take.return_value=ScreenFrame(image,('n',1,'a',1),100.0,10.0)
    provider.current.return_value=True
    provider.reader.return_value=('n',1,'a',1)
    return provider


def request():
    return DispatchRequest('COMMENT','original prompt',(TEXT,LOCAL))


def test_off_is_byte_for_byte_old_request(image):
    dispatch=RecordingDispatcher(); make=Mock()
    value=generate_screen_reply(request(),[row()],env={},dispatcher=dispatch,provider_factory=make)
    assert dispatch.requests==[request()]
    assert value.screen_status=='disabled'
    make.assert_not_called()


@pytest.mark.parametrize('need,status,confidence', [('not_required','jev',.9),
                                                  ('uncertain','low_confidence',.2),
                                                  ('required','timeout',None),
                                                  ('required','jev',True),
                                                  ('required','jev',float('nan'))])
def test_non_required_never_captures(need,status,confidence):
    dispatch=RecordingDispatcher(); make=Mock()
    out=generate_screen_reply(request(),[row(need=need,status=status,confidence=confidence)],
                              env=ENV,dispatcher=dispatch,provider_factory=make)
    make.assert_not_called(); assert not dispatch.requests[0].images
    assert out.result.ok


def test_required_batch_shares_one_frame_and_only_capable_chain(image):
    dispatch=RecordingDispatcher(); p=factory(image)
    rows=[row(),row(index=2),row(index=3,need='not_required')]
    result=generate_screen_reply(request(),rows,env=ENV,dispatcher=dispatch,provider_factory=lambda:p)
    p.take.assert_called_once()
    sent=dispatch.requests[0]
    assert sent.agents==(LOCAL,) and sent.images==(image,) and sent.image_guard()
    assert 'コメント番号=1,2' in sent.prompt
    assert 'original prompt' in sent.prompt
    assert result.result.images_sent==1 and result.required_count==2
    assert 'base64' not in json.dumps(result.metrics()) and 'original prompt' not in repr(result)


def test_no_capable_model_is_text_only_without_capture():
    d=RecordingDispatcher(); make=Mock()
    result=generate_screen_reply(request(),[row()],env={'COMMENT_SCREEN_CONTEXT_ENABLED':'1'},
                                dispatcher=d,provider_factory=make)
    assert result.screen_status=='no_image_model'
    assert '確認できていません' in d.requests[0].prompt
    assert d.requests[0].agents==request().agents
    make.assert_not_called()


def test_capture_failure_keeps_batch_text_reply(image):
    d=RecordingDispatcher(); p=factory(image); p.take.side_effect=RuntimeError('PRIVATE_DATA')
    result=generate_screen_reply(request(),[row()],env=ENV,dispatcher=d,provider_factory=lambda:p)
    assert result.result.ok and result.screen_status=='capture_unavailable'
    assert not d.requests[0].images and 'PRIVATE_DATA' not in repr(result)


@pytest.mark.parametrize('image_result',[DispatchResult(1,failure_kind='timeout'),
                                         DispatchResult(0,output='pretended to see image',images_sent=0)])
def test_failed_or_silently_dropped_image_gets_truthful_text_fallback(image,image_result):
    d=RecordingDispatcher([image_result,DispatchResult(0,output='text reply')]); p=factory(image)
    result=generate_screen_reply(request(),[row()],env=ENV,dispatcher=d,provider_factory=lambda:p)
    assert len(d.requests)==2 and d.requests[0].images and not d.requests[1].images
    assert '【画面資料】' not in d.requests[1].prompt
    assert '確認できていません' in d.requests[1].prompt
    assert result.result.output=='text reply' and result.result.images_sent==0
    assert d.budgets[1]<=d.budgets[0]


def test_scene_change_after_generation_discards_reply(image):
    d=RecordingDispatcher(); p=factory(image); p.reader.return_value=('n',2,'b',2)
    result=generate_screen_reply(request(),[row()],env=ENV,dispatcher=d,provider_factory=lambda:p)
    assert not result.result.ok and result.screen_status=='scene_changed_before_delivery'
    assert result.result.output==''


def test_outer_retry_does_not_recapture(image):
    d=RecordingDispatcher(); make=Mock()
    result=generate_screen_reply(request(),[row()],env={**ENV,'COMMENT_SCREEN_CAPTURE_ATTEMPT':'2'},
                                dispatcher=d,provider_factory=make)
    assert result.screen_status=='retry_without_capture'
    make.assert_not_called()


@pytest.mark.parametrize('rows',[[],[{'index':True}],[row(index=2)],[row(),row()]])
def test_malformed_classification_fails_closed_without_losing_reply(rows):
    d=RecordingDispatcher(); make=Mock()
    result=generate_screen_reply(request(),rows,env=ENV,dispatcher=d,provider_factory=make)
    assert result.result.ok and result.screen_status=='invalid_classification'
    make.assert_not_called()


@pytest.mark.parametrize('category', ['card_gacha','raid','subscription','stream_goal','bits'])
def test_notifications_never_request_a_screen(category):
    assert required_indices([row(category=category)])==()


@pytest.mark.parametrize('timeout',[0,-1,True,float('nan'),float('inf'),301])
def test_invalid_total_budget(timeout):
    with pytest.raises(ValueError):
        generate_screen_reply(request(),[row()],env=ENV,dispatcher=RecordingDispatcher(),
                              overall_timeout_sec=timeout)


def test_mutable_bare_local_alias_is_not_a_verified_capability():
    spec = AgentSpec('local', 'local', 'vision')
    assert not image_capable(spec, {'DOCICH_LLM_IMAGE_AGENTS': 'local'})


@pytest.mark.parametrize('key', ['candidate', 'previous', 'operation', 'request_id'])
def test_partial_scene_projection_is_not_a_valid_snapshot(tmp_path, key):
    state = scene_value()
    del state[key]
    path = tmp_path / 'scene.json'
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        read_scene(path)


def test_unknown_attachment_delivery_is_not_reported_as_zero_requests(image):
    dispatcher = RecordingDispatcher([DispatchResult(1, failure_kind='timeout'),
                                      DispatchResult(0, output='text reply')])
    provider = factory(image)
    reply = generate_screen_reply(request(), [row()], env=ENV, dispatcher=dispatcher,
                                  provider_factory=lambda: provider)
    assert reply.metrics()['image_dispatch_requested'] is True
    assert reply.metrics()['reply_images_sent'] == 0
    assert 'images_sent' not in reply.metrics()


def test_signal_handlers_restored_when_spawn_fails(monkeypatch):
    import signal
    import docich.comment.screen_context as context
    original = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(context.subprocess, 'Popen', Mock(side_effect=OSError('unavailable')))
    with pytest.raises(OSError):
        bounded_capture(['not-a-real-command'], 3, .5)
    assert {s: signal.getsignal(s) for s in original} == original


def test_empty_image_contract_does_not_import_pillow(monkeypatch):
    import builtins
    original = builtins.__import__
    def reject_pillow(name, *args, **kwargs):
        if name == 'PIL' or name.startswith('PIL.'):
            raise ImportError('intentionally absent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject_pillow)
    validate_images(())
    assert user_content('unchanged', ()) == 'unchanged'
