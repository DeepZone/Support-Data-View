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

    def test_parse_cable_spectrum_reads_points(self):
        text = """##### BEGIN SECTION DOCSIS cable spectrum
# min, max, step size, data...
1000000, 2000000, 500000, 10, 20, 30
##### END SECTION DOCSIS cable spectrum"""
        points = app.parse_cable_spectrum(text)
        self.assertEqual(
            points,
            [
                {"Frequenz (MHz)": 1.0, "Pegel (dB)": 1.0},
                {"Frequenz (MHz)": 1.5, "Pegel (dB)": 2.0},
                {"Frequenz (MHz)": 2.0, "Pegel (dB)": 3.0},
            ],
        )

    def test_parse_cable_spectrum_returns_empty_for_invalid_data(self):
        text = """##### BEGIN SECTION DOCSIS cable spectrum
1000000, 2000000, 0, 10
##### END SECTION DOCSIS cable spectrum"""
        self.assertEqual(app.parse_cable_spectrum(text), [])


if __name__ == "__main__":
    unittest.main()
