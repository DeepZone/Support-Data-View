from typing import Optional

from support_viewer.models import (
    Ar7BridgeInterface,
    Ar7DslIface,
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
