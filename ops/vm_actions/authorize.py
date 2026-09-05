#!/usr/bin/env python3
import json, os, re, sys

OWNER='azumag'
OWNER_ID='9018513'
REPOS={
    'azumag/soviet_now':'1155505884',
    'azumag/docich':'1327276249',
}
WORKFLOW='.github/workflows/vm-operations.yml'
OPS={'deploy','exec','status','bootstrap'}
TARGETS={'preview','production'}
REF_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}\Z')
SHA_RE=re.compile(r'[0-9a-f]{40}\Z')

def fail(msg):
    print(msg, file=sys.stderr); raise SystemExit(1)

def main():
    env=os.environ
    repo=env.get('GITHUB_REPOSITORY','')
    expected_id=REPOS.get(repo)
    checks=[
        expected_id is not None,
        env.get('GITHUB_REPOSITORY_ID')==expected_id,
        env.get('GITHUB_REPOSITORY_OWNER')==OWNER,
        env.get('GITHUB_REPOSITORY_OWNER_ID')==OWNER_ID,
        env.get('GITHUB_ACTOR')==OWNER,
        env.get('GITHUB_ACTOR_ID')==OWNER_ID,
        env.get('GITHUB_TRIGGERING_ACTOR')==OWNER,
        env.get('GITHUB_REF')=='refs/heads/main',
        env.get('GITHUB_WORKFLOW_REF')==f'{repo}/{WORKFLOW}@refs/heads/main',
    ]
    if not all(checks): fail('VM Actions authorization denied')
    event=env.get('GITHUB_EVENT_NAME')
    sha=env.get('GITHUB_SHA','')
    if not SHA_RE.fullmatch(sha): fail('invalid workflow SHA')
    if event=='push':
        result={'operation':'deploy','target':'production','ref':sha,'confirm':'production'}
    elif event=='workflow_dispatch':
        op=env.get('INPUT_OPERATION','')
        target=env.get('INPUT_TARGET','')
        ref=env.get('INPUT_REF') or 'main'
        confirm=env.get('INPUT_CONFIRM','')
        if op not in OPS or target not in TARGETS: fail('unsupported operation or target')
        if not REF_RE.fullmatch(ref) or '..' in ref or '//' in ref or ref.startswith('refs/') or ref.endswith(('/', '.lock')):
            fail('invalid ref')
        if op=='bootstrap' and target!='production': fail('bootstrap is production-only')
        if target=='production' and op in {'exec','bootstrap'} and confirm!='production':
            fail('production confirmation required')
        if op=='deploy' and target=='production' and ref!='main': fail('production deploy must use main')
        result={'operation':op,'target':target,'ref':ref,'confirm':confirm}
    else:
        fail('unsupported event')
    if env.get('GITHUB_OUTPUT'):
        with open(env['GITHUB_OUTPUT'],'a',encoding='utf-8') as out:
            for key in ('operation','target','ref'):
                out.write(f'{key}={result[key]}\n')
    print(json.dumps(result, separators=(',',':')))

if __name__=='__main__': main()
