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

    def test_parse_frequency_range_parses_values(self):
        self.assertEqual(app._parse_frequency_range("108.975 - 256.975"), (108.975, 256.975))
        self.assertEqual(app._parse_frequency_range("256.975-108.975"), (108.975, 256.975))
        self.assertIsNone(app._parse_frequency_range("invalid"))

    def test_build_cable_usage_ranges_contains_docsis_and_plc(self):
        docsis_data = {
            "downstream_channels": [{"Frequenz (MHz)": 618.0}],
            "ofdm_channels": [{"Frequenz (MHz)": "108.975 - 256.975", "PLC Freq (MHz)": 171.0}],
        }
        spectrum_points = [
            {"Frequenz (MHz)": 100.0, "Pegel (dB)": -30.0},
            {"Frequenz (MHz)": 120.0, "Pegel (dB)": 2.0},
            {"Frequenz (MHz)": 300.0, "Pegel (dB)": 4.0},
            {"Frequenz (MHz)": 301.0, "Pegel (dB)": 4.5},
            {"Frequenz (MHz)": 302.0, "Pegel (dB)": -35.0},
            {"Frequenz (MHz)": 303.0, "Pegel (dB)": -34.0},
        ]
        ranges = app.build_cable_usage_ranges(docsis_data, spectrum_points)
        categories = {entry["Kategorie"] for entry in ranges}
        self.assertIn("Verwendeter DOCSIS 3.1-Kanal", categories)
        self.assertIn("Verwendeter DOCSIS 3.0-Kanal", categories)
        self.assertIn("PLC", categories)
        self.assertIn("TV-Signal", categories)
        self.assertIn("Ausschlussbereich", categories)

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

    def test_parse_avm_counter_rrd_sections_extracts_requested_sections(self):
        text = """##### BEGIN SECTION AVM Counter rrdtoolapi names
name_1
##### END SECTION AVM Counter rrdtoolapi names
##### BEGIN SECTION AVM Counter rrdtoolapi values
value_1
##### END SECTION AVM Counter rrdtoolapi values
##### BEGIN SECTION AVM Counter showrrdstate
state_1
##### END SECTION AVM Counter showrrdstate
"""
        sections = app.parse_avm_counter_rrd_sections(text)
        self.assertEqual([section.title for section in sections], ["rrdtoolapi names", "rrdtoolapi values", "showrrdstate"])
        self.assertEqual(sections[0].content, "name_1")
        self.assertEqual(sections[1].content, "value_1")
        self.assertEqual(sections[2].content, "state_1")

    def test_parse_avm_counter_values_extracts_entries(self):
        content = """nets:
  <<< rcv_v4_lan 224251990895 C (age 3s) (UID datasource8180)
  >>> snd_v4_lan 102140772501 C (age 3s) (UID datasource8186)
cpuusage:
      idle 96 V (age 3s) (UID datasource8159)
"""
        entries = app.parse_avm_counter_values(content)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].category, "nets")
        self.assertEqual(entries[0].direction, "<<<")
        self.assertEqual(entries[0].metric, "rcv_v4_lan")
        self.assertEqual(entries[0].value, 224251990895)
        self.assertEqual(entries[1].direction, ">>>")
        self.assertEqual(entries[2].value_type, "V")

    def test_summarize_avm_counter_values_returns_totals(self):
        sections = [
            app.AvmCounterSection(
                title="rrdtoolapi values",
                content="""nets:
  <<< rcv_v4_lan 1000 C (age 3s) (UID datasource1)
  >>> snd_v4_lan 500 C (age 3s) (UID datasource2)
onlinemonitor_xfrm_0:
  <<< ds_normal 0 C (age 778540s) (UID datasource3)
""",
            )
        ]
        summary = app.summarize_avm_counter_values(sections)
        self.assertEqual(summary["total_entries"], 3)
        self.assertEqual(summary["total_rx"], 1000)
        self.assertEqual(summary["total_tx"], 500)
        self.assertEqual(summary["stale_entries"], 1)
        self.assertEqual(summary["top_categories"][0]["Kategorie"], "nets")

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

    def _build_hardware_analysis(self, session_text: str):
        sessions = app.parse_hardware_ratelimiter_sessions(session_text)
        return app.analyze_hardware_ratelimiter_sessions(sessions)

    def test_connection_performance_unauffaellig(self):
        runtime = [
            app.RatelimiterRuntimeEntry("WAN", "rule", packets=1000, interval_seconds=1, hits=10, blocked=0)
        ]
        config = [app.RatelimiterConfigEntry("rule", "qos_wan", "ip.proto 6", 1000, "1s", 0, True)]
        hardware_analysis = self._build_hardware_analysis(
            """accelerator: ratelimiter
source IPv4: 192.168.1.10
destination IPv4: 80.72.48.243
destination port: 80
matched packets: 120
matched bytes: 2048
"""
        )
        result = app.analyze_connection_performance(runtime, config, hardware_analysis, ["0.12", "0.10", "0.09"], "")
        self.assertEqual(result["status"], "green")
        self.assertLess(result["score"], 35)

    def test_connection_performance_beobachten(self):
        runtime = [
            app.RatelimiterRuntimeEntry("WAN", "rule", packets=1000, interval_seconds=1, hits=250, blocked=50)
        ]
        config = [app.RatelimiterConfigEntry("rule", "qos_wan", "ip.proto 6", 1000, "1s", 0, True)]
        hardware_analysis = self._build_hardware_analysis(
            """accelerator: ratelimiter
source IPv4: 1.2.3.4
destination IPv4: 80.72.48.243
destination port: 80
matched packets: 1000
matched bytes: 2048

accelerator: ratelimiter
source IPv4: 5.6.7.8
destination IPv4: 80.72.48.243
destination port: 443
matched packets: 800
matched bytes: 1024

accelerator: ratelimiter
source IPv4: 9.8.7.6
destination IPv4: 80.72.48.243
destination port: 53
matched packets: 600
matched bytes: 1000

accelerator: ratelimiter
source IPv4: 7.7.7.7
destination IPv4: 80.72.48.243
destination port: 22
matched packets: 500
matched bytes: 1000
"""
        )
        text = "icmp rate limit: 15\nrate limit echo request: 10"
        result = app.analyze_connection_performance(runtime, config, hardware_analysis, ["1.8", "1.1", "0.8"], text)
        self.assertEqual(result["status"], "yellow")
        self.assertGreaterEqual(result["score"], 35)
        self.assertLess(result["score"], 70)

    def test_connection_performance_auffaellig(self):
        runtime = [
            app.RatelimiterRuntimeEntry("WAN", "rule", packets=1000, interval_seconds=1, hits=5000, blocked=1500)
        ]
        config = [app.RatelimiterConfigEntry("rule", "qos_wan", "ip.proto 6", 1000, "1s", 0, True)]
        hardware_analysis = self._build_hardware_analysis(
            """accelerator: ratelimiter
source IPv4: 1.2.3.4
destination IPv4: 80.72.48.243
destination port: 80
matched packets: 180000
matched bytes: 999999

accelerator: ratelimiter
source IPv4: 5.6.7.8
destination IPv4: 80.72.48.243
destination port: 23
matched packets: 60000
matched bytes: 999999
"""
        )
        text = "frag: freemem 12\nreject not possible: 5\ntcp checksum wrong: 120"
        result = app.analyze_connection_performance(runtime, config, hardware_analysis, ["4.2", "3.1", "2.8"], text)
        self.assertEqual(result["status"], "red")
        self.assertGreaterEqual(result["score"], 70)

    def test_connection_performance_management_schutz_nicht_automatisch_kritisch(self):
        runtime = [
            app.RatelimiterRuntimeEntry("WAN", "rule", packets=1000, interval_seconds=1, hits=40, blocked=0)
        ]
        config = [app.RatelimiterConfigEntry("rule", "qos_wan", "ip.proto 6", 1000, "1s", 0, True)]
        hardware_analysis = self._build_hardware_analysis(
            """accelerator: ratelimiter
source IPv4: 80.66.224.98
destination IPv4: 80.72.48.243
destination port: 499
matched packets: 300
matched bytes: 5000

accelerator: ratelimiter
source IPv4: 80.66.224.99
destination IPv4: 80.72.48.243
destination port: 443
matched packets: 200
matched bytes: 5000
"""
        )
        result = app.analyze_connection_performance(runtime, config, hardware_analysis, ["0.2", "0.2", "0.1"], "")
        self.assertEqual(result["status"], "green")
        self.assertIn("normaler Schutzmechanismus", result["summary"])


if __name__ == "__main__":
    unittest.main()

class PpeDiagnosisParserTests(unittest.TestCase):
    SAMPLE_FULL_PPE = """
qca_nss_ppe_qdisc      90112  0
qca_nss_ppe_pppoe_mgr    16384  1 offload_pa
qca_nss_ppe_vlan       45056  3 offload_pa,qca_nss_ppe_bridge_mgr,qca_nss_ppe_lag
qca_nss_dp            139264  2 qca_nss_ppe_ds,qca_nss_ppe_vp
qca_nss_ppe           385024  12 offload_pa,qca_nss_ppe_vlan
qca_ssdk             1871872  4 offload_pa,qca_nss_dp,qca_nss_ppe
offload_pa            548864  0
offload_util           16384  1 offload_pa
##### BEGIN SECTION brief
HWPA ppe summary:
used hws 5 / 8192
free hws 8187 / 8192

Common PPE offload counter:
  no free hws     : 0
  offload failed  : 0
  flow flushed by hw: 10
  fallback offloads: 0
  dev not registered in ppe: 0
  ppe offload collision: 0
  add/remove vlan dev to ppe err: 0
  add/remove pppoe dev to ppe err: 0
  add/remove mac dev to ppe err: 0

Accelerator state:
  ratelimiter: enabled
  ipv6: enabled
  ipv4: enabled
##### END SECTION brief
##### BEGIN SECTION interfaces
Netdev              type      avm_pid   ppe_ifidx ppe_port  rfs   vp_dev    hwpa_type mht_bmp
lo                  772       0         -1        -1        no    NULL      0         0
wan                 1         10        5         6         no    NULL      1         0
lan                 1         0         7         -1        no    NULL      0         0
ath0                1         14        10        64        yes   NULL      3         0
PPE device only
Name                ppe_port  Type      MTU       Base-Dev  Refs      MAC
wan.v882            6         VLAN      1508      wan       2         aa:bb:cc:dd:ee:ff
wan.v882.p1         6         PPPoE     1500      wan.v882  1         aa:bb:cc:dd:ee:01
##### END SECTION interfaces
##### BEGIN SECTION synced_sessions
HWPA synced sessions
HWS: 3f576a90
accelerator: ratelimiter
source IPv4: 192.0.2.10
destination IPv4: 198.51.100.20
source port: 12345
destination port: 443
##### END SECTION synced_sessions
##### BEGIN SECTION caps
MAX HWPA PPE Sessions: 8192
##### END SECTION caps
##### BEGIN SECTION ppe_if_map (via mknod)
Interface.5.iface_number=5
Interface.5.iface_type=PHYSICAL
Interface.5.netdev_name=wan
Interface.5.port_number=6
Interface.5.l3_if_number=5

Interface.7.iface_number=7
Interface.7.iface_type=BRIDGE
Interface.7.netdev_name=lan
Interface.7.vsi_number=0
Interface.7.l3_if_number=7

Interface.13.iface_number=13
Interface.13.parent_iface_number=5
Interface.13.iface_type=VLAN
Interface.13.netdev_name=wan.v882
Interface.13.port_number=6
Interface.13.l3_if_number=13

Interface.14.iface_number=14
Interface.14.parent_iface_number=13
Interface.14.iface_type=PPPoE
Interface.14.netdev_name=wan.v882.p1
Interface.14.port_number=6
Interface.14.l3_if_number=14
##### END SECTION ppe_if_map (via mknod)
port 6 MTU 0x05e4 MRU 0x05e4
port 6 flow control status Illegal value
portshaper port 6 enabled CIR 0x100 CBS 0x20 frame mode L2
"""

    def test_parse_ppe_full_supportdata_extracts_overview(self):
        data = app.parse_ppe_diagnosis(self.SAMPLE_FULL_PPE)
        self.assertTrue(data["ppe_detected"])
        self.assertTrue(data["hwpa_detected"])
        self.assertEqual(data["summary"]["used_hws"], 5)
        self.assertEqual(data["summary"]["max_hws"], 8192)
        self.assertEqual(data["summary"]["accelerator_state"]["ipv4"], "enabled")
        self.assertEqual(data["counts"]["registered_devices"], 4)
        self.assertEqual(data["sessions"]["by_type"]["ratelimiter"], 1)

    def test_parse_ppe_without_ppe_is_safe(self):
        data = app.parse_ppe_diagnosis("ordinary support data without acceleration")
        self.assertFalse(data["ppe_detected"])
        self.assertEqual(data["assessment"]["overall"], "Hinweis")
        self.assertEqual(data["counts"]["registered_devices"], 0)

    def test_parse_ppe_vlan_pppoe_and_tree(self):
        data = app.parse_ppe_diagnosis(self.SAMPLE_FULL_PPE)
        self.assertEqual(data["counts"]["vlan_devices"], 2)
        self.assertEqual(data["counts"]["pppoe_devices"], 2)
        self.assertIn("wan.v882 hängt auf wan", data["device_chains"])
        self.assertTrue(any("wan.v882.p1" in line for line in data["device_tree"]))

    def test_parse_ppe_error_counters_raise_warning_or_critical(self):
        text = self.SAMPLE_FULL_PPE.replace("offload failed  : 0", "offload failed  : 3").replace("add/remove vlan dev to ppe err: 0", "add/remove vlan dev to ppe err: 1")
        data = app.parse_ppe_diagnosis(text)
        counters = {row["counter"]: row for row in data["counters"]}
        self.assertEqual(counters["offload failed"]["severity"], "warning")
        self.assertEqual(counters["add/remove vlan dev to ppe err"]["severity"], "critical")
        self.assertEqual(data["assessment"]["overall"], "Kritisch")

    def test_parse_hwpa_interface_minus_one_warns_only_for_productive(self):
        section = """Netdev type avm_pid ppe_ifidx ppe_port rfs vp_dev hwpa_type mht_bmp
lo 772 0 -1 -1 no NULL 0 0
wan 1 10 -1 -1 no NULL 1 0
wifi0 801 0 -1 -1 no NULL 0 0
"""
        rows = app.parse_hwpa_interfaces(section)
        severities = {row["netdev"]: row["severity"] for row in rows}
        self.assertEqual(severities["wan"], "warning")
        self.assertEqual(severities["lo"], "neutral")
        self.assertEqual(severities["wifi0"], "neutral")

    def test_parse_portshaper_active_on_wan(self):
        data = app.parse_ppe_diagnosis(self.SAMPLE_FULL_PPE)
        self.assertEqual(data["portshaper"][0]["assessment"], "WAN-Portshaper aktiv")
        self.assertTrue(any("WAN-Port" in finding["message"] for finding in data["assessment"]["findings"]))

    def test_parse_mtu_1508_marks_hint(self):
        rows = app.parse_ppe_mtu_mru("port 6 MTU 0x05e4 MRU 0x05e4")
        self.assertEqual(rows[0]["mtu_decimal"], 1508)
        self.assertIn("RFC4638", rows[0]["assessment"])

    def test_parse_flow_control_illegal_value_is_hint(self):
        rows = app.parse_ppe_flow_control("port 6 flow control status Illegal value")
        self.assertEqual(rows[0]["assessment"], "Hinweis: Illegal value")

class PpeNetworkingCorrelationTests(unittest.TestCase):
    def test_ppe_networking_correlates_pppoe_vlan_with_internet(self):
        text = """##### BEGIN SECTION ppe_if_map
Interface.1.iface_number = 1
Interface.1.netdev_name = wan
Interface.1.iface_type = physical
Interface.1.port_number = 5
Interface.2.iface_number = 2
Interface.2.netdev_name = wan.v882
Interface.2.iface_type = vlan
Interface.2.parent_iface_number = 1
Interface.2.port_number = 5
Interface.3.iface_number = 3
Interface.3.netdev_name = wan.v882.p1
Interface.3.iface_type = pppoe
Interface.3.parent_iface_number = 2
Interface.3.port_number = 5
##### END SECTION ppe_if_map
##### BEGIN SECTION Networking Supportdata networking
Networking
----------
2: wan.v882@wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1492 qdisc noop state UP master wanbr
    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff
    inet 198.51.100.2/32 scope global wan.v882
    default via 198.51.100.1 dev wan.v882
pppoe: wan.v882.p1 uses lower interface wan.v882 for internet
##### END SECTION Networking Supportdata networking
"""
        data = app.parse_ppe_diagnosis(text)
        correlation = {row["interface_name"]: row for row in data["ppeNetworkCorrelation"]}
        self.assertIn("wan.v882", correlation)
        self.assertTrue(correlation["wan.v882"]["ppe_registered"])
        self.assertTrue(correlation["wan.v882"]["networking_found"])
        self.assertEqual(correlation["wan.v882"]["detected_service"], "Internet")
        self.assertEqual(correlation["wan.v882"]["confidence"], "high")
        self.assertIn("PPE: PPPoE-Interface wan.v882.p1 hängt auf wan.v882", correlation["wan.v882"]["evidence"])
        self.assertNotIn("Internet-VLAN erkannt über PPPoE-Interface.", data["network_correlation"]["diagnostics"])

    def test_networking_vlan_without_ppe_stays_unknown_without_service_evidence(self):
        text = """##### BEGIN SECTION Networking Supportdata networking
Networking
----------
7: eth0.7@eth0: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN
##### END SECTION Networking Supportdata networking
"""
        data = app.parse_ppe_diagnosis(text)
        row = data["ppeNetworkCorrelation"][0]
        self.assertEqual(row["interface_name"], "eth0.7")
        self.assertFalse(row["ppe_registered"])
        self.assertTrue(row["networking_found"])
        self.assertEqual(row["detected_service"], "Unknown")
        self.assertEqual(row["confidence"], "unknown")


class WanServiceVlanNetworkingTests(unittest.TestCase):
    NETWORKING_SAMPLE = """##### BEGIN SECTION Networking Supportdata networking
Networking
----------
0: name internet (attached, active internet)
0: iface net_upstream0/wan PPPoE/26/dsl 08:b6:57:92:7f:8b stay online 1 vlan 882 prio 0 (prop: default internet)

1: name voip (attached)
1: iface net_upstream0/wan RBE/18/dsl 08:b6:57:92:7f:8c stay online 1 vlan 884 prio 6

2: name iptv (attached)
2: iface net_upstream0/wan RBE/18/dsl 08:b6:57:92:7f:8d stay online 1 vlan 883 prio 5

3: name tr069 (attached)
3: iface net_upstream0/wan RBE/18/dsl 08:b6:57:92:7f:8e stay online 1 vlan 881 prio 2

wandmng_encap_update(): wand_connection(internet): iface net_upstream0 PPPoE/26 vlan 882 fixed prio 0x8100 prio 0 tos 0x00
wandmng_encap_update(): wand_connection(voip): iface net_upstream0 RBE/18 vlan 884 fixed prio 0x8100 prio 6 tos 0x00
wandmng_encap_update(): wand_connection(iptv): iface net_upstream0 RBE/18 vlan 883 fixed prio 0x8100 prio 5 tos 0x00
wandmng_encap_update(): wand_connection(tr069): iface net_upstream0 RBE/18 vlan 881 fixed prio 0x8100 prio 2 tos 0x00

connections of ata0
0: name internet state attached:
encap PPPoE (26)
vlancfg
encap fixed prio
tagtype 0x8100
id 882
prio 0 (0x00)
ipv4_connstatus connected
mac 08:b6:57:92:7f:8b
pppconfig username/passwd set

1: name voip state attached:
encap RBE (18)
vlancfg
id 884
prio 6 (0x06)
ipv4_connstatus connected

2: name iptv state attached:
encap RBE (18)
vlancfg
id 883
prio 5 (0x05)
ipv4_connstatus connected

3: name tr069 state attached:
encap RBE (18)
vlancfg
id 881
prio 2 (0x02)
ipv4_connstatus connected
tr069_activated yes
##### END SECTION Networking Supportdata networking
"""

    def test_networking_connection_blocks_are_primary_service_vlan_source(self):
        rows = {row["service"]: row for row in app.parse_wan_service_vlans_from_networking(self.NETWORKING_SAMPLE)}

        self.assertEqual(rows["internet"]["vlan_id"], 882)
        self.assertEqual(rows["internet"]["encap"], "PPPoE")
        self.assertEqual(rows["internet"]["vlan_prio"], 0)
        self.assertEqual(rows["internet"]["physical_parent_interface"], "wan")
        self.assertEqual(rows["internet"]["confidence"], "high")

        self.assertEqual(rows["voip"]["vlan_id"], 884)
        self.assertEqual(rows["voip"]["encap"], "RBE")
        self.assertEqual(rows["voip"]["vlan_prio"], 6)
        self.assertEqual(rows["voip"]["physical_parent_interface"], "wan")
        self.assertEqual(rows["voip"]["confidence"], "high")

        self.assertEqual(rows["iptv"]["vlan_id"], 883)
        self.assertEqual(rows["iptv"]["encap"], "RBE")
        self.assertEqual(rows["iptv"]["vlan_prio"], 5)
        self.assertEqual(rows["iptv"]["physical_parent_interface"], "wan")
        self.assertEqual(rows["iptv"]["confidence"], "high")

        self.assertEqual(rows["tr069"]["vlan_id"], 881)
        self.assertEqual(rows["tr069"]["encap"], "RBE")
        self.assertEqual(rows["tr069"]["vlan_prio"], 2)
        self.assertEqual(rows["tr069"]["physical_parent_interface"], "wan")
        self.assertEqual(rows["tr069"]["confidence"], "high")

    def test_wandmng_and_internalview_confirm_same_mapping(self):
        rows = {row["service"]: row for row in app.parse_wan_service_vlans_from_networking(self.NETWORKING_SAMPLE)}
        self.assertEqual(rows["internet"]["tagtype"], "0x8100")
        self.assertEqual(rows["internet"]["tos"], "0x00")
        self.assertEqual(rows["internet"]["ipv4_status"], "connected")
        self.assertTrue(rows["internet"]["ppp_configured"])
        self.assertTrue(rows["tr069"]["tr069_activated"])
        self.assertTrue(any("wandmng_encap_update" in item for item in rows["iptv"]["evidence"]))
        self.assertTrue(any("vlancfg id 883" in item for item in rows["iptv"]["evidence"]))

    def test_partial_ppe_does_not_discard_networking_service_mapping(self):
        text = """##### BEGIN SECTION ppe_if_map
Interface.1.iface_number = 1
Interface.1.netdev_name = wan
Interface.1.iface_type = physical
Interface.2.iface_number = 2
Interface.2.netdev_name = wan.v882
Interface.2.iface_type = VLAN
Interface.2.parent_iface_number = 1
Interface.3.iface_number = 3
Interface.3.netdev_name = wan.v882.p1
Interface.3.iface_type = PPPoE
Interface.3.parent_iface_number = 2
Interface.4.iface_number = 4
Interface.4.netdev_name = wan.v883
Interface.4.iface_type = VLAN
Interface.4.parent_iface_number = 1
##### END SECTION ppe_if_map
""" + self.NETWORKING_SAMPLE
        data = app.parse_ppe_diagnosis(text)
        rows = {row["service"]: row for row in data["wanServiceVlans"]}
        self.assertEqual(rows["voip"]["vlan_id"], 884)
        self.assertEqual(rows["tr069"]["vlan_id"], 881)
        self.assertEqual(rows["voip"]["confidence"], "high")
        ppe_rows = {row["service"]: row for row in data["serviceVlanPpeCorrelation"]}
        self.assertTrue(ppe_rows["internet"]["ppe_registered"])
        self.assertTrue(ppe_rows["internet"]["pppoe_ppe_device_found"])
        self.assertTrue(ppe_rows["iptv"]["ppe_registered"])
        self.assertFalse(ppe_rows["voip"]["ppe_registered"])
        self.assertFalse(ppe_rows["tr069"]["ppe_registered"])

class SecurityHelperTests(unittest.TestCase):
    def test_escape_html_escapes_support_data_values(self):
        value = '<img src=x onerror="alert(1)"> & device'
        self.assertEqual(app.escape_html(value), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; device')

    def test_decode_support_data_upload_accepts_txt_and_ignores_invalid_utf8(self):
        decoded = app.decode_support_data_upload('support.txt', b'hello\xffworld')
        self.assertEqual(decoded, 'helloworld')

    def test_decode_support_data_upload_rejects_unexpected_extension(self):
        with self.assertRaises(ValueError):
            app.decode_support_data_upload('support.html', b'<html></html>')

    def test_decode_support_data_upload_rejects_oversized_content(self):
        with self.assertRaises(ValueError):
            app.decode_support_data_upload('support.txt', b'x' * (app.MAX_UPLOAD_SIZE_BYTES + 1))

    def test_parse_support_data_tolerates_missing_sections(self):
        parsed = app.parse_support_data('synthetic minimal support data')
        self.assertEqual(parsed['networks'], [])
        self.assertEqual(parsed['ports'], [])
        self.assertIsNone(parsed['internet_connection'])
        self.assertEqual(parsed['docsis_data'], {})
