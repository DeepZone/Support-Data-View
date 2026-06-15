import unittest

import app
from support_viewer import utils


class UtilsExtractionTests(unittest.TestCase):
    def test_extract_section_returns_content_between_markers(self):
        text = "before <start>\nvalue\n<end> after"
        self.assertEqual(utils.extract_section(text, "<start>", "<end>"), "\nvalue\n")
        self.assertEqual(utils.extract_section(text, "missing", "<end>"), "")

    def test_extract_section_by_prefix_stops_at_end_section_marker(self):
        text = """noise
##### BEGIN SECTION Example
line 1
##### END SECTION Example
trailing"""
        self.assertEqual(
            utils.extract_section_by_prefix(text, "##### BEGIN SECTION Example"),
            "##### BEGIN SECTION Example\nline 1\n",
        )
        self.assertEqual(utils.extract_section_by_prefix(text, "##### BEGIN SECTION Missing"), "")

    def test_extract_section_by_prefix_returns_tail_without_end_marker(self):
        text = "prefix\n##### BEGIN SECTION Open\nline"
        self.assertEqual(
            utils.extract_section_by_prefix(text, "##### BEGIN SECTION Open"),
            "##### BEGIN SECTION Open\nline",
        )

    def test_extract_numeric_array_reads_comma_separated_integers(self):
        text = "values: 1,-2,3\nother: 9"
        self.assertEqual(utils.extract_numeric_array(text, "values"), [1, -2, 3])
        self.assertEqual(utils.extract_numeric_array(text, "missing"), [])

    def test_extract_numeric_array_loose_allows_space_before_colon(self):
        text = "values   : 4,-5,6"
        self.assertEqual(utils.extract_numeric_array_loose(text, "values"), [4, -5, 6])
        self.assertEqual(utils.extract_numeric_array_loose(text, "missing"), [])

    def test_extract_int_float_and_kbits_values(self):
        text = """count: -42
ratio: -3.25
sync rate: 109344 kBits/s"""
        self.assertEqual(utils.extract_int_value(text, "count"), -42)
        self.assertEqual(utils.extract_float_value(text, "ratio"), -3.25)
        self.assertEqual(utils.extract_kbits_rate(text, "sync rate"), 109344)
        self.assertIsNone(utils.extract_int_value(text, "missing"))
        self.assertIsNone(utils.extract_float_value(text, "missing"))
        self.assertIsNone(utils.extract_kbits_rate(text, "missing"))

    def test_extract_section_block_reads_table_body_until_next_heading(self):
        text = """Bridgetaps
----------
1 row one
2 row two
Next Section:
ignored"""
        self.assertEqual(utils.extract_section_block(text, "Bridgetaps"), "1 row one\n2 row two")
        self.assertEqual(utils.extract_section_block(text, "Missing"), "")

    def test_extract_section_between_returns_content_between_markers(self):
        text = "alpha [begin]payload\nmore[end] omega"
        self.assertEqual(utils.extract_section_between(text, "[begin]", "[end]"), "payload\nmore")
        self.assertEqual(utils.extract_section_between(text, "[missing]", "[end]"), "")


class UtilsParseTests(unittest.TestCase):
    def test_parse_optional_float_handles_missing_dash_and_invalid_values(self):
        self.assertIsNone(utils.parse_optional_float(None))
        self.assertIsNone(utils.parse_optional_float(""))
        self.assertIsNone(utils.parse_optional_float(" - "))
        self.assertIsNone(utils.parse_optional_float("not-a-number"))
        self.assertEqual(utils.parse_optional_float(" 12.5 "), 12.5)

    def test_parse_channel_float_accepts_decimal_comma_and_rejects_invalid(self):
        self.assertEqual(utils.parse_channel_float("3,25"), 3.25)
        self.assertEqual(utils.parse_channel_float(" 4.5 "), 4.5)
        self.assertIsNone(utils.parse_channel_float(None))
        self.assertIsNone(utils.parse_channel_float(""))
        self.assertIsNone(utils.parse_channel_float("n/a"))

    def test_parse_int_accepts_integral_strings_and_rejects_invalid(self):
        self.assertEqual(utils.parse_int("7"), 7)
        self.assertEqual(utils.parse_int("-8"), -8)
        self.assertIsNone(utils.parse_int(None))
        self.assertIsNone(utils.parse_int("1.5"))
        self.assertIsNone(utils.parse_int("abc"))

    def test_parse_frequency_range_normalizes_order(self):
        self.assertEqual(utils._parse_frequency_range("108.975 - 256.975"), (108.975, 256.975))
        self.assertEqual(utils._parse_frequency_range("256.975 - 108.975"), (108.975, 256.975))
        self.assertIsNone(utils._parse_frequency_range(None))
        self.assertIsNone(utils._parse_frequency_range("invalid"))

    def test_escape_html_escapes_text_and_attribute_sensitive_characters(self):
        self.assertEqual(
            utils.escape_html('SSID "Lab" <script>&</script>'),
            "SSID &quot;Lab&quot; &lt;script&gt;&amp;&lt;/script&gt;",
        )


class AppImportCompatibilityTests(unittest.TestCase):
    def test_app_exports_selected_utils(self):
        self.assertIs(app.extract_section, utils.extract_section)
        self.assertIs(app.parse_int, utils.parse_int)
        self.assertIs(app.parse_channel_float, utils.parse_channel_float)
        self.assertIs(app.escape_html, utils.escape_html)

    def test_app_exported_utils_remain_callable(self):
        self.assertEqual(app.extract_section("a[start]x[end]b", "[start]", "[end]"), "x")
        self.assertEqual(app.parse_int("23"), 23)
        self.assertEqual(app.parse_channel_float("1,75"), 1.75)
        self.assertEqual(app.escape_html("<x>"), "&lt;x&gt;")


if __name__ == "__main__":
    unittest.main()
