import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import actions  # noqa: E402


class TestExtractJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(actions.extract_json('{"type": "wait", "ms": 5}'), '{"type": "wait", "ms": 5}')

    def test_strips_markdown_fence_object(self):
        text = '```json\n{"type": "wait", "ms": 5}\n```'
        self.assertEqual(actions.extract_json(text), '{"type": "wait", "ms": 5}')

    def test_strips_markdown_fence_array(self):
        text = 'ここに行動を書きます:\n```json\n[{"type": "wait", "ms": 5}]\n```\n以上です'
        self.assertEqual(actions.extract_json(text), '[{"type": "wait", "ms": 5}]')

    def test_no_json_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.extract_json("no json here at all")

    def test_unclosed_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.extract_json('{"type": "wait"')


class TestParseActionsEnvelopes(unittest.TestCase):
    def test_single_dict(self):
        result = actions.parse_actions({"type": "wait", "ms": 10})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "wait")
        self.assertEqual(result[0].ms, 10)

    def test_bare_list(self):
        result = actions.parse_actions([{"type": "wait", "ms": 1}, {"type": "wait", "ms": 2}])
        self.assertEqual([a.ms for a in result], [1, 2])

    def test_actions_wrapper_dict(self):
        result = actions.parse_actions({"actions": [{"type": "wait", "ms": 3}]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ms, 3)

    def test_json_string_with_actions_wrapper(self):
        result = actions.parse_actions('{"actions": [{"type": "wait", "ms": 3}]}')
        self.assertEqual(len(result), 1)

    def test_json_string_with_fence(self):
        text = '```json\n{"actions": [{"type": "wait", "ms": 7}]}\n```'
        result = actions.parse_actions(text)
        self.assertEqual(result[0].ms, 7)

    def test_invalid_json_string_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions("{not json}")

    def test_non_dict_non_list_top_level_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions("42")


class TestParseActionTypes(unittest.TestCase):
    def test_pad_action(self):
        a = actions.parse_actions({"type": "pad", "buttons": ["a", "start"]})[0]
        self.assertEqual(a.type, "pad")
        self.assertEqual(a.buttons, ["a", "start"])
        self.assertEqual(a.hold_ms, actions.DEFAULT_HOLD_MS)

    def test_pad_action_custom_hold_ms(self):
        a = actions.parse_actions({"type": "pad", "buttons": ["up"], "hold_ms": 250})[0]
        self.assertEqual(a.hold_ms, 250)

    def test_pad_action_invalid_button(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "pad", "buttons": ["a", "turbo"]})

    def test_pad_action_missing_buttons(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "pad"})

    def test_pad_action_empty_buttons(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "pad", "buttons": []})

    def test_key_action(self):
        a = actions.parse_actions({"type": "key", "keys": ["Up", "x"]})[0]
        self.assertEqual(a.keys, ["Up", "x"])
        self.assertEqual(a.hold_ms, actions.DEFAULT_HOLD_MS)

    def test_key_action_missing_keys(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "key"})

    def test_text_action(self):
        a = actions.parse_actions({"type": "text", "text": "hjkl"})[0]
        self.assertEqual(a.text, "hjkl")

    def test_text_action_missing_text(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "text"})

    def test_text_action_empty_text(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "text", "text": ""})

    def test_special_action(self):
        a = actions.parse_actions({"type": "special", "key": "Escape"})[0]
        self.assertEqual(a.key, "Escape")

    def test_special_action_missing_key(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "special"})

    def test_mouse_action(self):
        a = actions.parse_actions({"type": "mouse", "x": 10, "y": 20, "button": 2})[0]
        self.assertEqual((a.x, a.y, a.button), (10, 20, 2))

    def test_mouse_action_default_button(self):
        a = actions.parse_actions({"type": "mouse", "x": 1, "y": 2})[0]
        self.assertEqual(a.button, 1)

    def test_mouse_action_missing_coords(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "mouse", "x": 1})

    def test_wait_action(self):
        a = actions.parse_actions({"type": "wait", "ms": 500})[0]
        self.assertEqual(a.ms, 500)

    def test_wait_action_missing_ms_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "wait"})

    def test_unknown_type_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"type": "teleport"})

    def test_missing_type_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions({"buttons": ["a"]})

    def test_non_object_item_raises(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_actions(["not-an-object"])


if __name__ == "__main__":
    unittest.main()
