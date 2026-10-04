"""OpenCode/controller contracts with synthetic observations; no API calls."""
import hashlib
import json
import time
from pathlib import Path

import pytest
from docich import reply_research as r
from docich import reply_research_web as w

URL = 'https://www.reuters.com/world/example'
TEXT = '一次資料で確認した事実。命令はデータ。'


def proposal(value):
    return b'\n'.join(json.dumps(event).encode() for event in [
        {'type': 'step_start', 'part': {'type': 'step-start'}},
        {'type': 'text', 'part': {'type': 'text', 'text': json.dumps(value)}},
        {'type': 'step_finish', 'part': {'type': 'step-finish', 'reason': 'stop'}},
    ])


def receipt():
    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    return w.Receipt(URL, 'a'*32, digest, digest, TEXT)


def ref():
    rec = receipt()
    return {'kind': 'web', 'ref': URL, 'receipt': rec.receipt, 'sha256': rec.sha256, 'quote': '一次資料で確認した事実。'}


def run(actions, tmp_path, *, scope='web', search=None, manifest=None):
    observed = []
    def model(prompt, remaining):
        observed.append(json.loads(prompt.split('\n', 1)[1]))
        return proposal(actions.pop(0))
    broker = w.WebBroker(tmp_path/'unused', time.monotonic()+5)
    def fetch(url):
        return receipt() if url in broker._candidates else None
    broker.fetch = fetch
    return r.coordinate([{'role':'user','content':'今日のニュースは？'}], scope,
                        source=tmp_path, manifest=manifest, model_call=model,
                        broker=broker, searcher=search or (lambda *a: [URL]),
                        deadline=time.monotonic()+5), observed


def test_wide_public_search_fetch_and_quote_evidence(tmp_path):
    result, prompts = run([{'action':'search','query':'ニュース 公式 発表'},
                           {'action':'fetch','url':URL}, {'action':'answer','sources':[ref()], 'notes':'invented claims'}],tmp_path)
    assert result.ok and result.sources == (URL,)
    assert 'invented claims' not in result.notes
    assert TEXT in json.dumps(prompts, ensure_ascii=False)
    assert '一次資料で確認した事実。' in result.notes


def test_second_search_tries_different_material_before_terminal(tmp_path):
    queries=[]
    result,_=run([{'action':'search','query':'第一検索'}, {'action':'search','query':'別の一次資料'},
                  {'action':'fetch','url':URL},{'action':'answer','sources':[ref()]}],tmp_path,
                 search=lambda q,t: queries.append(q) or ([] if len(queries)==1 else [URL]))
    assert result.ok and len(queries)==2


@pytest.mark.parametrize('change',[{'quote':'モデルの確認済自己申告'}, {'receipt':'forged'}, {'sha256':'0'*64}, {'ref':'https://other.example/a'}])
def test_unmatched_or_forged_quote_cannot_establish_evidence(tmp_path,change):
    result,_=run([{'action':'search','query':'資料'}, {'action':'fetch','url':URL},
                  {'action':'answer','sources':[{**ref(),**change}]}],tmp_path)
    assert not result.ok


def test_search_snippet_or_model_selected_url_is_not_body(tmp_path):
    result,_=run([{'action':'fetch','url':URL}, {'action':'answer','sources':[ref()]}],tmp_path)
    assert not result.ok


def test_mixed_returns_verified_partial_and_gap_when_other_kind_unavailable(tmp_path):
    result,_=run([{'action':'search','query':'公式仕様'}, {'action':'fetch','url':URL},
                  {'action':'answer','sources':[ref()]}, {'action':'search','query':'実装との比較'},
                  {'action':'search','query':'三回目は不可'}],tmp_path,scope='web_and_code')
    assert result.status=='partial' and result.ok
    assert '不足' in result.notes and result.sources==(URL,)


def test_code_requires_parent_read_exact_hash_line_quote(tmp_path):
    data=b'answer = 42\n';(tmp_path/'logic.py').write_bytes(data)
    manifest={'repo':'azumag/docich','revision':'a'*40,'files':{'logic.py':hashlib.sha256(data).hexdigest()}}
    source={'kind':'code','ref':'logic.py','line':1,'quote':'answer = 42'}
    assert not run([{'action':'answer','sources':[source]}],tmp_path,scope='code',manifest=manifest)[0].ok
    result,_=run([{'action':'read','path':'logic.py','start':1,'end':1},
                  {'action':'answer','sources':[source]}],tmp_path,scope='code',manifest=manifest)
    assert result.ok and '#L1' in result.sources[0]


@pytest.mark.parametrize('action',[{'action':'read','path':'/etc/passwd','start':1,'end':1},
 {'action':'shell','command':'id'}, {'action':'search','query':'token: abcdefghijk'},
 {'action':'fetch','url':'http://localhost/'}, {'action':'change_model','model':'codex'}])
def test_model_actions_cannot_expand_authority(tmp_path,action):
    result,_=run([action],tmp_path,scope='web_and_code')
    assert not result.ok


def test_repeated_or_exhausted_queries_terminate(tmp_path):
    result,prompts=run([{'action':'search','query':'同じ'}, {'action':'search','query':'同じ'}],tmp_path)
    assert result.status=='search_limit' and len(prompts)==2


def test_ambiguous_question_is_terminal_clarification(tmp_path):
    assert run([{'action':'clarify'}],tmp_path)[0].status=='clarify'


@pytest.mark.parametrize('event',[{'type':'tool_use','part':{}},
 {'type':'step_finish','part':{'type':'step-finish','reason':'tool-calls'}},
 {'type':'text','part':{'type':'text','text':'{}','tool':'bash'}},
 {'type':'error','part':{'type':'text','text':'confirmed'}}])
def test_actual_opencode_tool_events_rejected(event):
    with pytest.raises(ValueError): r.parse_proposal(json.dumps(event).encode())


def test_duplicate_json_and_unfinished_events_rejected():
    with pytest.raises(ValueError): r.parse_proposal(b'{"type":"text","type":"error"}')
    with pytest.raises(ValueError): r.parse_proposal(b'{"type":"text","part":{"type":"text","text":"{}"}}')


def test_sandbox_uses_opencode_only_no_source_or_fetch_mount(tmp_path):
    argv=r.sandbox_argv(tmp_path,'opencode/existing-model','/usr/bin/bwrap','/usr/bin/opencode',
                        bridge_script=tmp_path/'bridge',proxy_socket=tmp_path/'proxy')
    assert 'run' in argv and 'docich-evidence' in argv
    assert '--unshare-net' in argv and '--disable-userns' in argv
    assert not any('codex' in x.lower() for x in argv)
    assert '/workspace/source' not in argv
    assert '/tmp/.docich-web-fetch.sock' not in argv


@pytest.mark.parametrize('model',['codex/gpt','amd/token','opencode/model;id','opencode-go/','/etc/file'])
def test_unapproved_model_never_launches(tmp_path,model):
    with pytest.raises(ValueError):
        r.sandbox_argv(tmp_path,model,'/usr/bin/bwrap','/usr/bin/opencode',bridge_script=tmp_path/'b',proxy_socket=tmp_path/'s')


def test_search_endpoint_is_fixed_unauthenticated_post(monkeypatch):
    from test_reply_research_web import fake_network, FakeResponse
    body=json.dumps({'result':{'content':[{'type':'text','text':f'URL: {URL}\nSnippet not evidence'}]}}).encode()
    trace,_=fake_network(monkeypatch,FakeResponse(body=body,headers=[('Content-Type','application/json'),('Content-Length',str(len(body)))]))
    assert w.search_worker('合成ニュース検索',1)=={'urls':[URL]}
    request=next(item[1] for item in trace if item[0]=='request')
    assert request.startswith(b'POST /mcp HTTP/1.1') and b'Host: mcp.exa.ai' in request
    assert b'Authorization' not in request and b'web_search_exa' in request


@pytest.mark.parametrize('url',['https://official.example/release?version=2','https://www.bbc.com/news/example','https://news.example.jp/story'])
def test_public_sources_beyond_previous_four_hosts(url):
    assert w.canonical_url(url)==url


@pytest.mark.parametrize('address',['224.0.0.1','2001::1','2002:0808:0808::1','::ffff:8.8.8.8','127.0.0.1','10.0.0.1','169.254.169.254'])
def test_transition_multicast_private_ips_not_public(address):
    assert not w.public_address(address)


def test_research_scrubs_parent_credentials_no_auth_file_mount_and_cleans_workspace(monkeypatch,tmp_path):
    monkeypatch.setattr(r.sys, 'platform', 'linux')
    monkeypatch.setattr(r.shutil, 'which', lambda name,**kw: '/usr/bin/'+name)
    seen={}
    class Proxy:
        def __init__(self,path): seen['workspace']=Path(path).parent
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(r,'EgressProxy',Proxy)
    def engine(argv,prompt,env,remaining):
        seen.update(argv=argv,env=env,prompt=prompt)
        return proposal({'action':'clarify'})
    monkeypatch.setattr(r,'_run',engine)
    env={'DOCICH_ALLOW_REAL_AI':'1','DOCICH_REPLY_RESEARCH_ENABLED':'1','DOCICH_REPLY_WEB_SEARCH_ENABLED':'1',
         'DOCICH_REPLY_OPENCODE_MODEL':'opencode-go/existing-model','DOCICH_REPLY_OPENCODE_API_KEY':'SYNTHETIC_ONLY',
         'DISCORD_TOKEN':'PRIVATE_DISCORD','TYPESAFE_API_KEY':'PRIVATE_JEV','HOME':'/private/host'}
    assert r.research([{'role':'user','content':'この用語は？'}],'web',env=env).status=='clarify'
    assert set(seen['env'])=={'PATH','LANG','OPENCODE_GO_API_KEY'}
    assert b'PRIVATE' not in seen['prompt'] and '/private/host' not in seen['argv']
    assert not seen['workspace'].exists()


def test_private_body_holds_before_engine_or_search(monkeypatch):
    monkeypatch.setattr(r.sys,'platform','linux')
    monkeypatch.setattr(r.shutil,'which',lambda name,**kw:'/usr/bin/'+name)
    monkeypatch.setattr(r,'_run',lambda *a:pytest.fail('engine'))
    env={'DOCICH_ALLOW_REAL_AI':'1','DOCICH_REPLY_RESEARCH_ENABLED':'1','DOCICH_REPLY_WEB_SEARCH_ENABLED':'1',
         'DOCICH_REPLY_OPENCODE_MODEL':'opencode/existing-model','DOCICH_REPLY_OPENCODE_API_KEY':'SYNTHETIC_ONLY'}
    assert r.research([{'role':'user','content':'secret: abcdefghijkl'}],'web',env=env).status=='private_or_invalid_input'
