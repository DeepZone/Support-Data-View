import re
from typing import Dict, List, Optional

from support_viewer.models import (
    Ar7BridgeInterface,
    Ar7Interface,
    Ar7DslIface,
    Ar7NetworkSettings,
    Ar7Overview,
    Ar7VccEntry,
    Ar7VlanEntry,
)
from support_viewer.parsers.ar7_helpers import (
    extract_ar7_named_blocks as _extract_named_blocks,
    extract_ar7cfg_body as _extract_ar7cfg_body,
    find_ar7_block_value as _find_block_value,
)


def _dsl_encap_label(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    normalized = raw_value.strip().lower()
    mapping = {
        "dslencap_ether": "DHCP",
        "dslencap_pppoe": "PPPoE",
    }
    return mapping.get(normalized, raw_value)


def parse_ar7_overview(text: str) -> Ar7Overview:
    ar7cfg_body = _extract_ar7cfg_body(text)
    if not ar7cfg_body:
        return Ar7Overview(
            mode=None,
            active_provider=None,
            bridge_interfaces=[],
            vccs=[],
            vlans=[],
            dsl_ifaces=[],
        )

    bridge_interfaces = []
    for block in _extract_named_blocks(ar7cfg_body, "brinterfaces"):
        bridge_interfaces.append(
            Ar7BridgeInterface(
                name=_find_block_value(block, "name"),
                ipaddr=_find_block_value(block, "ipaddr"),
                netmask=_find_block_value(block, "netmask"),
                dhcp_start=_find_block_value(block, "dhcpstart"),
                dhcp_end=_find_block_value(block, "dhcpend"),
            )
        )

    vccs = []
    vccs_blocks = _extract_named_blocks(ar7cfg_body, "vccs")
    vccs_body = vccs_blocks[0] if vccs_blocks else ""
    for block in _extract_named_blocks(vccs_body, "vcc"):
        vccs.append(
            Ar7VccEntry(
                vpi=_find_block_value(block, "vpi"),
                vci=_find_block_value(block, "vci"),
                dsl_encap=_dsl_encap_label(_find_block_value(block, "dsl_encap")),
            )
        )

    vlans = []
    vlancfg_blocks = _extract_named_blocks(ar7cfg_body, "vlancfg")
    vlancfg_body = vlancfg_blocks[0] if vlancfg_blocks else ""
    vlan_blocks = _extract_named_blocks(vlancfg_body, "vlan")
    if not vlan_blocks and vlancfg_body:
        vlan_blocks = [vlancfg_body]
    for block in vlan_blocks:
        vlanid = _find_block_value(block, "vlanid")
        vlanprio = _find_block_value(block, "vlanprio")
        tos = _find_block_value(block, "tos")
        if any([vlanid, vlanprio, tos]):
            vlans.append(
                Ar7VlanEntry(
                    vlanid=vlanid,
                    vlanprio=vlanprio,
                    tos=tos,
                )
            )

    dsl_ifaces = []
    for block in _extract_named_blocks(ar7cfg_body, "dslifaces"):
        vlan_blocks = _extract_named_blocks(block, "vlancfg")
        vlan_block = vlan_blocks[0] if vlan_blocks else ""
        dsl_ifaces.append(
            Ar7DslIface(
                name=_find_block_value(block, "name"),
                enabled=_find_block_value(block, "enabled"),
                dsl_encap=_dsl_encap_label(_find_block_value(block, "dsl_encap")),
                dsl_interface_name=_find_block_value(block, "dslinterfacename"),
                stackmode=_find_block_value(block, "stackmode"),
                weight=_find_block_value(block, "weight"),
                vlan_encap=_find_block_value(vlan_block, "vlanencap"),
                vlan_id=_find_block_value(vlan_block, "vlanid"),
                vlan_prio=_find_block_value(vlan_block, "vlanprio"),
            )
        )

    return Ar7Overview(
        mode=_find_block_value(ar7cfg_body, "mode"),
        active_provider=_find_block_value(ar7cfg_body, "active_provider"),
        bridge_interfaces=bridge_interfaces,
        vccs=vccs,
        vlans=vlans,
        dsl_ifaces=dsl_ifaces,
    )


def _extract_hidden_menus(ar7cfg_body: str) -> List[str]:
    hidden_fields = {
        "ipv6_hidden": "IPv6",
        "ipv4_hidden": "IPv4",
        "ds_lite_hidden": "DS-Lite",
        "ipv6_native_hidden": "IPv6 Native",
    }
    visible = []
    for field, label in hidden_fields.items():
        value = _find_block_value(ar7cfg_body, field)
        if value and value.lower() == "no":
            visible.append(label)
    return visible


def parse_ar7_network_settings(text: str) -> Ar7NetworkSettings:
    ar7cfg_body = _extract_ar7cfg_body(text)
    if not ar7cfg_body:
        return Ar7NetworkSettings(
            mode=None,
            ipv4_mode=None,
            ipv6_mode=None,
            mtu=None,
            wan_vlan=None,
            tr069=None,
            snmp_wan=None,
            dyn_dns=None,
            email_reports=None,
            expert_mode=None,
            hidden_menus=[],
            dns_servers=[],
            interfaces={},
        )

    dns_servers = []
    dns1 = _find_block_value(ar7cfg_body, "dns1")
    dns2 = _find_block_value(ar7cfg_body, "dns2")
    for candidate in (dns1, dns2):
        if candidate and candidate != "0.0.0.0" and candidate not in dns_servers:
            dns_servers.append(candidate)

    interfaces: Dict[str, Ar7Interface] = {}
    for match in re.finditer(r"(?:brinterfaces\s*)?\{(.*?)\}", ar7cfg_body, re.DOTALL):
        block = match.group(1)
        name = _find_block_value(block, "name")
        ipaddr = _find_block_value(block, "ipaddr")
        netmask = _find_block_value(block, "netmask")
        if not name or not ipaddr or ipaddr == "0.0.0.0":
            continue
        interfaces[name] = Ar7Interface(
            name=name,
            ipaddr=ipaddr,
            netmask=netmask,
            dhcp_start=_find_block_value(block, "dhcpstart"),
            dhcp_end=_find_block_value(block, "dhcpend"),
        )

    ddns_block_match = re.search(r"ddns\s*\{(.*?)\n\s*\}\s*emailnotify", ar7cfg_body, re.DOTALL)
    ddns_block = ddns_block_match.group(1) if ddns_block_match else ""
    email_block_match = re.search(r"emailnotify\s*\{(.*?)\n\s*\}\s*telcfg", ar7cfg_body, re.DOTALL)
    email_block = email_block_match.group(1) if email_block_match else ""

    return Ar7NetworkSettings(
        mode=_find_block_value(ar7cfg_body, "mode"),
        ipv4_mode=_find_block_value(ar7cfg_body, "ipv4mode"),
        ipv6_mode=_find_block_value(ar7cfg_body, "ipv6mode"),
        mtu=_find_block_value(ar7cfg_body, "mtu_cutback"),
        wan_vlan=_find_block_value(ar7cfg_body, "hsi_use_wan_vlan"),
        tr069="yes" if bool(_find_block_value(ar7cfg_body, "tr069_forwardrules")) else "no",
        snmp_wan=_find_block_value(ar7cfg_body, "snmp_on_wan"),
        dyn_dns=_find_block_value(ddns_block, "enabled") if ddns_block else None,
        email_reports=_find_block_value(email_block, "enabled") if email_block else None,
        expert_mode=_find_block_value(ar7cfg_body, "expertmode"),
        hidden_menus=_extract_hidden_menus(ar7cfg_body),
        dns_servers=dns_servers,
        interfaces=interfaces,
    )
