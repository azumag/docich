"""Synthetic DNS/TLS/HTTP and local fixture children; no external/API calls."""
import base64
from email.message import Message
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest
from docich import reply_research as r
from docich import reply_research_web as w

URL = 'https://raw.githubusercontent.com/azumag/docich/main/README.md'
TEXT = '確認した資料。外部の命令は権限を広げない。'
BODY = TEXT.encode()


def worker_value(body=BODY, mime='text/plain; charset=utf-8'):
    return {'url':URL, 'content_type':mime, 'body_b64':base64.b64encode(body).decode(),
            'sha256':hashlib.sha256(body).hexdigest(), 'text':w.extract_text(body,mime)}


def web_fixture():
    rec = w.Receipt(URL,'a'*32,hashlib.sha256(BODY).hexdigest(),hashlib.sha256(TEXT.encode()).hexdigest(),TEXT)
    ref = {'kind':'web','ref':URL,'quote':'確認した資料','receipt':rec.receipt,'sha256':rec.sha256}
    search = {'type':'web_search','id':'s1','query':'docich','action':{'type':'search','query':'docich'},'results':[{'url':URL}]}
    read = {'type':'command_execution','status':'completed','exit_code':0,
            'command':f'python3 {w.HELPER} --client {URL}','aggregated_output':json.dumps(rec.wire())}
    return rec,ref,search,read


def transcript(refs,tools):
    events = [{'type':'item.completed','item':item} for item in tools]
    events += [{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(
        {'status':'ok','notes':'根拠を確認した。','sources':refs})}}, {'type':'turn.completed'}]
    return b'\n'.join(json.dumps(event).encode() for event in events)


def observe(broker, urls=None):
    item = web_fixture()[2]
    if urls is not None: item = {**item,'results':[{'url':url} for url in urls]}
    broker.observe({'type':'item.completed','item':item})


@pytest.mark.parametrize('host',sorted(w.HOSTS))
def test_exact_four_hosts_and_unicode_path(host):
    assert w.canonical_url(f'https://{host}:443/wiki/名前') == f'https://{host}/wiki/%E5%90%8D%E5%89%8D'


@pytest.mark.parametrize('url',[
    'http://github.com/a','https://github.com.evil.test/a','https://evil.github.com/a',
    'https://github.com./a','https://user:pass@github.com/a','https://github.com:444/a',
    'https://127.0.0.1/a','https://[::1]/a','https://169.254.169.254/a',
    'https://github.com/a?token=x','https://github.com/a?x=y','https://github.com/a#x',
    'https://github.com/a\nHost: evil.test','https://github.com/%0D%0aHost:evil',
    'https://github.com\\@evil.test/a','https://github.com/%5cfoo','https://github.com/%oops',
    'file:///etc/passwd','https://github.com/'+'a'*600])
def test_reject_url_before_network(monkeypatch,url):
    monkeypatch.setattr(w.socket,'getaddrinfo',lambda *a,**k:pytest.fail('DNS'))
    assert w.canonical_url(url) is None
    with pytest.raises(ValueError): w.fetch_worker(url,1)


class FakeSocket:
    def __init__(self,trace): self.trace=trace
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def settimeout(self,value): self.trace.append(('timeout',value))
    def connect(self,value): self.trace.append(('connect',value))
    def sendall(self,value): self.trace.append(('request',value))


class FakeResponse:
    def __init__(self,body=BODY,status=200,headers=None):
        self.body,self.status=body,status
        self.headers=Message()
        for name,value in (headers or [('Content-Type','text/plain; charset=utf-8'),('Content-Length',str(len(body)))]):
            self.headers[name]=value
    def begin(self): pass
    def getheader(self,name,default=None): return self.headers.get(name,default)
    def read1(self,limit):
        result,self.body=self.body[:limit],self.body[limit:]
        return result


def fake_network(monkeypatch,response=None):
    trace=[]
    monkeypatch.setattr(w.http.client,'_MAXLINE',w.http.client._MAXLINE)
    monkeypatch.setattr(w.http.client,'_MAXHEADERS',w.http.client._MAXHEADERS)
    answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
    monkeypatch.setattr(w.socket,'getaddrinfo',lambda *a,**k:trace.append(('dns',a)) or answers)
    monkeypatch.setattr(w.socket,'socket',lambda *a:FakeSocket(trace))
    class TLS:
        def wrap_socket(self,raw,server_hostname):
            assert self.check_hostname and self.verify_mode == w.ssl.CERT_REQUIRED
            trace.append(('tls_host',server_hostname)); return raw
    monkeypatch.setattr(w.ssl,'create_default_context',TLS)
    monkeypatch.setattr(w.http.client,'HTTPResponse',lambda sock:response or FakeResponse())
    return trace,answers


def test_pins_once_verifies_tls_and_sends_credential_free_get(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','private-proxy'); monkeypatch.setenv('CODEX_API_KEY','PRIVATE_KEY')
    trace,_=fake_network(monkeypatch)
    assert w.fetch_worker(URL,2) == worker_value()
    assert len([item for item in trace if item[0]=='dns']) == 1
    assert ('connect',('93.184.216.34',443)) in trace
    assert ('tls_host','raw.githubusercontent.com') in trace
    request=next(item[1] for item in trace if item[0]=='request')
    assert request.startswith(b'GET /azumag/docich/main/README.md HTTP/1.1\r\n')
    assert all(word not in request.lower() for word in [b'authorization',b'cookie',b'private',b'proxy'])


@pytest.mark.parametrize('ip',['127.0.0.1','10.1.2.3','169.254.169.254','192.168.1.2','0.0.0.0','::1','fc00::1','fe80::1','::ffff:127.0.0.1'])
def test_nonpublic_and_mixed_dns_never_connect(monkeypatch,ip):
    trace,answers=fake_network(monkeypatch)
    family=socket.AF_INET6 if ':' in ip else socket.AF_INET
    address=(ip,443,0,0) if family==socket.AF_INET6 else (ip,443)
    answers.append((family,socket.SOCK_STREAM,6,'',address))
    with pytest.raises(ValueError,match='nonpublic'): w.fetch_worker(URL,1)
    assert not any(item[0] in {'connect','tls_host'} for item in trace)


@pytest.mark.parametrize('status',[301,302,303,307,308,401,404,500])
def test_redirect_or_non_success_never_followed(monkeypatch,status):
    trace,_=fake_network(monkeypatch,FakeResponse(status=status))
    with pytest.raises(ValueError,match='http_status'): w.fetch_worker(URL,1)
    assert len([item for item in trace if item[0]=='connect']) == 1


@pytest.mark.parametrize('body,headers',[
    (BODY,[('Content-Type','application/json')]),(BODY,[('Content-Type','application/octet-stream')]),
    (BODY,[('Content-Type','text/plain; charset=iso-8859-1')]),
    (BODY,[('Content-Type','text/plain'),('Content-Encoding','gzip')]),
    (BODY,[('Content-Type','text/plain'),('Content-Length','999999')]),
    (BODY,[('Content-Type','text/plain'),('Content-Length',str(len(BODY)+1))]),
    (BODY,[('Content-Type','text/plain'),('Content-Length','1'),('Transfer-Encoding','chunked')]),
    (BODY,[('Content-Type','text/plain'),('Content-Type','text/html')]),
    (BODY,[('Content-Type','text/plain'),('Transfer-Encoding','gzip')]),
    (b'\xff',[('Content-Type','text/plain')]),(b'a\x00b',[('Content-Type','text/plain')]),
    (b'a'*(w.MAX_BODY+1),[('Content-Type','text/plain')]),
    (b'a'*(w.MAX_TEXT+1),[('Content-Type','text/plain')])])
def test_mime_encoding_framing_and_size_fail_closed(monkeypatch,body,headers):
    fake_network(monkeypatch,FakeResponse(body,headers=headers))
    with pytest.raises((ValueError,UnicodeError)): w.fetch_worker(URL,1)


def test_html_receipt_uses_visible_text_and_raw_digest():
    body=b'<head><title>hidden</title></head><p>Hello <b>world</b></p><script>secret()</script>'
    assert w.extract_text(body,'text/html') == 'Hello world'
    with pytest.raises(ValueError): w.extract_text(b'<script>only script</script>','text/html')


def fixture_process(monkeypatch,payload=None,code=None):
    real_popen=subprocess.Popen; seen=[]
    def launch(argv,**kwargs):
        seen.append((argv,kwargs))
        value=payload if payload is not None else {**worker_value(),'url':argv[-2]}
        fixture=code or ('import sys; sys.stdout.write('+repr(json.dumps(value))+')')
        return real_popen([sys.executable,'-I','-c',fixture],**kwargs)
    monkeypatch.setattr(w.subprocess,'Popen',launch)
    return seen


def test_broker_validates_digest_scrubs_env_and_reaps(monkeypatch,tmp_path):
    seen=fixture_process(monkeypatch); monkeypatch.setenv('DISCORD_TOKEN','PRIVATE_TOKEN')
    broker=w.WebBroker(tmp_path/'s',time.monotonic()+2); observe(broker)
    rec=broker.fetch(URL)
    assert rec and rec.url==URL and rec.text==TEXT and rec.sha256==worker_value()['sha256']
    assert not broker._processes
    argv,kwargs=seen[0]
    assert argv[1:3]==['-I','-B'] and argv[4]=='--fetch'
    assert set(kwargs['env'])=={'PATH','LANG','LC_ALL'} and kwargs['start_new_session'] and kwargs['close_fds']
    assert 'PRIVATE' not in json.dumps(kwargs['env']) and 'body_b64' not in rec.wire()
    assert broker.receipts=={rec.receipt:rec}


@pytest.mark.parametrize('change',[{'sha256':'0'*64},{'url':'https://github.com/wrong'},{'text':'invented'},{'body_b64':'invalid*'},{'content_type':'image/png'}])
def test_worker_receipt_corruption_holds(monkeypatch,tmp_path,change):
    fixture_process(monkeypatch,{**worker_value(),**change})
    broker=w.WebBroker(tmp_path/'s',time.monotonic()+2); observe(broker)
    assert broker.fetch(URL) is None and not broker.receipts and not broker._processes


@pytest.mark.parametrize('code,seconds',[
    ('import time; time.sleep(30)',.1),
    ('import sys,time;sys.stdout.write("x"*300000);sys.stdout.flush();time.sleep(30)',2)])
def test_worker_timeout_or_output_limit_kills_reaps(monkeypatch,tmp_path,code,seconds):
    fixture_process(monkeypatch,code=code)
    broker=w.WebBroker(tmp_path/'s',time.monotonic()+seconds); observe(broker)
    start=time.monotonic()
    assert broker.fetch(URL) is None and not broker._processes and not broker.receipts
    assert time.monotonic()-start < 2


@pytest.fixture
def unix_socket_path():
    with tempfile.TemporaryDirectory(prefix='docich-web-test-',dir='/tmp') as directory:
        yield Path(directory)/'s'


@pytest.mark.skipif(sys.platform!='linux',reason='Unix broker listener acceptance requires Linux')
def test_broker_exit_cancels_active_worker_and_joins(monkeypatch,unix_socket_path):
    fixture_process(monkeypatch,code='import time; time.sleep(30)')
    with w.WebBroker(unix_socket_path,time.monotonic()+30) as broker:
        observe(broker)
        def send():
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
                conn.connect(str(unix_socket_path)); conn.sendall(json.dumps({'url':URL}).encode()+b'\n')
                try: conn.recv(1024)
                except OSError: pass
        thread=threading.Thread(target=send); thread.start()
        deadline=time.monotonic()+2
        while not broker._processes and time.monotonic()<deadline: time.sleep(.01)
        assert broker._processes
    thread.join(timeout=2)
    assert not thread.is_alive() and not broker._processes and not broker.receipts and not unix_socket_path.exists()


def test_receipt_requires_search_success_helper_and_exact_quote(tmp_path):
    rec,ref,search,read=web_fixture()
    kwargs={'web_receipts':{rec.receipt:rec}}
    assert r.parse_evidence(transcript([ref],[search,read]),'web',tmp_path,None,**kwargs).ok
    for tools in ([],[search],[read],[search,{**read,'exit_code':1}],
                  [search,{**read,'command':'echo '+json.dumps(rec.wire())}],
                  [search,{**read,'aggregated_output':'{"status":"ok"}'}]):
        with pytest.raises(ValueError,match='unverified'):
            r.parse_evidence(transcript([ref],tools),'web',tmp_path,None,**kwargs)
    for change in ({'sha256':'0'*64},{'quote':'invented'},{'receipt':'missing'},{'ref':'https://github.com/wrong'}):
        with pytest.raises(ValueError,match='unverified'):
            r.parse_evidence(transcript([{**ref,**change}],[search,read]),'web',tmp_path,None,**kwargs)
    with pytest.raises(ValueError,match='unverified'):
        r.parse_evidence(transcript([ref],[search,read]),'web',tmp_path,None)


def test_mixed_requires_web_receipt_and_snapshot_read(tmp_path):
    rec,ref,search,read=web_fixture(); (tmp_path/'a.py').write_text('value = 42\n')
    manifest={'repo':'azumag/docich','revision':'a'*40,'files':{'a.py':'b'*64}}
    code={'kind':'code','ref':'a.py','line':1,'quote':'value = 42'}
    shown={'type':'command_execution','command':'cat source/a.py','status':'completed','exit_code':0,'aggregated_output':'value = 42'}
    assert r.parse_evidence(transcript([ref,code],[search,read,shown]),'web_and_code',tmp_path,manifest,web_receipts={rec.receipt:rec}).ok
    assert not r.parse_evidence(transcript([ref],[search,read]),'web_and_code',tmp_path,manifest,web_receipts={rec.receipt:rec}).ok


def test_tls_validation_failure_prevents_request(monkeypatch):
    trace,_=fake_network(monkeypatch)
    class BadTLS:
        def wrap_socket(self,*a,**k): raise w.ssl.SSLCertVerificationError('synthetic mismatch')
    monkeypatch.setattr(w.ssl,'create_default_context',BadTLS)
    with pytest.raises(w.ssl.SSLCertVerificationError): w.fetch_worker(URL,1)
    assert not any(item[0]=='request' for item in trace)


def test_broker_fetch_budget_and_cache(monkeypatch,tmp_path):
    seen=fixture_process(monkeypatch); broker=w.WebBroker(tmp_path/'s',time.monotonic()+5)
    urls=[URL+f'/{n}' for n in range(5)]; observe(broker,urls)
    assert all(broker.fetch(url) for url in urls[:4])
    assert broker.fetch(urls[0]) is not None and len(seen)==4
    assert broker.fetch(urls[4]) is None and len(seen)==4


@pytest.mark.parametrize('scope',['web','web_and_code'])
def test_research_mounts_fixed_broker_and_validates_receipt(monkeypatch,tmp_path,scope):
    rec,ref,search,read=web_fixture()
    monkeypatch.setattr(r.sys,'platform','linux')
    monkeypatch.setattr(r.shutil,'which',lambda name,**kw:'/usr/bin/'+name)
    class FakeProxy:
        def __init__(self,path): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
    class FakeBroker(FakeProxy):
        def __init__(self,path,deadline): assert str(path).endswith('web.sock') and deadline>time.monotonic()
        receipts={rec.receipt:rec}
        def observe(self,event): pass
    monkeypatch.setattr(r,'EgressProxy',FakeProxy); monkeypatch.setattr(r,'WebBroker',FakeBroker)
    root=tmp_path/'approved'; root.mkdir(); (root/'a.py').write_text('value = 42\n')
    (root/'manifest.json').write_text(json.dumps({'repo':'azumag/docich','revision':'a'*40,'files':{'a.py':hashlib.sha256(b'value = 42\n').hexdigest()}}))
    def run(argv,prompt,child_env,timeout,observer=None):
        assert observer is not None; observer({'type':'item.completed','item':search})
        assert set(child_env)=={'PATH','LANG','CODEX_API_KEY'}
        assert 'web_search="live"' in argv and '--share-net' not in argv and w.HELPER in argv and w.SOCKET in argv
        setting=next(arg for arg in argv if arg.startswith('tools.web_search.allowed_domains='))
        assert set(json.loads(setting.split('=',1)[1]))==w.HOSTS
        assert 'credential' in Path(argv[argv.index(w.HELPER)-1]).read_text()
        refs,tools=[ref],[search,read]
        if scope=='web_and_code':
            refs += [{'kind':'code','ref':'a.py','line':1,'quote':'value = 42'}]
            tools += [{'type':'command_execution','command':'cat source/a.py','status':'completed','exit_code':0,'aggregated_output':'value = 42'}]
        return transcript(refs,tools)
    monkeypatch.setattr(r,'_run',run)
    env={'DOCICH_REPLY_RESEARCH_ENABLED':'1','DOCICH_ALLOW_REAL_AI':'1','DOCICH_REPLY_WEB_SEARCH_ENABLED':'1',
         'DOCICH_REPLY_CODEX_MODEL':'synthetic-model','DOCICH_REPLY_CODEX_API_KEY':'SYNTHETIC_KEY',
         'DOCICH_REPLY_SOURCE_APPROVED':'1','DOCICH_REPLY_SOURCE_DIR':str(root),'DISCORD_TOKEN':'PRIVATE_TOKEN'}
    result=r.research([{'role':'user','text':'公開仕様を確認して'}],scope,env=env)
    assert result.ok and URL in result.sources


@pytest.mark.parametrize('wire_request',[{'url':'https://evil.test/'},{'url':URL,'headers':{'Authorization':'secret'}},{'url':URL,'method':'POST'},{'command':'id'}])
@pytest.mark.skipif(sys.platform!='linux',reason='Unix broker listener acceptance requires Linux')
def test_wire_cannot_add_hosts_headers_methods_or_commands(monkeypatch,unix_socket_path,wire_request):
    seen=fixture_process(monkeypatch)
    with w.WebBroker(unix_socket_path,time.monotonic()+2) as broker:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.connect(str(unix_socket_path)); conn.sendall(json.dumps(wire_request).encode()+b'\n')
            conn.settimeout(2); conn.recv(1024)
        assert seen==[] and not broker.receipts


def test_only_native_completed_search_metadata_authorizes_candidate(monkeypatch,tmp_path):
    seen=fixture_process(monkeypatch); broker=w.WebBroker(tmp_path/'s',time.monotonic()+2); search=web_fixture()[2]
    for event in (None,[],{'type':'item.started','item':search},
                  {'type':'item.completed','item':{**search,'action':{'type':'open_page','url':URL}}},
                  {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(search)}},
                  {'type':'item.completed','item':{'type':'command_execution','aggregated_output':json.dumps(search)}}):
        broker.observe(event)
        assert broker.fetch(URL) is None and not seen
    observe(broker); assert broker.fetch(URL) is not None and len(seen)==1


def test_run_observes_complete_jsonl_before_completion_without_api():
    events=[]; first=json.dumps({'type':'item.completed','item':web_fixture()[2]})+'\n'; last=json.dumps({'type':'turn.completed'})
    code=('import sys,time;sys.stdout.write('+repr(first[:20])+');sys.stdout.flush();time.sleep(.02);'
          'sys.stdout.write('+repr(first[20:]+last)+');sys.stdout.flush()')
    raw=r._run([sys.executable,'-I','-c',code],b'',{},2,observer=events.append)
    assert raw==(first+last).encode() and len(events)==2 and events[0]['item']['type']=='web_search'


@pytest.mark.skipif(sys.platform!='linux',reason='Unix broker listener acceptance requires Linux')
def test_real_wire_client_receives_only_broker_receipt(monkeypatch,unix_socket_path):
    fixture_process(monkeypatch); monkeypatch.setattr(w,'SOCKET',str(unix_socket_path))
    with w.WebBroker(unix_socket_path,time.monotonic()+3) as broker:
        observe(broker); value=w.client(URL)
        assert value['status']=='ok' and value['url']==URL and value['text']==TEXT
        assert value==broker.receipts[value['receipt']].wire()
    assert not broker._processes and not unix_socket_path.exists()
