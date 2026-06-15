import unittest

import app


UI_CONNECTIONS_PPPOE = """##### BEGIN SECTION UI connections synthetic
opmode = opmode_pppoe
connection0/
name = backup
is_active_internet_connection = 0
use_dhcp = 1
dslencap = dslencap_ether
connection1/
name = internet
is_active_internet_connection = 1
use_dhcp = 0
dslencap = dslencap_pppoe
vlanencap = vlanencap_fixed_prio
vlanid = 7
vlanprio = 3
ip4_addr = 192.0.2.10
ip4_first_dns = 198.51.100.53
ip4_second_dns = 0.0.0.0
ip4_masqaddr = 192.0.2.1
ip6_addr = 2001:db8::10
ip6_first_dns = 2001:db8::53
ip6_second_dns = ::
ip6_prefix = 2001:db8:1::/56
##### END SECTION UI connections synthetic
"""

UI_CONNECTIONS_DHCP_RBE = """##### BEGIN SECTION UI connections synthetic
opmode = opmode_eth_ipclient
connection0/
name = dhcp-wan
is_active_internet_connection = 1
use_dhcp = 1
dslencap = dslencap_ether
vlanencap = vlanencap_none
vlanid = 0
vlanprio = 0
ip4_addr = 198.51.100.10
ip4_first_dns = 198.51.100.54
ip4_second_dns = 198.51.100.55
ip4_masqaddr = 198.51.100.1
ip6_addr = 2001:db8:2::10
ip6_first_dns = 2001:db8:2::53
ip6_second_dns = 2001:db8:2::54
ip6_prefix = 2001:db8:2::/56
##### END SECTION UI connections synthetic
"""

PORT_FORWARDINGS = """##### BEGIN SECTION port_forwards IPv4 forwardings synthetic
--- Active IPv4 Portforwardings ---
web TCP 192.0.2.20 8443 203.0.113.10 443 "HTTPS test service"
allow-only-from 198.51.100.0/24
vpn UDP 192.0.2.30 1194 203.0.113.10 1194 "Synthetic VPN"
##### END SECTION port_forwards IPv4 forwardings synthetic
"""

AR7_CFG = """##### BEGIN SECTION ar7_cfg /var/flash/ar7.cfg synthetic
ar7cfg {
        mode = dsldmode_router;
        active_provider = "synthetic-provider";
        ipv4mode = ipv4_normal;
        ipv6mode = ipv6_native;
        mtu_cutback = 1500;
        hsi_use_wan_vlan = yes;
        tr069_forwardrules = "tcp 0.0.0.0:8089 192.0.2.1:8089";
        snmp_on_wan = no;
        expertmode = yes;
        ipv6_hidden = no;
        ipv4_hidden = no;
        ds_lite_hidden = yes;
        dns1 = 198.51.100.53;
        dns2 = 198.51.100.54;
        brinterfaces {
                name = "lan";
                ipaddr = 192.0.2.1;
                netmask = 255.255.255.0;
                dhcpstart = 192.0.2.100;
                dhcpend = 192.0.2.150;
        }
        brinterfaces {
                name = "guest";
                ipaddr = 198.51.100.1;
                netmask = 255.255.255.0;
                dhcpstart = 198.51.100.20;
                dhcpend = 198.51.100.40;
        }
        vccs {
                vcc {
                        vpi = 1;
                        vci = 32;
                        dsl_encap = dslencap_pppoe;
                }
                vcc {
                        vpi = 1;
                        vci = 33;
                        dsl_encap = dslencap_ether;
                }
        }
        vlancfg {
                vlan {
                        vlanid = 7;
                        vlanprio = 3;
                        tos = 0x00;
                }
        }
        dslifaces {
                name = "internet";
                enabled = yes;
                dsl_encap = dslencap_pppoe;
                dslinterfacename = "dsl";
                stackmode = ipv4_ipv6;
                weight = 50;
                vlancfg {
                        vlanencap = vlanencap_fixed_prio;
                        vlanid = 7;
                        vlanprio = 3;
                }
        }
        ddns {
                enabled = yes;
        }
        emailnotify {
                enabled = no;
        }
        telcfg {
        }
}
##### END SECTION ar7_cfg /var/flash/ar7.cfg synthetic
"""

NETWORKING_SAMPLE = """##### BEGIN SECTION Networking Supportdata networking
Networking
----------
0: name internet (attached, active internet)
0: iface net_upstream0/wan PPPoE/26/dsl 00:00:5e:00:53:01 stay online 1 vlan 7 prio 3 (prop: default internet)
1: name guest (attached)
1: iface guest/lan RBE/18/ether 00:00:5e:00:53:02 stay online 1 vlan 70 prio 0
2: name tr069 (attached)
2: iface net_upstream0/wan RBE/18/dsl 00:00:5e:00:53:03 stay online 1 vlan 8 prio 1

connections of ata0
0: name internet state attached:
encap PPPoE (26)
vlancfg
id 7
prio 3 (0x03)
ipv4_connstatus connected
ipv6_connstatus connected
pppconfig username/passwd set

2: name tr069 state attached:
encap RBE (18)
vlancfg
id 8
prio 1 (0x01)
ipv4_connstatus connected
tr069_activated yes
##### END SECTION Networking Supportdata networking
"""


class InternetAr7PortForwardingSyntheticTests(unittest.TestCase):
    def test_parse_internet_connection_active_pppoe_with_ipv4_ipv6_dns_masquerading_and_vlan(self):
        connection = app.parse_internet_connection(UI_CONNECTIONS_PPPOE)

        self.assertIsNotNone(connection)
        self.assertEqual(connection.name, "internet")
        self.assertEqual(connection.access_type, "PPPoE")
        self.assertEqual(connection.vlan, "7 (Prio 3)")
        self.assertEqual(connection.ipv4_address, "192.0.2.10")
        self.assertEqual(connection.ipv4_dns, ["198.51.100.53"])
        self.assertEqual(connection.ipv4_masq, "192.0.2.1")
        self.assertEqual(connection.ipv6_address, "2001:db8::10")
        self.assertEqual(connection.ipv6_dns, ["2001:db8::53"])
        self.assertEqual(connection.ipv6_masq, "2001:db8:1::/56")

    def test_parse_internet_connection_dhcp_rbe_without_vlan_and_complete_dns_lists(self):
        connection = app.parse_internet_connection(UI_CONNECTIONS_DHCP_RBE)

        self.assertIsNotNone(connection)
        self.assertEqual(connection.access_type, "DHCP (RBE)")
        self.assertIsNone(connection.vlan)
        self.assertEqual(connection.ipv4_dns, ["198.51.100.54", "198.51.100.55"])
        self.assertEqual(connection.ipv6_dns, ["2001:db8:2::53", "2001:db8:2::54"])

    def test_parse_internet_connection_missing_and_inactive_sections_are_safe(self):
        self.assertIsNone(app.parse_internet_connection("synthetic support data without UI connections"))
        inactive = UI_CONNECTIONS_DHCP_RBE.replace("is_active_internet_connection = 1", "is_active_internet_connection = 0")
        self.assertIsNone(app.parse_internet_connection(inactive))

    def test_parse_port_forwardings_tcp_udp_description_target_public_and_source_restriction(self):
        entries = app.parse_port_forwardings(PORT_FORWARDINGS)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].protocol, "TCP")
        self.assertEqual(entries[0].target_ip, "192.0.2.20")
        self.assertEqual(entries[0].target_port, "8443")
        self.assertEqual(entries[0].public_ip, "203.0.113.10")
        self.assertEqual(entries[0].public_port, "443")
        self.assertEqual(entries[0].description, "HTTPS test service")
        self.assertEqual(entries[0].allow_only_from, "198.51.100.0/24")
        self.assertEqual(entries[1].protocol, "UDP")
        self.assertEqual(entries[1].description, "Synthetic VPN")
        self.assertIsNone(entries[1].allow_only_from)

    def test_parse_port_forwardings_empty_missing_and_incomplete_sections_are_safe(self):
        self.assertEqual(app.parse_port_forwardings("synthetic support data without forwards"), [])
        incomplete = """##### BEGIN SECTION port_forwards IPv4 forwardings synthetic
--- Active IPv4 Portforwardings ---
this line is intentionally incomplete
allow-only-from 198.51.100.10
##### END SECTION port_forwards IPv4 forwardings synthetic
"""
        self.assertEqual(app.parse_port_forwardings(incomplete), [])

    def test_parse_ar7_overview_extracts_bridge_vcc_vlan_and_dsl_iface_details(self):
        overview = app.parse_ar7_overview(AR7_CFG)

        self.assertEqual(overview.mode, "dsldmode_router")
        self.assertEqual(overview.active_provider, "synthetic-provider")
        self.assertEqual([bridge.name for bridge in overview.bridge_interfaces], ["lan", "guest"])
        self.assertEqual(overview.bridge_interfaces[0].dhcp_start, "192.0.2.100")
        self.assertEqual([vcc.dsl_encap for vcc in overview.vccs], ["PPPoE", "DHCP"])
        self.assertEqual(overview.vlans[0].vlanid, "7")
        self.assertEqual(overview.vlans[0].vlanprio, "3")
        self.assertEqual(overview.dsl_ifaces[0].name, "internet")
        self.assertEqual(overview.dsl_ifaces[0].dsl_encap, "PPPoE")
        self.assertEqual(overview.dsl_ifaces[0].vlan_id, "7")
        self.assertEqual(overview.dsl_ifaces[0].vlan_prio, "3")

    def test_parse_ar7_network_settings_extracts_wan_lan_guest_service_flags_and_dns(self):
        settings = app.parse_ar7_network_settings(AR7_CFG)

        self.assertEqual(settings.mode, "dsldmode_router")
        self.assertEqual(settings.ipv4_mode, "ipv4_normal")
        self.assertEqual(settings.ipv6_mode, "ipv6_native")
        self.assertEqual(settings.mtu, "1500")
        self.assertEqual(settings.wan_vlan, "yes")
        self.assertEqual(settings.tr069, "yes")
        self.assertEqual(settings.snmp_wan, "no")
        self.assertEqual(settings.dyn_dns, "yes")
        self.assertEqual(settings.email_reports, "no")
        self.assertEqual(settings.expert_mode, "yes")
        self.assertEqual(settings.dns_servers, ["198.51.100.53", "198.51.100.54"])
        self.assertIn("IPv6", settings.hidden_menus)
        self.assertEqual(settings.interfaces["lan"].ipaddr, "192.0.2.1")
        self.assertEqual(settings.interfaces["guest"].dhcp_end, "198.51.100.40")

    def test_ar7_parsers_missing_empty_or_incomplete_sections_return_empty_models(self):
        overview = app.parse_ar7_overview("synthetic support data without ar7 cfg")
        settings = app.parse_ar7_network_settings("##### BEGIN SECTION ar7_cfg /var/flash/ar7.cfg synthetic\nar7cfg {\n")

        self.assertIsNone(overview.mode)
        self.assertEqual(overview.bridge_interfaces, [])
        self.assertIsNone(settings.mode)
        self.assertEqual(settings.interfaces, {})
        self.assertEqual(settings.dns_servers, [])

    def test_networking_wan_lan_guest_and_service_vlan_parser_extracts_access_types_and_priorities(self):
        rows = {row["service"]: row for row in app.parse_wan_service_vlans_from_networking(NETWORKING_SAMPLE)}

        self.assertEqual(rows["internet"]["encap"], "PPPoE")
        self.assertEqual(rows["internet"]["vlan_id"], 7)
        self.assertEqual(rows["internet"]["vlan_prio"], 3)
        self.assertEqual(rows["internet"]["physical_parent_interface"], "wan")
        self.assertEqual(rows["internet"]["ipv4_status"], "connected")
        self.assertEqual(rows["internet"]["ipv6_status"], "connected")
        self.assertTrue(rows["internet"]["ppp_configured"])
        self.assertEqual(rows["guest"]["encap"], "RBE")
        self.assertEqual(rows["guest"]["physical_parent_interface"], "lan")
        self.assertEqual(rows["tr069"]["vlan_id"], 8)
        self.assertEqual(rows["tr069"]["vlan_prio"], 1)
        self.assertTrue(rows["tr069"]["tr069_activated"])

    def test_parse_support_data_exposes_internet_ar7_portforwarding_and_networking_results(self):
        parsed = app.parse_support_data(UI_CONNECTIONS_PPPOE + PORT_FORWARDINGS + AR7_CFG + NETWORKING_SAMPLE)

        self.assertEqual(parsed["internet_connection"].access_type, "PPPoE")
        self.assertEqual(len(parsed["port_forwardings"]), 2)
        self.assertEqual(parsed["ar7_network_settings"].interfaces["lan"].ipaddr, "192.0.2.1")
        self.assertEqual(parsed["ar7_overview"].dsl_ifaces[0].vlan_id, "7")
        wan_rows = {row["service"]: row for row in parsed["ppe_diagnosis"]["wanServiceVlans"]}
        self.assertEqual(wan_rows["internet"]["vlan_id"], 7)


if __name__ == "__main__":
    unittest.main()
