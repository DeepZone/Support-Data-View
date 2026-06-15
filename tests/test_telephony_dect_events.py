import unittest

import app
from support_viewer.parsers.dect import (
    extract_dect_rssi_index_to_dbm,
    parse_dect_basis_info,
    parse_dect_device_info,
)
from support_viewer.parsers.events import parse_events
from support_viewer.parsers.telephony import parse_voip_accounts


def dect_device_line(name="LabPhone", model="8.3", ber="0.25"):
    values = [""] * 38
    values[0] = name
    values[2] = "7"
    values[3] = model
    values[5] = "01 23 45 67 89"
    values[7] = "G.722"
    values[27] = ber
    values[28:38] = ["1", "2", "-", "4", "5", "-61.5", "7", "8", "9", "10"]
    return ",".join(values)


def dect_hg_line():
    values = [""] * 25
    values[12] = "0.75"
    values[13:23] = ["10", "9", "8", "-", "6", "5", "4", "3", "2", "1"]
    values[23] = "1"
    values[24] = "4.98"
    return ",".join(values)


class TelephonyVoipParserTests(unittest.TestCase):
    def test_app_reexports_telephony_parse_voip_accounts(self):
        self.assertIs(app.parse_voip_accounts, parse_voip_accounts)

    def test_parse_voip_accounts_extracts_registration_security_traffic_and_call_counters(self):
        text = """##### BEGIN SECTION voip Voice over IP
ua0 (11111@sip.example.test, UDP, port=5060, sipiface=internet): registered -- reachability 100 % (OK)
0: Cipher: TLS_AES_256_GCM_SHA384 / SRTP_AES_CM_128_HMAC_SHA1_80
0: RX: 12345 bytes, 67 pkts, TX: 89012 bytes, 345 pkts, Lost packets: 2
0: Outgoing Calls: 9 attempted, 8 answered, 7 connected, 1 failed
0: Incoming Calls: 6 received, 5 answered, 4 connected, 1 failed
0: Overall Calls: 3 dropped, Total Call Time = 01:02:03
0: Direct Loopback: 2 connected, 1 failed
ua1 (22222@backup.example.test, TCP, port=5061, sipiface=voip): not registered -- reachability 0 % (failed)
1: RX: 0 bytes, 0 pkts, TX: 10 bytes, 1 pkts, Lost packets: 0
##### END SECTION voip Voice over IP"""

        accounts = app.parse_voip_accounts(text)

        self.assertEqual(len(accounts), 2)
        primary = accounts[0]
        self.assertEqual(primary.index, 0)
        self.assertEqual(primary.number, "11111")
        self.assertEqual(primary.provider, "sip.example.test")
        self.assertEqual(primary.transport, "UDP")
        self.assertEqual(primary.port, 5060)
        self.assertEqual(primary.sip_interface, "internet")
        self.assertTrue(primary.registered)
        self.assertEqual(primary.reachability, 100)
        self.assertEqual(primary.cipher, "TLS_AES_256_GCM_SHA384 / SRTP_AES_CM_128_HMAC_SHA1_80")
        self.assertEqual((primary.rx_bytes, primary.rx_pkts, primary.tx_bytes, primary.tx_pkts), (12345, 67, 89012, 345))
        self.assertEqual(primary.lost_pkts, 2)
        self.assertEqual((primary.outgoing_attempted, primary.outgoing_answered, primary.outgoing_connected, primary.outgoing_failed), (9, 8, 7, 1))
        self.assertEqual((primary.incoming_received, primary.incoming_answered, primary.incoming_connected, primary.incoming_failed), (6, 5, 4, 1))
        self.assertEqual(primary.dropped_calls, 3)
        self.assertEqual(primary.total_call_time, "01:02:03")
        self.assertEqual((primary.loopback_connected, primary.loopback_failed), (2, 1))
        secondary = accounts[1]
        self.assertFalse(secondary.registered)
        self.assertEqual(secondary.reachability, 0)


class DectParserTests(unittest.TestCase):
    def test_app_reexports_dect_parser_functions(self):
        self.assertIs(app.extract_dect_rssi_index_to_dbm, extract_dect_rssi_index_to_dbm)
        self.assertIs(app.parse_dect_device_info, parse_dect_device_info)
        self.assertIs(app.parse_dect_basis_info, parse_dect_basis_info)

    def test_extract_dect_rssi_index_to_dbm_uses_synthetic_mapping_and_falls_back_on_invalid_data(self):
        mapping = app.extract_dect_rssi_index_to_dbm(
            "DECT_RSSI_INDEX_TO_DBM = -90.0 -89.0 -88.0 -87.0 -86.0 -85.0 -84.0 -83.0 -82.0 -81.0"
        )

        self.assertEqual(mapping[1], -90.0)
        self.assertEqual(mapping[10], -81.0)
        self.assertEqual(app.extract_dect_rssi_index_to_dbm("DECT_RSSI_INDEX_TO_DBM = -1 -2"), app.DEFAULT_DECT_RSSI_INDEX_TO_DBM)

    def test_parse_dect_basis_info_extracts_feature_flags_and_rfpi(self):
        text = """##### BEGIN SECTION DECTBasisInfo
Basis DECT_ENABLED=1, DECT_REPEATER_ENABLED=0, ECOMode=1, NoEmission=1, NoEmissionState=0, RepeaterMode=0, OverlappedSending=1, ExtSecurity=1, CATIQ20SUPPORT=1, PINProtect=1, AVMULEAES=1
RFPI=12 34 56 78 9A
##### END SECTION DECTBasisInfo"""

        basis = app.parse_dect_basis_info(text)

        self.assertIsNotNone(basis)
        self.assertEqual(basis.dect_enabled, 1)
        self.assertEqual(basis.dect_repeater_enabled, 0)
        self.assertEqual(basis.eco_mode, 1)
        self.assertEqual(basis.no_emission, 1)
        self.assertEqual(basis.no_emission_state, 0)
        self.assertEqual(basis.repeater_mode, 0)
        self.assertEqual(basis.ext_security, 1)
        self.assertEqual(basis.catiq20support, 1)
        self.assertEqual(basis.pin_protect, 1)
        self.assertEqual(basis.avmuleaes, 1)
        self.assertEqual(basis.rfpi, "12 34 56 78 9A")

    def test_parse_dect_device_info_extracts_devices_and_skips_incomplete_blocks(self):
        text = f"""##### BEGIN SECTION DECTDeviceInfo
Name,synthetic header
{dect_device_line()}
{dect_hg_line()}
Incomplete,1,2
ULE Devices
ignored
##### END SECTION DECTDeviceInfo"""
        mapping = {index: -100.0 + index for index in range(1, 11)}

        devices = app.parse_dect_device_info(text, mapping)

        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.name, "LabPhone")
        self.assertEqual(device.hgid, 7)
        self.assertEqual(device.model, "FRITZ!Fon C6 (8.3)")
        self.assertEqual(device.ipui, "01 23 45 67 89")
        self.assertEqual(device.curr_codec, "G.722")
        self.assertEqual(device.ber, 0.25)
        self.assertEqual(device.rssi_values, [-99.0, -98.0, -96.0, -95.0, -61.5, -93.0, -92.0, -91.0, -90.0])
        self.assertEqual(device.hg_ber, 0.75)
        self.assertEqual(device.hg_rssi_values, [-90.0, -91.0, -92.0, -94.0, -95.0, -96.0, -97.0, -98.0, -99.0])
        self.assertEqual(device.no_emission, 1)
        self.assertEqual(device.fw_version, "4.98")


class EventParserTests(unittest.TestCase):
    def test_app_parse_events_reexports_parser_function(self):
        self.assertIs(app.parse_events, parse_events)

    def test_parse_events_extracts_multiple_date_time_message_rows_and_ignores_incomplete_rows(self):
        text = """##### BEGIN SECTION Events Events
Events
--------
15.06.26 09:10:11 Synthetic internet connection established.
15.06.26 09:11:12 Synthetic telephony registration failed.
incomplete event row without date
15.06.26 09:12:13 Synthetic DECT handset registered.
##### END SECTION Events Events"""

        events = app.parse_events(text)

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].date, "15.06.26")
        self.assertEqual(events[0].time, "09:10:11")
        self.assertEqual(events[0].message, "Synthetic internet connection established.")
        self.assertEqual(events[2].message, "Synthetic DECT handset registered.")

    def test_parse_events_returns_empty_for_missing_or_empty_sections(self):
        self.assertEqual(app.parse_events(""), [])
        self.assertEqual(app.parse_events("##### BEGIN SECTION Events Events\nEvents\n-----\n##### END SECTION Events Events"), [])


class SupportDataIntegrationTests(unittest.TestCase):
    def test_parse_support_data_exposes_voip_dect_and_events(self):
        text = f"""DECT_RSSI_INDEX_TO_DBM = -90.0 -89.0 -88.0 -87.0 -86.0 -85.0 -84.0 -83.0 -82.0 -81.0
##### BEGIN SECTION voip Voice over IP
ua0 (33333@sip.integration.test, TLS, port=5061, sipiface=internet): registration ok -- reachability 99 % (OK)
##### END SECTION voip Voice over IP
##### BEGIN SECTION DECTBasisInfo
Basis DECT_ENABLED=0, DECT_REPEATER_ENABLED=1, ECOMode=0, NoEmission=0, ExtSecurity=1, CATIQ20SUPPORT=1, PINProtect=0
RFPI=AA BB CC DD EE
##### END SECTION DECTBasisInfo
##### BEGIN SECTION DECTDeviceInfo
{dect_device_line(name="IntegrationPhone", model="8.4", ber="-")}
{dect_hg_line()}
##### END SECTION DECTDeviceInfo
##### BEGIN SECTION Events Events
15.06.26 10:00:00 Synthetic integration event.
##### END SECTION Events Events"""

        parsed = app.parse_support_data(text)

        self.assertEqual(parsed["voip_accounts"][0].number, "33333")
        self.assertTrue(parsed["voip_accounts"][0].registered)
        self.assertEqual(parsed["dect_basis_info"].dect_enabled, 0)
        self.assertEqual(parsed["dect_basis_info"].dect_repeater_enabled, 1)
        self.assertEqual(parsed["dect_devices"][0].name, "IntegrationPhone")
        self.assertEqual(parsed["dect_devices"][0].model, "FRITZ!Fon X6 (8.4)")
        self.assertEqual(parsed["events"][0].message, "Synthetic integration event.")


if __name__ == "__main__":
    unittest.main()
