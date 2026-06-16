import unittest

import pandas as pd

import app


class WifiSupportDataRegressionTests(unittest.TestCase):
    def test_wlan_environment_scan_keeps_ssid_and_radio_metadata(self):
        text = '''##### BEGIN SECTION ENV_SCAN WLAN environment scan results
bss[0]: ssid = "Lab<&>Net" rssi = -42 radioband = 102 frequency = 5180
bss[1]: ssid = "Guest" rssi = -77 radioband = 101 frequency = 2412
##### END SECTION ENV_SCAN WLAN environment scan results'''

        networks = app.parse_wlan_env_scan(text)

        self.assertEqual(len(networks), 2)
        self.assertEqual(networks[0].ssid, "Lab<&>Net")
        self.assertEqual(networks[0].rssi, -42)
        self.assertEqual(networks[0].radioband, 102)
        self.assertEqual(networks[0].frequency, 5180)
        self.assertEqual(networks[1].ssid, "Guest")
        self.assertEqual(networks[1].radioband, 101)

    def test_wlan_noisefloor_classifies_24_5_and_6_ghz_entries(self):
        text = '''##### BEGIN SECTION WLAN_SCAN_RESULTS WLAN scan results
Scan results for radio '101':
Noisefloor table:
[  0]: 2412 MHz (  1) -95 12
[  1]: 5180 MHz ( 36) -101 3
[  2]: 5955 MHz (  1) -98 1
Scan table:
##### END SECTION WLAN_SCAN_RESULTS WLAN scan results'''

        entries = app.parse_wlan_noisefloor(text)

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].band, "2,4 GHz")
        self.assertEqual(entries[0].channel, 1)
        self.assertEqual(entries[0].noise_floor, -95)
        self.assertEqual(entries[1].band, "5 GHz")
        self.assertEqual(entries[1].frequency_mhz, 5180)
        self.assertEqual(entries[2].band, "6 GHz")
        self.assertEqual(entries[2].load, 1)

    def test_wlan_station_parser_handles_missing_optional_values(self):
        text = '''##### BEGIN SECTION STATION_LIST WLAN client list
mac = 'aa:bb:cc:dd:ee:ff'
if_name = ath0
connect_state = 1
rate_rx = 866
rate_tx = 433
rate_rx_max = 1200
rate_tx_max = 1200
rssi = -49
quality = 91
----------------------------------------
mac = '11:22:33:44:55:66'
if_name = ath1
connect_state = 0
##### END SECTION STATION_LIST WLAN client list'''

        stations = app.parse_wlan_stations(text)

        self.assertEqual(len(stations), 2)
        self.assertEqual(stations[0].mac, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(stations[0].rate_rx, 866)
        self.assertEqual(stations[0].quality, 91)
        self.assertEqual(stations[1].mac, "11:22:33:44:55:66")
        self.assertEqual(stations[1].rate_rx, 0)
        self.assertEqual(stations[1].rssi, 0)


class DeviceMetadataRegressionTests(unittest.TestCase):
    def test_fritz_metadata_parsers_extract_model_firmware_uptime_and_load(self):
        text = '''##### TITLE Version 304.08.24
##### TITLE SubVersion -133524
CONFIG_PRODUKT_NAME='FRITZ!Box 7630'
uptime: 3 days, 42 min, load average: 0.11, 0.22, 0.33
maca 08:b6:57:12:34:56
'''

        self.assertEqual(app.parse_fritz_model(text), "FRITZ!Box 7630")
        self.assertEqual(app.parse_fritz_firmware_version(text), "08.24-133524")
        self.assertEqual(app.parse_fritz_uptime_days_minutes(text), "3 Tage, 42 Min")
        self.assertEqual(app.parse_fritz_load_average(text), ["0.11", "0.22", "0.33"])
        self.assertEqual(app.extract_device_mac(text), "08:B6:57:12:34:56")

    def test_minimal_support_data_parse_is_tolerant(self):
        parsed = app.parse_support_data("ordinary truncated support data without known sections")

        self.assertIn("access_technology", parsed)
        self.assertEqual(parsed["networks"], [])
        self.assertEqual(parsed["stations"], [])
        self.assertEqual(parsed["ports"], [])
        self.assertEqual(parsed["events"], [])


class UploadAndHtmlRegressionTests(unittest.TestCase):
    def test_escape_html_quotes_support_data_values_for_attributes(self):
        self.assertEqual(
            app.escape_html('SSID "Lab" <script>alert(1)</script>'),
            'SSID &quot;Lab&quot; &lt;script&gt;alert(1)&lt;/script&gt;',
        )

    def test_mac_copy_component_escapes_display_and_serializes_copy_value(self):
        html = app.build_mac_address_copy_component_html('AA:BB:"<>&:CC')

        self.assertIn('AA:BB:&quot;&lt;&gt;&amp;:CC', html)
        self.assertIn('const macaValue = "AA:BB:\\"<>&:CC";', html)
        self.assertIn('button.addEventListener("click"', html)
        self.assertIn('navigator.clipboard.writeText(macaValue)', html)
        self.assertIn('fallbackCopy(macaValue)', html)
        self.assertNotIn('data-copy="AA:BB:"<>&:CC"', html)


    def test_normalize_display_dataframe_stringifies_mixed_object_values(self):
        df = pd.DataFrame({"Wert": [1, "zwei", b"drei\xff", None]})

        normalized = app.normalize_display_dataframe(df)

        self.assertEqual(list(normalized["Wert"]), ["1", "zwei", "drei�", pd.NA])
        self.assertEqual(list(df["Wert"]), [1, "zwei", b"drei\xff", None])

    def test_upload_decoder_accepts_uppercase_txt_and_drops_invalid_utf8(self):
        decoded = app.decode_support_data_upload("SUPPORT.TXT", b"valid\xff text")

        self.assertEqual(decoded, "valid text")

    def test_upload_decoder_rejects_oversized_support_data(self):
        payload = b"x" * (app.MAX_UPLOAD_SIZE_BYTES + 1)

        with self.assertRaises(ValueError):
            app.decode_support_data_upload("support.txt", payload)


if __name__ == "__main__":
    unittest.main()
