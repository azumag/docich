import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'ops/hotfixes/apply_analysis_contract_20260907.py'

def load():
    if not SCRIPT.is_file():raise AssertionError('analysis installer is missing')
    sp=importlib.util.spec_from_file_location('analysis_deploy',SCRIPT)
    m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

class AnalysisDeployTests(unittest.TestCase):
    def test_exact_source_targets_and_preserved_prompt_mode(self):
        m=load();d=m.manifest()
        self.assertEqual(d['source_sha'],'8ba454fce60ca23637a54471b5391b6d732355a8')
        self.assertEqual(list(d['files']),['strategy/analysis_contract.py','prompts/analyze_strategy.md','eloop_improve.sh','strategy/sandbox.sh'])
        self.assertEqual(d['files']['strategy/sandbox.sh']['mode'], 0o755)
        self.assertEqual(d['files']['prompts/analyze_strategy.md']['mode'],0o664)
        self.assertEqual(d['files']['prompts/analyze_strategy.md']['new'],'89cafb063910c0c2ee41d0cbfac83ec5f857cbcc6389a3314ec13487251d7751')
        self.assertIsNone(d['files']['strategy/analysis_contract.py']['old'])
        self.assertEqual(d['files']['eloop_improve.sh']['old'],'727129563b29f45e9a152628e35838ca05588f80edf81cbb8f5f18ed091499d6')

    def test_not_an_unrestricted_apply_interface(self):
        m=load();d=m.manifest();d['files']['strategy.py']={'old':None,'new':'0'*64,'mode':420}
        with self.assertRaises(ValueError):m.validate_payload(d,{p:b'a' for p in d['files']})

    def test_pause_and_idle_are_required(self):
        m=load()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve();(p/'tmp/state').mkdir(parents=True)
            (p/'tmp/state/improve_state.json').write_text('{"status":"idle","pid":0}')
            with self.assertRaises(ValueError):m.preflight(p)
            (p/'tmp/state/improve_daemon.paused').write_text(m.PAUSE_OWNER)
            m.preflight(p)
            (p/'tmp/state/improve_state.json').write_text('{"status":"running","pid":123}')
            with self.assertRaises(ValueError):m.preflight(p)

    def test_payload_hash_mismatch_prevents_execution(self):
        m=load()
        with self.assertRaises(ValueError):m.validate_payload(m.manifest(),{p:b'bad' for p in m.PATHS})

if __name__=='__main__':unittest.main()
