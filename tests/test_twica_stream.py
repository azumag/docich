import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from PIL import Image
import pytest
from docich.twica_config import load_common_config
from docich.twica_overlay import OverlayPipe, SnapshotPublisher, SnapshotReader
from docich.twica_stream import compose_command, close_owned_group, RendererSupervisor, load_legacy

def base_command():
    return ['ffmpeg', '-hide_banner', '-f', 'x11grab', '-i', ':99.0+0,0',
            '-isync', '0', '-f', 'pulse', '-i', 'soren_null.monitor',
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264',
            '-a53cc', '1', '-vf', 'docichcc=socket=/run/user/1000/docich/ffmpeg-cc.sock',
            '-af', 'adelay=100:all=1,aresample=async=1:first_pts=0',
            '-c:a', 'aac', '-f', 'flv', 'rtmp://127.0.0.1:1935/soren/live']

def test_real_command_contract_keeps_audio_captions_and_control_stdin():
    original=base_command(); command=compose_command(original,12,1280,720,15)
    assert original==base_command()
    assert command.count('-i')==3 and 'pipe:12' in command and 'pipe:0' not in command
    assert '-vf' not in command
    graph=command[command.index('-filter_complex')+1]
    assert graph.startswith('[0:v:0][2:v:0]overlay=x=round(main_w/3):y=0')
    assert ',docichcc=socket=/run/user/1000/docich/ffmpeg-cc.sock[twica_video]' in graph
    assert command[command.index('-map')+1]=='[twica_video]'
    assert command[command.index('-map',command.index('-map')+1)+1]=='1:a:0'
    assert command[command.index('-af')+1]==original[original.index('-af')+1]
    assert command[-1]==original[-1]
    assert command[command.index('-a53cc')+1]=='1'
    assert command.count('-isync')==2

@pytest.mark.parametrize('bad', [
    ['ffmpeg','-i','one','-map','0:v:0'], base_command()+['-filter_complex','unexpected'],
    [x if x!='0:v:0' else '1:v:0' for x in base_command()], base_command()+['-vf','null']])
def test_unsupported_legacy_graph_fails_before_spawn(bad):
    with pytest.raises(ValueError): compose_command(bad,3,1280,720,15)

def test_renderer_environment_has_no_llm_or_stream_credentials(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','do-not-inherit')
    monkeypatch.setenv('TWITCH_STREAM_KEY','do-not-inherit')
    monkeypatch.setenv('SOREN_DIRECT_TWICA_OVERLAY_URL','https://example.test/overlay/fixture')
    cfg=load_common_config(tmp_path,{'DOCICH_TWICA_FRAME_DIR':str(tmp_path/'frames')})
    env=RendererSupervisor(cfg,SimpleNamespace(width=320,height=180),tmp_path)._environment()
    assert 'OPENAI_API_KEY' not in env and 'TWITCH_STREAM_KEY' not in env
    assert env['SOREN_DIRECT_TWICA_OVERLAY_URL'].endswith('/overlay/fixture')
    assert env['DOCICH_TWICA_AUDIO_SINK']=='soren_null'

def test_owned_renderer_cleanup_does_not_touch_unrelated_process(tmp_path):
    other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
    parent=subprocess.Popen([sys.executable,'-c','import subprocess,sys,time;subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);time.sleep(30)'],start_new_session=True)
    try:
        time.sleep(.1);close_owned_group(parent,timeout=.2)
        assert parent.poll() is not None and other.poll() is None
    finally: close_owned_group(other,timeout=.2)

def test_final_video_and_audio_continue_through_game_changes_and_renderer_expiry(tmp_path):
    ffmpeg=shutil.which('ffmpeg');assert ffmpeg
    width,height,fps=120,72,10
    backgrounds=[(10,20,100,255),(20,100,20,255),(80,10,80,255),(90,90,20,255),(20,90,90,255),(0,0,0,255)]
    base=tmp_path/'base.rgba';base.write_bytes(b''.join(bytes(c)*(width*height) for c in backgrounds))
    output=tmp_path/'out.mkv'
    with SnapshotPublisher(tmp_path/'overlay',width,height) as pub:
        image=Image.new('RGBA',(width,height))
        for x in range(48,72):
            for y in range(24,48):image.putpixel((x,y),(255,255,255,128))
        stamp=time.monotonic_ns();pub.publish(image.tobytes(),stamp)
        class Reader(SnapshotReader):
            count=0
            def read(self,now_ns=None):
                self.count+=1
                return super().read(stamp+(0 if self.count<=4 else self.ttl_ns))
        with OverlayPipe(Reader(tmp_path/'overlay',width,height),fps) as feed:
            original=[ffmpeg,'-v','error','-f','rawvideo','-pixel_format','rgba','-video_size',f'{width}x{height}',
              '-framerate',str(fps),'-i',str(base),'-f','lavfi','-i','sine=frequency=400:sample_rate=48000:duration=0.6',
              '-map','0:v:0','-map','1:a:0','-c:v','ffv1','-pix_fmt','bgr0','-c:a','pcm_s16le',
              '-frames:v','6','-t','0.6','-y',str(output)]
            child=subprocess.Popen(compose_command(original,feed.read_fd,width,height,fps,live_clock=False),
                pass_fds=(feed.read_fd,),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                feed.start();stdout,stderr=child.communicate(timeout=10)
                assert child.returncode==0,stderr.decode()
            finally:
                if child.poll() is None:close_owned_group(child,.2)
    metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
    assert {s['codec_type'] for s in metadata['streams']}=={'audio','video'}
    raw=subprocess.check_output([ffmpeg,'-v','error','-i',str(output),'-map','0:v:0','-pix_fmt','rgba','-f','rawvideo','pipe:1'])
    size=width*height*4;assert len(raw)==6*size
    for i,background in enumerate(backgrounds):
        result=Image.frombytes('RGBA',(width,height),raw[i*size:(i+1)*size])
        assert result.getpixel((1,1))[:3]==background[:3]
        pixel=result.getpixel((89,30))[:3]
        expected=tuple(round((255*128+c*127)/255) for c in background[:3]) if i<4 else background[:3]
        assert all(abs(a-b)<=1 for a,b in zip(pixel,expected))

def test_actual_soren_command_builder_contract_when_checkout_present():
    root=Path(os.environ.get('DOCICH_TWICA_SOREN_CHECKOUT','games/soviet_now'))
    path=root/'lib/direct_stream.py'
    if not path.exists():pytest.skip('full paired Soren checkout is required')
    legacy=load_legacy(path)
    config=legacy.load_config({'SOREN_STREAM_BACKEND':'ffmpeg','DOCICH_CC_ENABLED':'1'})
    for mode in ('live','record'):
        original=legacy.build_ffmpeg_command(config,mode=mode,output_path=Path('/tmp/test.mkv') if mode=='record' else None)
        command=compose_command(original,12,config.width,config.height,15)
        assert '[twica_video]' in command
        assert command[command.index('-af')+1]==original[original.index('-af')+1]
        assert 'docichcc=' in command[command.index('-filter_complex')+1]
