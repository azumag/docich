"""Synthetic DNS/TLS/HTTP and local fixture children; no external/API calls."""
import base64
from email.message import Message
import hashlib
import json
import os
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


def observe(broker, urls=None):
    broker.authorize(urls or [URL])


@pytest.mark.parametrize('host',['ja.wikipedia.org','en.wikipedia.org','github.com','raw.githubusercontent.com','www.bbc.com','www.reuters.com','www.google.com'])
def test_exact_public_hosts_and_unicode_path(host):
    assert w.canonical_url(f'https://{host}:443/wiki/名前') == f'https://{host}/wiki/%E5%90%8D%E5%89%8D'


@pytest.mark.parametrize('url',[
    'http://github.com/a',
    'https://github.com./a','https://user:pass@github.com/a','https://github.com:444/a',
    'https://127.0.0.1/a','https://[::1]/a','https://169.254.169.254/a',
    'https://github.com/a?token=x','https://github.com/a#x',
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
    monkeypatch.setenv('HTTPS_PROXY','private-proxy'); monkeypatch.setenv('OPENCODE_API_KEY','PRIVATE_KEY')
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


def descendant_exited(pid, real_popen, timeout=1):
    """Wait for absent/Z only; an X transition or any live state is not success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if sys.platform == 'linux':
            try:
                state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                return True
        else:
            state = real_popen(['ps', '-o', 'stat=', '-p', str(pid)], stdout=subprocess.PIPE,
                               text=True).communicate(timeout=2)[0].strip()
        if not state or state.startswith('Z'):
            return True
        time.sleep(.01)
    return False


@pytest.mark.parametrize('state', ['R', 'S', 'D', 'T', 'I', 'X'])
def test_descendant_probe_never_accepts_live_or_x_state(monkeypatch, state):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(os, 'kill', lambda *_: None)
    monkeypatch.setattr(Path, 'read_text', lambda *_: f'1 (fixture) {state} 0')
    clock = iter([0, 0, 2])
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(time, 'sleep', lambda *_: None)
    assert not descendant_exited(1, None)


@pytest.mark.parametrize('terminal', ['absent', 'Z'])
def test_descendant_probe_waits_through_x_until_absent_or_z(monkeypatch, terminal):
    monkeypatch.setattr(sys, 'platform', 'linux')
    calls = []
    def exists(*_):
        calls.append(True)
        if terminal == 'absent' and len(calls) == 2:
            raise ProcessLookupError
    monkeypatch.setattr(os, 'kill', exists)
    states = iter(['X', terminal])
    monkeypatch.setattr(Path, 'read_text', lambda *_: f'1 (fixture) {next(states)} 0')
    monkeypatch.setattr(time, 'sleep', lambda *_: None)
    assert descendant_exited(1, None)
    assert len(calls) == 2  # X alone was not accepted.


@pytest.mark.parametrize('_repeat', range(5))
def test_exited_worker_leader_does_not_leave_stdout_descendant(monkeypatch,tmp_path,_repeat):
    # A finite fixture also bounds a regression with the old poll()-guarded
    # kill: it would return only when this child closes stdout 3 seconds later.
    child = 'import time;time.sleep(3)'
    pid_path = tmp_path/'child.pid'
    code = ('import pathlib,subprocess,sys;'
            f'p=subprocess.Popen([sys.executable,"-I","-c",{child!r}]);'
            f'pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))')
    real_popen = subprocess.Popen
    workers = []
    def launch(argv,**kwargs):
        proc = real_popen([sys.executable,'-I','-c',code],**kwargs)
        workers.append(proc)
        return proc
    monkeypatch.setattr(w.subprocess,'Popen',launch)
    broker=w.WebBroker(tmp_path/'s',time.monotonic()+.5); observe(broker)
    started=time.monotonic()
    assert broker.fetch(URL) is None
    assert time.monotonic()-started < 1.5
    assert workers[0].returncode == 0  # Leader exited normally before cleanup.
    assert not broker._processes and not broker.receipts
    assert descendant_exited(int(pid_path.read_text()), real_popen), 'stdout descendant survived cleanup'


@pytest.fixture
def unix_socket_path():
    with tempfile.TemporaryDirectory(prefix='docich-web-test-',dir='/tmp') as directory:
        yield Path(directory)/'s'








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
