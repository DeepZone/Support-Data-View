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

    def test_parse_ratelimiter_runtime_reads_scope_and_counters(self):
        text = """ratelimitlanset:
   rllan-cfg:
     0: ip.version 6 icmp.type 135 (ratelimit) => 0 (# 56, blocked # 2) pakets 10 interval 1 seconds {now 100 endtime 90 count 1}
ratelimitwanset:
   rlwan-cfg:
     0: ip.proto 6 tcp.flags 0x002/0xfff (ratelimit) => 0 (# 129, blocked # 0) pakets 1000 interval 1 seconds {now 100 endtime 99 count 1}
"""
        entries = app.parse_ratelimiter_runtime(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].scope, "LAN")
        self.assertEqual(entries[0].blocked, 2)
        self.assertEqual(entries[1].scope, "WAN")
        self.assertEqual(entries[1].packets, 1000)

    def test_parse_ratelimiter_config_reads_rules(self):
        text = """ratelimits {
                enabled = yes;
                name = "dhcpv6";
                type = qos_cfg_system;
                iface = qos_lan;
                rule = "ip.version 6 udp.dport 547";
                packets = 10;
                interval = 1s;
                early = 0;
        } {
                enabled = no;
                name = "syn";
                type = qos_cfg_system;
                iface = qos_wan;
                rule = "ip.proto 6 tcp.flags 0x002/0xfff";
                packets = 1000;
                interval = 1s;
                early = 1;
        }
"""
        entries = app.parse_ratelimiter_config(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].name, "dhcpv6")
        self.assertTrue(entries[0].enabled)
        self.assertFalse(entries[1].enabled)
        self.assertEqual(entries[1].iface, "qos_wan")


    def test_parse_hardware_ratelimiter_sessions_extracts_fields(self):
        text = """accelerator: ratelimiter
protocol: TCP (6)
source IPv4: 80.66.224.98
destination IPv4: 80.72.48.243
source port: 24267
destination port: 499
matched packets: 1457
matched bytes: 116844
rule type: ACL IPO
covered by catchall: yes

accelerator: ratelimiter
protocol: TCP (6)
source IPv4: 80.66.224.99
destination IPv4: 80.72.48.244
source port: 11111
destination port: 443
matched packets: 100
matched bytes: 5000
rule type: ACL IPO
"""
        sessions = app.parse_hardware_ratelimiter_sessions(text)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].source_ip, "80.66.224.98")
        self.assertEqual(sessions[0].destination_port, 499)
        self.assertTrue(sessions[0].catchall)
        self.assertEqual(sessions[1].matched_packets, 100)

    def test_analyze_hardware_ratelimiter_sessions_aggregates_and_assesses(self):
        text = """accelerator: ratelimiter
source IPv4: 80.66.224.98
destination IPv4: 80.72.48.243
source port: 24267
destination port: 499
matched packets: 120000
matched bytes: 116844
rule type: ACL IPO
covered by catchall: yes

accelerator: ratelimiter
source IPv4: 1.2.3.4
destination IPv4: 80.72.48.243
destination port: 443
matched packets: 20
matched bytes: 1024
rule type: ACL IPO

accelerator: ratelimiter
source IPv4: 5.6.7.8
destination IPv4: 80.72.48.243
destination port: 80
matched packets: 30
matched bytes: 2048
rule type: ACL IPO
"""
        analysis = app.analyze_hardware_ratelimiter_sessions(app.parse_hardware_ratelimiter_sessions(text))
        self.assertEqual(analysis["summary"]["total_sessions"], 3)
        self.assertEqual(analysis["summary"]["total_packets"], 120050)
        self.assertIn("80.66.224.98", analysis["summary"]["unique_sources"])
        self.assertIn(499, analysis["summary"]["limited_ports"])
        self.assertIn("Catch-all Rate-Limiter-Regel aktiv.", analysis["assessment"])
        self.assertIn("Management-Port wird durch Rate-Limiter geschützt.", analysis["assessment"])
        self.assertIn("Sehr hohe Paketanzahl erkannt, möglicher Flood.", analysis["assessment"])


if __name__ == "__main__":
    unittest.main()
