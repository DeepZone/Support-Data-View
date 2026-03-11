import unittest

import app


class HelperFunctionTests(unittest.TestCase):
    def test_binary_state_is_ja_nein(self):
        self.assertEqual(app._format_binary_state(1), "Ja")
        self.assertEqual(app._format_binary_state(0), "Nein")
        self.assertEqual(app._format_binary_state(None), "k.A.")

    def test_toggle_state_is_aktiviert_deaktiviert(self):
        self.assertEqual(app._format_toggle_state("yes"), "Aktiviert")
        self.assertEqual(app._format_toggle_state("off"), "Deaktiviert")
        self.assertEqual(app._format_toggle_state(None), "k.A.")

    def test_parse_optional_float_handles_empty(self):
        self.assertIsNone(app.parse_optional_float("-"))
        self.assertEqual(app.parse_optional_float(" 3.5 "), 3.5)

    def test_parse_channel_float_accepts_decimal_comma(self):
        self.assertEqual(app.parse_channel_float("3,25"), 3.25)
        self.assertEqual(app.parse_channel_float("4.5"), 4.5)
        self.assertIsNone(app.parse_channel_float(""))


if __name__ == "__main__":
    unittest.main()
