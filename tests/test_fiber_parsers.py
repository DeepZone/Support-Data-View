import unittest

import app


SYNTHETIC_FIBER_SECTION = """#### BEGIN SECTION FIBERManager_port_1_1 synthetic fiber manager
Training State: Showtime
Downstream Rate: 2500000 kBits/s
Upstream Rate: 1250000 kBits/s
OLT Vendor: Synthetic OLT Labs
OLT Vendor ID: SYNL
OLT VersionNumber: XGS-PON synthetic profile 1.0
SFP Label: Synthetic GPON SFP
SFP Vendor: Synthetic Optics Ltd
SFP Part Number: SYN-GPON-001
SFP Serial: SYNTHETIC123456
Vlan Rule Table:
 0 | 0 7 0 | 0 100 0 0 | 1 |
 1 | 0 7 0 | 0 200 0 0 | 0 |
Vlan Rule Translation:
Temperature (0.1 deg C): 423
Supply Voltage (mV): 3298
Tx Bias Current (mA): 18.5
Tx Optical Pwr (0.1 dBm): 32
Rx Received Pwr (0.1 dBm): -181
APD Voltage (0.1 V): 392
Current PLOAM State: O5 Operation
Emergency Alarm State: Off
#### END SECTION FIBERManager_port_1_1"""


class FiberOverviewParserTests(unittest.TestCase):
    def test_parse_fiber_overview_reads_rates_optics_sfp_olt_vlan_and_pon_state(self):
        fiber_data = app.parse_fiber_overview(SYNTHETIC_FIBER_SECTION)

        self.assertEqual(fiber_data["downstream_rate_kbits"], 2500000)
        self.assertEqual(fiber_data["upstream_rate_kbits"], 1250000)
        self.assertEqual(fiber_data["olt_vendor"], "Synthetic OLT Labs")
        self.assertEqual(fiber_data["olt_vendor_id"], "SYNL")
        self.assertEqual(fiber_data["olt_version"], "XGS-PON synthetic profile 1.0")
        self.assertEqual(fiber_data["sfp_label"], "Synthetic GPON SFP")
        self.assertEqual(fiber_data["sfp_vendor"], "Synthetic Optics Ltd")
        self.assertEqual(fiber_data["sfp_part_number"], "SYN-GPON-001")
        self.assertEqual(fiber_data["sfp_serial"], "SYNTHETIC123456")
        self.assertEqual(
            fiber_data["vlan_rules"],
            [
                {"Regel": 0, "Outer Prio": 0, "Outer VLAN": 7, "Inner Prio": 0, "Inner VLAN": 100, "Remove Tags": 1},
                {"Regel": 1, "Outer Prio": 0, "Outer VLAN": 7, "Inner Prio": 0, "Inner VLAN": 200, "Remove Tags": 0},
            ],
        )
        self.assertEqual(fiber_data["temperature_c"], 42.3)
        self.assertEqual(fiber_data["supply_voltage_v"], 3.298)
        self.assertEqual(fiber_data["tx_bias_ma"], 18.5)
        self.assertEqual(fiber_data["tx_optical_dbm"], 3.2)
        self.assertEqual(fiber_data["rx_optical_dbm"], -18.1)
        self.assertEqual(fiber_data["apd_voltage_v"], 39.2)
        self.assertEqual(fiber_data["ploam_state"], "O5 Operation")
        self.assertEqual(fiber_data["ploam_alarm"], "Off")

    def test_parse_fiber_overview_handles_missing_empty_and_incomplete_sections(self):
        self.assertEqual(app.parse_fiber_overview("synthetic support data without fiber manager"), {})

        empty_section = app.parse_fiber_overview(
            """#### BEGIN SECTION FIBERManager_port_1_1
#### END SECTION FIBERManager_port_1_1"""
        )
        self.assertIsNone(empty_section["downstream_rate_kbits"])
        self.assertIsNone(empty_section["upstream_rate_kbits"])
        self.assertEqual(empty_section["vlan_rules"], [])
        self.assertIsNone(empty_section["sfp_vendor"])
        self.assertIsNone(empty_section["tx_optical_dbm"])
        self.assertIsNone(empty_section["rx_optical_dbm"])
        self.assertIsNone(empty_section["ploam_state"])

        incomplete_section = app.parse_fiber_overview(
            """#### BEGIN SECTION FIBERManager_port_1_1 synthetic incomplete AON manager
Training State: Idle
Downstream Rate: 1000000 kBits/s
SFP Vendor: Synthetic AON Optics
Tx Optical Pwr (0.1 dBm): not-a-number
#### END SECTION FIBERManager_port_1_1"""
        )
        self.assertEqual(incomplete_section["downstream_rate_kbits"], 1000000)
        self.assertIsNone(incomplete_section["upstream_rate_kbits"])
        self.assertEqual(incomplete_section["sfp_vendor"], "Synthetic AON Optics")
        self.assertIsNone(incomplete_section["tx_optical_dbm"])
        self.assertEqual(incomplete_section["vlan_rules"], [])


class FiberAccessTechnologyTests(unittest.TestCase):
    def test_detect_access_technology_prefers_active_fiber_over_missing_or_inactive_dsl(self):
        self.assertEqual(app.detect_access_technology(SYNTHETIC_FIBER_SECTION), "Fiber")

        inactive_dsl_with_fiber = """#### BEGIN SECTION DSLManager_port_1_1 synthetic inactive DSL
Training State: Idle
Downstream Rate: 0 kBits/s
##### END SECTION DSLManager_port_1_1 synthetic inactive DSL
""" + SYNTHETIC_FIBER_SECTION
        self.assertEqual(app.detect_access_technology(inactive_dsl_with_fiber), "Fiber")

    def test_detect_access_technology_uses_fiber_markers_when_fiber_section_has_no_link(self):
        config_marker = "CONFIG_FIBER=y\nsynthetic data without link counters"
        overview_marker = "FIBER Overview\nsynthetic overview without manager section"

        self.assertEqual(app.detect_access_technology(config_marker), "Fiber")
        self.assertEqual(app.detect_access_technology(overview_marker), "Fiber")


class FiberSupportDataIntegrationTests(unittest.TestCase):
    def test_parse_support_data_exposes_synthetic_fiber_data_and_access_technology(self):
        parsed = app.parse_support_data(SYNTHETIC_FIBER_SECTION)

        self.assertEqual(parsed["access_technology"], "Fiber")
        self.assertEqual(parsed["fiber_data"]["downstream_rate_kbits"], 2500000)
        self.assertEqual(parsed["fiber_data"]["upstream_rate_kbits"], 1250000)
        self.assertEqual(parsed["fiber_data"]["olt_version"], "XGS-PON synthetic profile 1.0")
        self.assertEqual(parsed["fiber_data"]["sfp_label"], "Synthetic GPON SFP")
        self.assertEqual(parsed["fiber_data"]["tx_optical_dbm"], 3.2)
        self.assertEqual(parsed["fiber_data"]["rx_optical_dbm"], -18.1)
        self.assertEqual(parsed["fiber_data"]["ploam_state"], "O5 Operation")


if __name__ == "__main__":
    unittest.main()
