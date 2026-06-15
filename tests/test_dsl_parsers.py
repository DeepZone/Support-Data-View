import unittest

import app


class DslSpectrumParserTests(unittest.TestCase):
    def test_parse_dsl_snr_reads_downstream_and_upstream_spectrum_arrays(self):
        text = """##### BEGIN SECTION DSL Spectrum synthetic lab data
Bits Array DS: 0,2,4,6,8
Bits Array US: 1,3,5
SNR Array DS: 120,121,119,118
SNR Array US: 90,91,92
HLOG DS Array: -10,-11,-12
HLOG US Array: -7,-8
##### END SECTION DSL Spectrum synthetic lab data"""

        dsl_data = app.parse_dsl_snr(text)

        self.assertEqual(dsl_data["Bits Array DS"], [0, 2, 4, 6, 8])
        self.assertEqual(dsl_data["Bits Array US"], [1, 3, 5])
        self.assertEqual(dsl_data["SNR Array DS"], [120, 121, 119, 118])
        self.assertEqual(dsl_data["SNR Array US"], [90, 91, 92])
        self.assertEqual(dsl_data["HLOG DS Array"], [-10, -11, -12])
        self.assertEqual(dsl_data["HLOG US Array"], [-7, -8])

    def test_parse_dsl_snr_returns_empty_arrays_for_missing_or_incomplete_spectrum_section(self):
        missing_section = app.parse_dsl_snr("synthetic support data without DSL spectrum")
        incomplete_section = app.parse_dsl_snr(
            """##### BEGIN SECTION DSL Spectrum synthetic partial data
SNR Array DS: 100,101
##### END SECTION DSL Spectrum synthetic partial data"""
        )

        expected_empty = {
            "Bits Array DS": [],
            "Bits Array US": [],
            "SNR Array DS": [],
            "SNR Array US": [],
            "HLOG DS Array": [],
            "HLOG US Array": [],
        }
        self.assertEqual(missing_section, expected_empty)
        self.assertEqual(incomplete_section["SNR Array DS"], [100, 101])
        self.assertEqual(incomplete_section["SNR Array US"], [])
        self.assertEqual(incomplete_section["Bits Array DS"], [])
        self.assertEqual(incomplete_section["HLOG US Array"], [])


class DslMetricsParserTests(unittest.TestCase):
    def test_parse_dsl_metrics_reads_sync_rates_margins_errors_and_retrain_counters(self):
        text = """#### BEGIN SECTION DSLManager_port_1_1 synthetic DSL manager
Training State: Showtime
Estimated loop length: 312
Downstream Rate: 116800 kBits/s
Upstream Rate: 46720 kBits/s
DS Margin (dB): 7.5
US Margin (dB): 8.0
DS Attenuation (dB): 14.5
US Attenuation (dB): 9.5
DS total FEC: 12345
US total FEC: 67
DS total CRC: 8
US total CRC: 1
DS ES: 2
US ES: 3
Resyncs: 0,1,0,2
Host triggered Retrains: 1,0,1
Bridgetaps
----------
BT length (m): 42
##### END SECTION DSLManager_port_1_1 synthetic DSL manager"""

        metrics = app.parse_dsl_metrics(text)

        self.assertEqual(metrics["loop_length_m"], 312)
        self.assertEqual(metrics["ds_rate_kbits"], 116800)
        self.assertEqual(metrics["us_rate_kbits"], 46720)
        self.assertEqual(metrics["ds_margin_db"], 7.5)
        self.assertEqual(metrics["us_margin_db"], 8.0)
        self.assertEqual(metrics["ds_attenuation_db"], 14.5)
        self.assertEqual(metrics["us_attenuation_db"], 9.5)
        self.assertEqual(metrics["ds_total_fec"], 12345)
        self.assertEqual(metrics["us_total_fec"], 67)
        self.assertEqual(metrics["ds_total_crc"], 8)
        self.assertEqual(metrics["us_total_crc"], 1)
        self.assertEqual(metrics["ds_es"], 2)
        self.assertEqual(metrics["us_es"], 3)
        self.assertEqual(metrics["resyncs_24h"], 3)
        self.assertEqual(metrics["retrains_24h"], 2)
        self.assertTrue(metrics["bridgetap_found"])
        self.assertEqual(metrics["bridgetap_length_m"], 42)

    def test_parse_dsl_metrics_handles_empty_missing_and_incomplete_dsl_manager_sections(self):
        self.assertEqual(app.parse_dsl_metrics("synthetic support data without DSL manager"), {})

        metrics = app.parse_dsl_metrics(
            """#### BEGIN SECTION DSLManager_port_1_1 synthetic partial manager
Downstream Rate: 50000 kBits/s
Bridgetaps
----------
no bridge taps found
##### END SECTION DSLManager_port_1_1 synthetic partial manager"""
        )

        self.assertEqual(metrics["ds_rate_kbits"], 50000)
        self.assertIsNone(metrics["us_rate_kbits"])
        self.assertIsNone(metrics["ds_total_crc"])
        self.assertIsNone(metrics["us_total_fec"])
        self.assertIsNone(metrics["resyncs_24h"])
        self.assertFalse(metrics["bridgetap_found"])
        self.assertIsNone(metrics["bridgetap_length_m"])

    def test_parse_support_data_exposes_synthetic_dsl_spectrum_and_metrics(self):
        text = """##### BEGIN SECTION DSL Spectrum synthetic lab data
SNR Array DS: 110,111
SNR Array US: 80,81
##### END SECTION DSL Spectrum synthetic lab data
#### BEGIN SECTION DSLManager_port_1_1 synthetic DSL manager
Training State: Showtime
Downstream Rate: 100000 kBits/s
Upstream Rate: 40000 kBits/s
DS total CRC: 0
US total CRC: 0
##### END SECTION DSLManager_port_1_1 synthetic DSL manager"""

        parsed = app.parse_support_data(text)

        self.assertEqual(parsed["access_technology"], "DSL")
        self.assertEqual(parsed["dsl_data"]["SNR Array DS"], [110, 111])
        self.assertEqual(parsed["dsl_data"]["SNR Array US"], [80, 81])
        self.assertEqual(parsed["dsl_metrics"]["ds_rate_kbits"], 100000)
        self.assertEqual(parsed["dsl_metrics"]["us_rate_kbits"], 40000)
        self.assertEqual(parsed["dsl_metrics"]["ds_total_crc"], 0)
        self.assertEqual(parsed["dsl_metrics"]["us_total_crc"], 0)


if __name__ == "__main__":
    unittest.main()
