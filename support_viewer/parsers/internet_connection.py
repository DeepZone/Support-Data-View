import re
from typing import List, Optional

from support_viewer.models import InternetConnection
from support_viewer.utils import extract_section_by_prefix, extract_value


def _parse_internet_connections(section: str) -> List[str]:
    connections = []
    connection_pattern = re.compile(
        r"connection\d+/\n(?P<body>.*?)(?=\nconnection\d+/|##### END SECTION|$)",
        re.DOTALL,
    )
    for match in connection_pattern.finditer(section):
        connections.append(match.group("body"))
    return connections


def _normalize_dns(values: List[Optional[str]]) -> List[str]:
    cleaned = []
    for value in values:
        if not value:
            continue
        if value in {"0.0.0.0", "::", "0"}:
            continue
        cleaned.append(value)
    return cleaned


def parse_internet_connection(text: str) -> Optional[InternetConnection]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION UI connections")
    if not section:
        return None
    opmode = extract_value(section, "opmode")
    active_block = None
    for block in _parse_internet_connections(section):
        if extract_value(block, "is_active_internet_connection") == "1":
            active_block = block
            break
    if not active_block:
        return None

    name = extract_value(active_block, "name") or "internet"
    use_dhcp = extract_value(active_block, "use_dhcp") == "1"
    dslencap = extract_value(active_block, "dslencap") or ""
    access_type = "Unbekannt"
    if "pppoe" in dslencap.lower() or (opmode and "pppoe" in opmode.lower()):
        access_type = "PPPoE"
    elif use_dhcp:
        access_type = "DHCP (RBE)"

    vlanencap = extract_value(active_block, "vlanencap")
    vlanid = extract_value(active_block, "vlanid")
    vlanprio = extract_value(active_block, "vlanprio")
    vlan = None
    if vlanencap and vlanencap != "vlanencap_none" and vlanid and vlanid != "0":
        vlan_prio_label = f" (Prio {vlanprio})" if vlanprio and vlanprio != "0" else ""
        vlan = f"{vlanid}{vlan_prio_label}"

    ipv4_dns = _normalize_dns(
        [extract_value(active_block, "ip4_first_dns"), extract_value(active_block, "ip4_second_dns")]
    )
    ipv6_dns = _normalize_dns(
        [extract_value(active_block, "ip6_first_dns"), extract_value(active_block, "ip6_second_dns")]
    )

    return InternetConnection(
        name=name,
        access_type=access_type,
        vlan=vlan,
        ipv4_address=extract_value(active_block, "ip4_addr"),
        ipv4_dns=ipv4_dns,
        ipv4_masq=extract_value(active_block, "ip4_masqaddr"),
        ipv6_address=extract_value(active_block, "ip6_addr"),
        ipv6_dns=ipv6_dns,
        ipv6_masq=extract_value(active_block, "ip6_prefix"),
    )
