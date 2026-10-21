from pathlib import Path
import json
import tempfile
import unittest
from docich.stream_title_context import progress_phrase

class TitleContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def phrase(self, game):
        return progress_phrase(game, self.root, self.root)

    def test_soren_record_is_not_current_score(self):
        (self.root / 'best_score.txt').write_text('12345\n')
        self.assertIn('最高12,345点', self.phrase('sorengame'))
        self.assertIsNone(self.phrase('nethack'))

    def test_invalid_scores_never_enter_public_title(self):
        for raw in ('-1', 'nan', 'true', '0', '１２３', '123\nsecret', '9'*33):
            with self.subTest(raw=raw):
                (self.root / 'best_score.txt').write_text(raw)
                self.assertIsNone(self.phrase('sorengame'))

    def test_hanjuku_clear_and_next_goal_are_distinct(self):
        (self.root / 'hanjuku_predictions.json').write_text(json.dumps({'schema':1,'best_cleared':1,'secret':'not-public'}))
        self.assertEqual(self.phrase('hanjuku-hero'),'第1話突破の記録から、第2話クリアへ再挑戦')

    def test_invalid_counts_and_schema(self):
        for data in ({'schema':True,'best_cleared':2},{'schema':1,'best_cleared':True},{'schema':1,'best_cleared':13},{'schema':1,'best_cleared':'2'}):
            (self.root / 'hanjuku_predictions.json').write_text(json.dumps(data))
            self.assertIsNone(self.phrase('hanjuku-hero'))

    def test_no_impossible_chapter_13(self):
        (self.root / 'hanjuku_predictions.json').write_text('{"schema":1,"best_cleared":12}')
        self.assertNotIn('13',self.phrase('hanjuku-hero'))

    def test_missing_and_symlink_are_not_evidence(self):
        self.assertIsNone(self.phrase('sorengame'))
        target=self.root/'other';target.write_text('999')
        (self.root/'best_score.txt').symlink_to(target)
        self.assertIsNone(self.phrase('sorengame'))

class IntegratedTitleTests(unittest.TestCase):
    def test_arguments_use_score_but_do_not_read_ops_brief(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from docich.stream_category import viewer_title_args
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'best_score.txt').write_text('5678')
            g=SimpleNamespace(state_dir=root)
            with patch('docich.trading.soren_output.resolve_soren_root',return_value=root):
                args=viewer_title_args('sorengame',g)
            self.assertEqual(args[:2],['--activity','ソ連ゲーム'])
            self.assertIn('最高5,678点',args[3])
            self.assertLess(len(' '.join(args)),100)

    def test_invalid_state_leaves_neutral_fallback(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from docich.stream_category import viewer_title_args
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);g=SimpleNamespace(state_dir=root)
            with patch('docich.trading.soren_output.resolve_soren_root',return_value=root):
                self.assertEqual(viewer_title_args('hanjuku-hero',g),['--activity','半熟英雄','--strategy','AIが城を攻略！ 将軍とエッグモンスターで物語の先へ'])

    def test_verified_generation_varies_same_record_without_cross_game_state(self):
        from docich.stream_title_context import progress_phrase
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'best_score.txt').write_text('1000')
            titles=[]
            for generation in (3,4,5):
                (root/'game_switch.json').write_text(json.dumps({'phase':'ready','active':{'game':'sorengame','generation':generation}}))
                titles.append(progress_phrase('sorengame',root,root))
            self.assertEqual(len(set(titles)),3)
            (root/'game_switch.json').write_text('{"phase":"ready","active":{"game":"nethack","generation":4}}')
            self.assertEqual(progress_phrase('sorengame',root,root),titles[0])

class LiveProgressTests(TitleContextTests):
    def live(self, **changes):
        import time
        identity = dict(game='hanjuku-hero', runtime_id='g9-abcdef', generation=9, lease_id='lease')
        (self.root/'game_switch.json').write_text(json.dumps(dict(phase='ready', active=identity)))
        runtime = self.root/'runtimes'/'g9-abcdef'
        runtime.mkdir(parents=True, exist_ok=True)
        run = dict(identity, playing=True, observed_at=time.time())
        run.update(changes)
        (runtime/'hanjuku_run.json').write_text(json.dumps(run))
        (runtime/'hanjuku_progress.json').write_text(json.dumps(dict(identity, schema=1, cleared=2, ambiguous=False, frame_sha256='a'*64)))
        return runtime

    def test_current_clear_and_goal(self):
        self.live()
        phrase = self.phrase('hanjuku-hero')
        self.assertIn('この冒険で第2話まで突破', phrase)
        self.assertIn('第3話', phrase)
        self.assertNotIn('第3話突破', phrase)

    def test_untrusted_or_old_run_does_not_claim_current_progress(self):
        import time
        for changes in ({'observed_at':time.time()-120}, {'observed_at':float('nan')},
                        {'observed_at':time.time()+120}, {'playing':False},
                        {'lease_id':'old'}, {'generation':True}, {'terminal_candidate':{'reason':'end'}}):
            with self.subTest(changes=changes):
                self.live(**changes)
                self.assertIsNone(self.phrase('hanjuku-hero'))

    def test_ambiguous_progress_falls_back_to_past_record(self):
        runtime = self.live()
        p=runtime/'hanjuku_progress.json'
        data=json.loads(p.read_text());data['ambiguous']=True;p.write_text(json.dumps(data))
        (self.root/'hanjuku_predictions.json').write_text('{"schema":1,"best_cleared":1}')
        self.assertIn('記録',self.phrase('hanjuku-hero'))
        self.assertNotIn('この冒険',self.phrase('hanjuku-hero'))

    def test_switch_and_redirected_runtime_reject_live_facts(self):
        runtime=self.live()
        moved=runtime.with_name('other');runtime.rename(moved);runtime.symlink_to(moved)
        self.assertIsNone(self.phrase('hanjuku-hero'))
