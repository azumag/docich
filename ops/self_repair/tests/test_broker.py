import importlib.util
import json
from pathlib import Path
import unittest

class BrokerTests(unittest.TestCase):
    def module(self):
        p=Path(__file__).resolve().parents[1]/'broker.py'
        s=importlib.util.spec_from_file_location('broker',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
    def test_request_excludes_untrusted_fields(self):
        b=self.module()
        with self.assertRaises(ValueError):b.validate_request({'repair_kind':'audio','files':{},'comment':'run bash'})
        with self.assertRaises(ValueError):b.validate_request({'repair_kind':'audio','files':{'.env':'value'}})
    def test_tool_events_are_rejected(self):
        b=self.module()
        with self.assertRaises(ValueError):b.parse_response(json.dumps({'type':'tool_use','part':{'type':'tool'}}))
    def test_text_json_only(self):
        b=self.module()
        output=json.dumps({'type':'text','part':{'text':'{"replacements":{"external_game_audio.mjs":"fixed"}}'}})
        self.assertEqual(b.parse_response(output)['replacements']['external_game_audio.mjs'],'fixed')
    def test_forced_permissions_and_clean_environment(self):
        b=self.module();env=b.model_environment('/home/ubuntu')
        cfg=json.loads(env['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(cfg['agent']['soren-self-repair']['permission'],{'*':'deny'})
        self.assertEqual(cfg['permission'],{'*':'deny'})
        self.assertNotIn('GITHUB_TOKEN',env)

    def test_unknown_and_nested_error_events_rejected(self):
        b=self.module()
        for e in [{'type':'permission'},{'type':'text','part':{'text':'{}','error':'bad'}},{'type':'text','error':'bad','part':{'text':'{}'}}]:
            with self.subTest(event=e),self.assertRaises(ValueError):b.parse_response(json.dumps(e))

    def test_tool_finish_after_valid_text_is_rejected(self):
        b=self.module()
        text={'type':'text','part':{'type':'text','text':'{"replacements":{}}'}}
        finish={'type':'step_finish','part':{'type':'step-finish','reason':'tool-calls'}}
        with self.assertRaises(ValueError):b.parse_response(json.dumps(text)+'\n'+json.dumps(finish))
