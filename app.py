import base64
import ipaddress
import json
import re
import sys
import textwrap
import zlib
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx

from support_viewer.parsers.dect import (
    DEFAULT_DECT_RSSI_INDEX_TO_DBM,
    extract_dect_rssi_index_to_dbm,
    parse_dect_basis_info,
    parse_dect_device_info,
)
from support_viewer.parsers.events import parse_events
from support_viewer.parsers.port_forwarding import parse_port_forwardings
from support_viewer.parsers.telephony import parse_voip_accounts
from support_viewer.models import (
    Ar7BridgeInterface,
    Ar7DslIface,
    Ar7Interface,
    Ar7NetworkSettings,
    Ar7Overview,
    Ar7VccEntry,
    Ar7VlanEntry,
    AvmCounterSection,
    AvmCounterValueEntry,
    ConnectionPerformanceFinding,
    DectBasisInfo,
    DectDevice,
    EventEntry,
    HardwareRatelimiterSession,
    InternetConnection,
    LanPort,
    MeshTopology,
    NeighbourClient,
    PortForwarding,
    RatelimiterConfigEntry,
    RatelimiterRuntimeEntry,
    TelephonyAccount,
    WifiNetwork,
    WifiNoiseFloorEntry,
    WifiRadioLoad,
    WifiStation,
)
from support_viewer.utils import (
    _parse_frequency_range,
    escape_html,
    extract_float_value,
    extract_int_value,
    extract_kbits_rate,
    extract_numeric_array,
    extract_numeric_array_loose,
    extract_section,
    extract_section_between,
    extract_section_block,
    extract_section_by_prefix,
    parse_channel_float,
    parse_int,
    parse_optional_float,
)


ALLOWED_UPLOAD_SUFFIXES = {".txt"}
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024


RADIO_BAND_LABELS = {
    101: "2,4 GHz",
    102: "5 GHz",
    111: "5 GHz",
    121: "6 GHz",
}




def is_allowed_support_data_filename(filename: str) -> bool:
    return any(filename.lower().endswith(suffix) for suffix in ALLOWED_UPLOAD_SUFFIXES)


def decode_support_data_upload(filename: str, content: bytes) -> str:
    if not is_allowed_support_data_filename(filename):
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_SUFFIXES))
        raise ValueError(f"Nicht unterstützter Dateityp. Erlaubt: {allowed}")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        max_mib = MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        raise ValueError(f"Datei ist zu groß. Maximum: {max_mib} MiB")
    return content.decode("utf-8", errors="ignore")


def format_radio_label(radio_id: int) -> str:
    band = RADIO_BAND_LABELS.get(radio_id)
    if band:
        return f"Radio {radio_id} ({band})"
    return f"Radio {radio_id}"



def parse_wlan_env_scan(text: str) -> List[WifiNetwork]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION ENV_SCAN WLAN environment scan results")
    networks = []
    for line in section.splitlines():
        if "ssid" not in line:
            continue
        ssid_match = re.search(r'ssid\s*=\s*"([^"]+)"', line)
        rssi_match = re.search(r"rssi\s*=\s*(-?\d+)", line)
        band_match = re.search(r"radioband\s*=\s*(\d+)", line)
        freq_match = re.search(r"frequency\s*=\s*(\d+)", line)
        if ssid_match and rssi_match:
            networks.append(
                WifiNetwork(
                    ssid=ssid_match.group(1),
                    rssi=int(rssi_match.group(1)),
                    radioband=int(band_match.group(1)) if band_match else None,
                    frequency=int(freq_match.group(1)) if freq_match else None,
                )
            )
    return networks


def parse_avm_counter_rrd_sections(text: str) -> List[AvmCounterSection]:
    markers = [
        ("rrdtoolapi names", "##### BEGIN SECTION AVM Counter rrdtoolapi names"),
        ("rrdtoolapi values", "##### BEGIN SECTION AVM Counter rrdtoolapi values"),
        ("showrrdstate", "##### BEGIN SECTION AVM Counter showrrdstate"),
    ]
    sections: List[AvmCounterSection] = []
    for title, marker in markers:
        section = extract_section_by_prefix(text, marker)
        if not section:
            continue
        lines = section.splitlines()
        if lines and lines[0].startswith("##### BEGIN SECTION"):
            lines = lines[1:]
        content = "\n".join(lines).strip()
        sections.append(AvmCounterSection(title=title, content=content))
    return sections


def parse_avm_counter_values(content: str) -> List[AvmCounterValueEntry]:
    entries: List[AvmCounterValueEntry] = []
    current_category: Optional[str] = None
    category_pattern = re.compile(r"^(?P<category>[a-zA-Z0-9_\-]+):\s*$")
    value_pattern = re.compile(
        r"^(?:(?P<direction><<<|>>>)\s+)?(?P<metric>[a-zA-Z0-9_\-]+)\s+"
        r"(?P<value>-?\d+)\s+(?P<value_type>[CV])\s+\(age\s+(?P<age>\d+)s\)"
    )

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        category_match = category_pattern.match(stripped)
        if category_match:
            current_category = category_match.group("category")
            continue
        if not current_category:
            continue
        value_match = value_pattern.match(stripped)
        if not value_match:
            continue
        entries.append(
            AvmCounterValueEntry(
                category=current_category,
                metric=value_match.group("metric"),
                direction=value_match.group("direction") or "",
                value=int(value_match.group("value")),
                value_type=value_match.group("value_type"),
                age_seconds=int(value_match.group("age")),
            )
        )
    return entries


def summarize_avm_counter_values(sections: List[AvmCounterSection]) -> dict:
    values_section = next((section for section in sections if section.title == "rrdtoolapi values"), None)
    if not values_section:
        return {
            "entries": [],
            "total_entries": 0,
            "stale_entries": 0,
            "total_rx": 0,
            "total_tx": 0,
            "total_traffic": 0,
            "rx_share_pct": None,
            "tx_share_pct": None,
            "top_categories": [],
        }

    entries = parse_avm_counter_values(values_section.content)
    stale_entries = sum(1 for entry in entries if entry.age_seconds > 300)
    total_rx = sum(entry.value for entry in entries if entry.direction == "<<<")
    total_tx = sum(entry.value for entry in entries if entry.direction == ">>>")

    category_rows = []
    for category in sorted({entry.category for entry in entries}):
        category_entries = [entry for entry in entries if entry.category == category]
        category_rows.append(
            {
                "Kategorie": category,
                "Messwerte": len(category_entries),
                "RX gesamt": sum(item.value for item in category_entries if item.direction == "<<<"),
                "TX gesamt": sum(item.value for item in category_entries if item.direction == ">>>"),
                "Werte (V)": sum(1 for item in category_entries if item.value_type == "V"),
                "Counter (C)": sum(1 for item in category_entries if item.value_type == "C"),
                "Max. Alter (s)": max((item.age_seconds for item in category_entries), default=0),
            }
        )
    top_categories = sorted(category_rows, key=lambda row: row["RX gesamt"] + row["TX gesamt"], reverse=True)
    total_traffic = total_rx + total_tx
    rx_share_pct = (total_rx / total_traffic * 100) if total_traffic > 0 else None
    tx_share_pct = (total_tx / total_traffic * 100) if total_traffic > 0 else None

    return {
        "entries": entries,
        "total_entries": len(entries),
        "stale_entries": stale_entries,
        "total_rx": total_rx,
        "total_tx": total_tx,
        "total_traffic": total_traffic,
        "rx_share_pct": rx_share_pct,
        "tx_share_pct": tx_share_pct,
        "top_categories": top_categories,
    }


def parse_wlan_stations(text: str) -> List[WifiStation]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION STATION_LIST WLAN client list")
    stations = []
    for block in section.split("----------------------------------------"):
        mac = extract_value(block, "mac")
        if not mac:
            continue
        stations.append(
            WifiStation(
                mac=mac,
                if_name=extract_value(block, "if_name") or "",
                connect_state=int(extract_value(block, "connect_state") or 0),
                rate_rx=int(extract_value(block, "rate_rx") or 0),
                rate_tx=int(extract_value(block, "rate_tx") or 0),
                rate_rx_max=int(extract_value(block, "rate_rx_max") or 0),
                rate_tx_max=int(extract_value(block, "rate_tx_max") or 0),
                rssi=int(extract_value(block, "rssi") or 0),
                quality=int(extract_value(block, "quality") or 0),
            )
        )
    return stations


def parse_wlan_radio_load(text: str) -> List[WifiRadioLoad]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION WLAN_SCAN_RADIO_LOAD WLAN radio load")
    if not section:
        return []

    radio_pattern = re.compile(
        r"Radio:\s*(?P<radio>\d+)\s*\[offset=(?P<offset>-?\d+),interval=(?P<interval>\d+),count=(?P<count>\d+)\]\s*"
        r"<<<<<BASE64:BEGIN>>>>>(?P<data>.*?)<<<<<BASE64:END>>>>>",
        re.DOTALL,
    )
    radio_loads = []
    for match in radio_pattern.finditer(section):
        radio_id = int(match.group("radio"))
        offset = int(match.group("offset"))
        interval = int(match.group("interval"))
        count = int(match.group("count"))
        encoded = re.sub(r"\s+", "", match.group("data"))
        try:
            raw = base64.b64decode(encoded)
            decoded = zlib.decompress(raw).decode("utf-8", errors="ignore")
        except (ValueError, zlib.error) as exc:
            radio_loads.append(
                WifiRadioLoad(
                    radio_id=radio_id,
                    offset=offset,
                    interval=interval,
                    count=count,
                    dataframe=pd.DataFrame(),
                    error=f"Dekodierung fehlgeschlagen ({exc}).",
                )
            )
            continue

        rows = []
        for idx, entry in enumerate(decoded.strip().split(";")):
            if not entry:
                continue
            parts = entry.split(",")
            if len(parts) != 2:
                continue
            global_usage = parse_int(parts[0])
            own_tx_usage = parse_int(parts[1])
            if global_usage is None or own_tx_usage is None:
                continue
            rows.append(
                {
                    "Sekunde": offset + idx * interval,
                    "Global Usage (%)": global_usage,
                    "Own TX Usage (%)": own_tx_usage,
                }
            )
            if count and len(rows) >= count:
                break
        radio_loads.append(
            WifiRadioLoad(
                radio_id=radio_id,
                offset=offset,
                interval=interval,
                count=count,
                dataframe=pd.DataFrame(rows),
            )
        )
    return radio_loads


def parse_wlan_noisefloor(text: str) -> List[WifiNoiseFloorEntry]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION WLAN_SCAN_RESULTS WLAN scan results")
    if not section:
        return []

    radio_header = re.compile(r"Scan results for radio '(\d+)':")
    entries: List[WifiNoiseFloorEntry] = []
    matches = list(radio_header.finditer(section))
    for idx, match in enumerate(matches):
        radio_id = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section)
        block = section[start:end]

        table_match = re.search(r"Noisefloor table:\s*(.*?)\s*Scan table:", block, re.DOTALL)
        if not table_match:
            continue
        table_block = table_match.group(1)
        for line in table_block.splitlines():
            row_match = re.search(
                r"\[\s*\d+\]:\s*(\d+)\s*MHz\s*\(\s*(\d+)\)\s+(-?\d+)\s+(-?\d+)",
                line,
            )
            if not row_match:
                continue
            frequency = int(row_match.group(1))
            channel = int(row_match.group(2))
            noise_floor = int(row_match.group(3))
            load = int(row_match.group(4))
            if frequency < 3000:
                band = "2,4 GHz"
            elif frequency >= 5925:
                band = "6 GHz"
            else:
                band = "5 GHz"
            entries.append(
                WifiNoiseFloorEntry(
                    radio_id=radio_id,
                    frequency_mhz=frequency,
                    channel=channel,
                    noise_floor=noise_floor,
                    load=load,
                    band=band,
                )
            )
    return entries


def extract_value(block: str, key: str) -> Optional[str]:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+)$", block, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'")


def extract_device_mac(text: str) -> Optional[str]:
    match = re.search(r"^maca\s+([0-9a-f:]{17})\s*$", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return match.group(1).upper()


def parse_fritz_model(text: str) -> Optional[str]:
    match = re.search(r"^CONFIG_PRODUKT_NAME\s*=\s*(.+)$", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'")


def parse_fritz_uptime_line(text: str) -> Optional[str]:
    match = re.search(r"^uptime:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def parse_fritz_uptime_days_minutes(text: str) -> Optional[str]:
    uptime_line = parse_fritz_uptime_line(text)
    if not uptime_line:
        return None
    days_match = re.search(r"(\d+)\s+days?", uptime_line, re.IGNORECASE)
    minutes_match = re.search(r"(\d+)\s+min", uptime_line, re.IGNORECASE)
    if not days_match and not minutes_match:
        return None
    parts = []
    if days_match:
        days = int(days_match.group(1))
        day_label = "Tag" if days == 1 else "Tage"
        parts.append(f"{days} {day_label}")
    if minutes_match:
        minutes = int(minutes_match.group(1))
        parts.append(f"{minutes} Min")
    return ", ".join(parts)


def parse_fritz_load_average(text: str) -> Optional[List[str]]:
    uptime_line = parse_fritz_uptime_line(text)
    if not uptime_line:
        return None
    match = re.search(
        r"load average:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)",
        uptime_line,
        re.IGNORECASE,
    )
    if not match:
        return None
    return [match.group(1), match.group(2), match.group(3)]


def parse_fritz_firmware_version(text: str) -> Optional[str]:
    version_match = re.search(r"^#####\s+TITLE\s+Version\s+(.+)$", text, re.MULTILINE)
    if not version_match:
        return None
    version = version_match.group(1).strip()
    parts = [part for part in version.split(".") if part]
    if len(parts) > 1:
        version = ".".join(parts[1:])
    elif len(version) > 3:
        version = version[3:]
    subversion_match = re.search(r"^#####\s+TITLE\s+SubVersion\s+(.+)$", text, re.MULTILINE)
    if subversion_match:
        subversion = subversion_match.group(1).strip()
        version = f"{version}{subversion}"
    return version


def parse_lan_ports(text: str) -> List[LanPort]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION Ethernet-ctlmgr-fbstateeth")
    ports = []
    for line in section.splitlines():
        match = re.match(r"\s*\d+:\s*(LAN:\d+|WAN:\d+)\s+\w+\s+(.*)", line)
        if not match:
            continue
        port = match.group(1)
        details = match.group(2)
        status = "up" if "up" in details else "down"
        speed_match = re.search(r"(\d+Mbps)", details)
        ports.append(LanPort(port=port, status=status, speed=speed_match.group(1) if speed_match else None))
    return ports


def parse_neighbour_clients(text: str) -> List[NeighbourClient]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION neighbours Neighbors")
    if not section:
        return []

    matches = list(re.finditer(r"\[(?P<mac>[0-9a-f:]{17})\]", section, re.IGNORECASE))
    clients = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[start:end]

        interface_match = re.search(r"\(([^,]+)", block)
        interface = interface_match.group(1).strip() if interface_match else ""
        interface_base = interface.split("_")[-1].lower()
        connection_type = None
        if interface_base.startswith("eth"):
            connection_type = "LAN"
        elif interface_base.startswith("ath"):
            connection_type = "WLAN"

        lan_port_match = re.search(r"lanport=([^,)\s]+)", block)
        lan_port = lan_port_match.group(1) if lan_port_match and lan_port_match.group(1) else None
        speed_match = re.search(r"speed=([0-9]+)", block)
        speed = speed_match.group(1) if speed_match else None

        friendly_match = re.search(r"friendlyname=([^\s]+)", block)
        dns_match = re.search(r"dnsname=([^\s]+)", block)
        name = friendly_match.group(1) if friendly_match else (dns_match.group(1) if dns_match else None)
        if not name:
            device_match = re.search(r"\b([A-Za-z0-9._-]+)\s+device_class=", block)
            if device_match:
                name = device_match.group(1)

        ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", block)
        ip_address = ip_match.group(1) if ip_match else None

        clients.append(
            NeighbourClient(
                mac=match.group("mac"),
                interface=interface,
                connection_type=connection_type,
                ip_address=ip_address,
                name=name,
                lan_port=lan_port,
                speed=speed,
                is_online=bool(re.search(r"\bONLINE\b", block)),
            )
        )
    return clients



def parse_mesh_topology(text: str) -> MeshTopology:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION MESH daemon")
    if not section:
        return MeshTopology(nodes=[], links=[])

    dump_match = re.search(
        r"===== Mesh Topology Dump Begin =====\s*(\{.*?\})\s*===== Mesh Topology Dump End =====",
        section,
        re.DOTALL,
    )
    if not dump_match:
        return MeshTopology(nodes=[], links=[], error="Mesh-Topologie-Dump nicht gefunden.")

    try:
        payload = json.loads(dump_match.group(1))
    except json.JSONDecodeError as exc:
        return MeshTopology(nodes=[], links=[], error=f"Mesh-Topologie konnte nicht gelesen werden: {exc.msg}")

    nodes = payload.get("nodes") or []
    links_by_uid: Dict[str, dict] = {}
    for node in nodes:
        node_uid = node.get("uid")
        for interface in node.get("node_interfaces") or []:
            for link in interface.get("node_links") or []:
                link_uid = link.get("uid")
                if not link_uid:
                    continue
                links_by_uid.setdefault(
                    link_uid,
                    {
                        "uid": link_uid,
                        "type": link.get("type"),
                        "state": link.get("state"),
                        "node_1_uid": link.get("node_1_uid") or node_uid,
                        "node_2_uid": link.get("node_2_uid"),
                        "cur_data_rate_rx": link.get("cur_data_rate_rx"),
                        "cur_data_rate_tx": link.get("cur_data_rate_tx"),
                    },
                )

    return MeshTopology(nodes=nodes, links=list(links_by_uid.values()))


def build_mesh_positions(mesh: MeshTopology, disconnected_clients: Optional[set] = None) -> Dict[str, Tuple[float, float]]:
    disconnected_clients = disconnected_clients or set()
    nodes_by_uid = {node.get("uid"): node for node in mesh.nodes if node.get("uid")}
    links = [
        link for link in mesh.links if link.get("node_1_uid") in nodes_by_uid and link.get("node_2_uid") in nodes_by_uid
    ]

    def _is_infra(node: dict) -> bool:
        role = (node.get("mesh_role") or "").lower()
        capabilities = set(node.get("device_capabilities") or [])
        return role in {"master", "slave"} or "ROUTER" in capabilities or "WLAN_ACCESS_POINT" in capabilities

    master_uid = next(
        (node.get("uid") for node in mesh.nodes if (node.get("mesh_role") or "").lower() == "master"),
        None,
    )
    infra_uids = [uid for uid, node in nodes_by_uid.items() if _is_infra(node)]
    if master_uid and master_uid in infra_uids:
        infra_uids = [master_uid] + [uid for uid in infra_uids if uid != master_uid]
    elif master_uid:
        infra_uids = [master_uid] + infra_uids

    client_uids = [uid for uid in nodes_by_uid.keys() if uid not in set(infra_uids)]

    positions: Dict[str, Tuple[float, float]] = {}
    infra_spacing = 5.0
    for index, uid in enumerate(infra_uids):
        positions[uid] = (index * infra_spacing, 0.0)

    client_by_parent: Dict[str, List[str]] = {uid: [] for uid in infra_uids}
    fallback_clients: List[str] = []
    for client_uid in client_uids:
        parent_uid = None
        for link in links:
            node_1 = link.get("node_1_uid")
            node_2 = link.get("node_2_uid")
            if client_uid == node_1 and node_2 in client_by_parent:
                parent_uid = node_2
                break
            if client_uid == node_2 and node_1 in client_by_parent:
                parent_uid = node_1
                break
        if parent_uid:
            client_by_parent[parent_uid].append(client_uid)
        else:
            fallback_clients.append(client_uid)

    disconnected_row: List[str] = []
    for parent_uid, assigned_clients in client_by_parent.items():
        parent_x, _ = positions[parent_uid]
        connected_clients = [uid for uid in assigned_clients if uid not in disconnected_clients]
        disconnected_clients_on_parent = [uid for uid in assigned_clients if uid in disconnected_clients]

        for index, client_uid in enumerate(connected_clients):
            row = index // 4
            column = index % 4
            offset_x = (column - 1.5) * 1.7
            positions[client_uid] = (parent_x + offset_x, -2.3 - (row * 2.0))

        disconnected_row.extend(disconnected_clients_on_parent)

    fallback_connected = [uid for uid in fallback_clients if uid not in disconnected_clients]
    for index, client_uid in enumerate(fallback_connected):
        positions[client_uid] = (index * 2.2, -6.6)

    for client_uid in fallback_clients:
        if client_uid in disconnected_clients:
            disconnected_row.append(client_uid)

    for index, client_uid in enumerate(disconnected_row):
        positions[client_uid] = (index * 2.2, -8.8)

    return positions


def is_mesh_client_connected(node: dict, mesh_links: List[dict]) -> bool:
    for key in ("is_online", "online", "active"):
        value = node.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value > 0

    status_text = " ".join(
        str(node.get(key, ""))
        for key in ("status", "node_status", "connection_status", "online_status")
    ).lower()
    if any(token in status_text for token in ("offline", "disconnected", "down", "nicht verbunden")):
        return False
    if any(token in status_text for token in ("online", "connected", "up", "verbunden")):
        return True

    uid = node.get("uid")
    related_links = [
        link
        for link in mesh_links
        if uid in (link.get("node_1_uid"), link.get("node_2_uid"))
    ]
    if not related_links:
        return False

    link_states = " ".join(str(link.get("state", "")) for link in related_links).lower()
    if any(token in link_states for token in ("offline", "disconnected", "down", "inactive")):
        return False
    return True






def _format_binary_state(value: Optional[int], yes: str = "Ja", no: str = "Nein") -> str:
    if value is None:
        return "k.A."
    return yes if value == 1 else no


def _format_no_emission_mode(value: Optional[int]) -> str:
    mode_labels = {
        0: "Nein",
        1: "Ja",
        2: "Nachtschaltung",
    }
    if value is None:
        return "k.A."
    return mode_labels.get(value, str(value))


def _format_repeater_mode(value: Optional[int]) -> str:
    if value is None:
        return "k.A."
    return "Ja" if value == 0 else "Nein"


def render_dect_basis_info(info: Optional[DectBasisInfo]) -> None:
    st.subheader("DECTBasisInfo")
    if not info:
        st.info("Keine DECT-Basisinformationen gefunden.")
        return

    rows = [
        {"Eigenschaft": "DECT aktiviert", "Wert": _format_binary_state(info.dect_enabled)},
        {"Eigenschaft": "Als Repeater konfiguriert", "Wert": _format_binary_state(info.dect_repeater_enabled)},
        {"Eigenschaft": "Funkleistung verringern (ECOMode)", "Wert": _format_binary_state(info.eco_mode)},
        {"Eigenschaft": "DECT Eco aktiv", "Wert": _format_no_emission_mode(info.no_emission)},
        {"Eigenschaft": "Aktueller DECT-Eco-Status", "Wert": info.no_emission_state if info.no_emission_state is not None else "k.A."},
        {"Eigenschaft": "Verschlüsselung aktiv", "Wert": _format_repeater_mode(info.repeater_mode)},
        {"Eigenschaft": "GAP-Problembehandlung", "Wert": _format_binary_state(info.overlapped_sending)},
        {"Eigenschaft": "Erweiterte Sicherheitsfunktionen", "Wert": _format_binary_state(info.ext_security)},
        {"Eigenschaft": "CATiq 2.0 aktiviert", "Wert": _format_binary_state(info.catiq20support)},
        {"Eigenschaft": "PIN-Schutz aktiv", "Wert": _format_binary_state(info.pin_protect)},
        {"Eigenschaft": "Smarthomegeräteverschlüsselung", "Wert": _format_binary_state(info.avmuleaes)},
        {"Eigenschaft": "RFPI (DECT-Basiskennung)", "Wert": info.rfpi or "k.A."},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def assess_dect_rssi(rssi: Optional[float]) -> str:
    if rssi is None:
        return "k.A."
    if rssi > -80:
        return "Eher ungünstig"
    return "Unauffällig"


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


def extract_training_state(section: str) -> Optional[str]:
    if not section:
        return None
    match = re.search(r"^Training State:\s*(.+)$", section, re.MULTILINE | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def is_showtime_state(state: Optional[str]) -> bool:
    if not state:
        return False
    return "showtime" in state.lower()



def _extract_ar7cfg_body(text: str) -> str:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION ar7_cfg /var/flash/ar7.cfg")
    if not section:
        return ""
    start = section.find("ar7cfg {")
    if start == -1:
        return ""
    brace_level = 0
    end = None
    for index in range(start, len(section)):
        char = section[index]
        if char == "{":
            brace_level += 1
        elif char == "}":
            brace_level -= 1
            if brace_level == 0:
                end = index + 1
                break
    if end is None:
        return section[start:]
    return section[start:end]


def _strip_quotes(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip().rstrip(';').strip().strip('"').strip("'")


def _format_toggle_state(value: Optional[object]) -> str:
    if value is None:
        return "k.A."
    normalized = str(value).strip().lower()
    if not normalized:
        return "k.A."
    if normalized in {"yes", "on", "1", "true", "enabled"}:
        return "Aktiviert"
    if normalized in {"no", "off", "0", "false", "disabled"}:
        return "Deaktiviert"
    return str(value)


def _mode_label(raw_mode: Optional[str]) -> str:
    if not raw_mode:
        return "k.A."
    mode = raw_mode.lower()
    if "router" in mode:
        return "Router"
    if "bridge" in mode:
        return "Bridge"
    return raw_mode


def _ipv4_label(raw_mode: Optional[str]) -> str:
    mapping = {
        "ipv4_normal": "Normal",
        "ipv4_ds_lite": "DS-Lite",
        "ipv4_off": "Aus",
    }
    return mapping.get((raw_mode or "").strip().lower(), raw_mode or "k.A.")


def _ipv6_label(raw_mode: Optional[str]) -> str:
    mapping = {
        "ipv6_native": "Native",
        "ipv6_off": "Aus",
        "ipv6_6to4": "6to4",
    }
    return mapping.get((raw_mode or "").strip().lower(), raw_mode or "k.A.")


def _find_block_value(block: str, key: str) -> Optional[str]:
    return _strip_quotes(extract_value(block, key))


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


def _extract_named_blocks(text: str, block_name: str) -> List[str]:
    blocks = []
    pattern = re.compile(rf"{re.escape(block_name)}\s*\{{")
    for match in pattern.finditer(text):
        start = match.end() - 1
        brace_level = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                brace_level += 1
            elif char == "}":
                brace_level -= 1
                if brace_level == 0:
                    blocks.append(text[start + 1:index])
                    break
    return blocks


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
def detect_access_technology(text: str) -> str:
    dsl_section = extract_section_by_prefix(text, "#### BEGIN SECTION DSLManager_port_1_1")
    fiber_section = extract_section_between(
        text,
        "#### BEGIN SECTION FIBERManager_port_1_1",
        "#### END SECTION FIBERManager_port_1_1",
    )

    fiber_state = extract_training_state(fiber_section)
    dsl_state = extract_training_state(dsl_section)

    fiber_rate = extract_kbits_rate(fiber_section, "Downstream Rate")
    dsl_rate = extract_kbits_rate(dsl_section, "Downstream Rate")

    fiber_active = is_showtime_state(fiber_state) or (fiber_rate is not None and fiber_rate > 0)
    dsl_active = is_showtime_state(dsl_state) or (dsl_rate is not None and dsl_rate > 0)

    if fiber_active and not dsl_active:
        return "Fiber"
    if dsl_active and not fiber_active:
        return "DSL"
    if fiber_active and dsl_active:
        return "Fiber"
    if re.search(r"^CONFIG_FIBER=y\s*$", text, re.MULTILINE) or "FIBER Overview" in text:
        return "Fiber"
    annex_match = re.search(r"^annex\s+(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if annex_match and "kabel" in annex_match.group(1).lower():
        return "Cable"
    return "DSL"


def parse_dsl_snr(text: str) -> dict:
    section = extract_section_by_prefix(text, "DSL Spectrum")
    return {
        "Bits Array DS": extract_numeric_array(section, "Bits Array DS"),
        "Bits Array US": extract_numeric_array(section, "Bits Array US"),
        "SNR Array DS": extract_numeric_array(section, "SNR Array DS"),
        "SNR Array US": extract_numeric_array(section, "SNR Array US"),
        "HLOG DS Array": extract_numeric_array(section, "HLOG DS Array"),
        "HLOG US Array": extract_numeric_array(section, "HLOG US Array"),
    }




def parse_dsl_metrics(text: str) -> dict:
    section = extract_section_by_prefix(text, "#### BEGIN SECTION DSLManager_port_1_1")
    if not section:
        return {}

    bridgetap_block = extract_section_block(section, "Bridgetaps")
    bridgetap_found = None
    bridgetap_length = None
    if bridgetap_block:
        if "no bridge taps found" in bridgetap_block:
            bridgetap_found = False
        else:
            bridgetap_found = True
            bridgetap_length = extract_int_value(bridgetap_block, "BT length (m)")

    resyncs = extract_numeric_array_loose(section, "Resyncs")
    retrains = extract_numeric_array_loose(section, "Host triggered Retrains")

    return {
        "loop_length_m": extract_int_value(section, "Estimated loop length"),
        "ds_rate_kbits": extract_kbits_rate(section, "Downstream Rate"),
        "us_rate_kbits": extract_kbits_rate(section, "Upstream Rate"),
        "ds_margin_db": extract_float_value(section, "DS Margin (dB)"),
        "us_margin_db": extract_float_value(section, "US Margin (dB)"),
        "ds_attenuation_db": extract_float_value(section, "DS Attenuation (dB)"),
        "us_attenuation_db": extract_float_value(section, "US Attenuation (dB)"),
        "ds_total_fec": extract_int_value(section, "DS total FEC"),
        "us_total_fec": extract_int_value(section, "US total FEC"),
        "ds_total_crc": extract_int_value(section, "DS total CRC"),
        "us_total_crc": extract_int_value(section, "US total CRC"),
        "ds_es": extract_int_value(section, "DS ES"),
        "us_es": extract_int_value(section, "US ES"),
        "resyncs_24h": sum(resyncs) if resyncs else None,
        "retrains_24h": sum(retrains) if retrains else None,
        "bridgetap_found": bridgetap_found,
        "bridgetap_length_m": bridgetap_length,
    }


def parse_fiber_overview(text: str) -> dict:
    section = extract_section_between(
        text,
        "#### BEGIN SECTION FIBERManager_port_1_1",
        "#### END SECTION FIBERManager_port_1_1",
    )
    if not section:
        return {}

    def parse_line(label: str) -> Optional[str]:
        match = re.search(rf"^\s*{re.escape(label)}:\s*(.+)$", section, re.MULTILINE)
        if not match:
            return None
        return match.group(1).strip()

    vlan_rules = []
    vlan_match = re.search(r"Vlan Rule Table:\n(.*?)(?:\nVlan Rule Translation:)", section, re.DOTALL)
    if vlan_match:
        for line in vlan_match.group(1).splitlines():
            row_match = re.match(
                r"^\s*(\d+)\s*\|\s*(\d+)\s+(\d+)\s+(\d+)\s*\|\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\|\s*(\d+)\s*\|",
                line,
            )
            if not row_match:
                continue
            vlan_rules.append(
                {
                    "Regel": int(row_match.group(1)),
                    "Outer Prio": int(row_match.group(2)),
                    "Outer VLAN": int(row_match.group(3)),
                    "Inner Prio": int(row_match.group(5)),
                    "Inner VLAN": int(row_match.group(6)),
                    "Remove Tags": int(row_match.group(9)),
                }
            )

    def parse_scaled(label: str, divisor: float) -> Optional[float]:
        raw_value = parse_line(label)
        if raw_value is None:
            return None
        try:
            return float(raw_value) / divisor
        except ValueError:
            return None

    return {
        "downstream_rate_kbits": extract_kbits_rate(section, "Downstream Rate"),
        "upstream_rate_kbits": extract_kbits_rate(section, "Upstream Rate"),
        "olt_vendor": parse_line("OLT Vendor"),
        "olt_vendor_id": parse_line("OLT Vendor ID"),
        "olt_version": parse_line("OLT VersionNumber"),
        "sfp_label": parse_line("SFP Label"),
        "sfp_vendor": parse_line("SFP Vendor"),
        "sfp_part_number": parse_line("SFP Part Number"),
        "sfp_serial": parse_line("SFP Serial"),
        "vlan_rules": vlan_rules,
        "temperature_c": parse_scaled("Temperature (0.1 deg C)", 10),
        "supply_voltage_v": parse_scaled("Supply Voltage (mV)", 1000),
        "tx_bias_ma": parse_scaled("Tx Bias Current (mA)", 1),
        "tx_optical_dbm": parse_scaled("Tx Optical Pwr (0.1 dBm)", 10),
        "rx_optical_dbm": parse_scaled("Rx Received Pwr (0.1 dBm)", 10),
        "apd_voltage_v": parse_scaled("APD Voltage (0.1 V)", 10),
        "ploam_state": parse_line("Current PLOAM State"),
        "ploam_alarm": parse_line("Emergency Alarm State"),
    }


def render_fiber_dashboard(fiber_data: dict) -> None:
    st.subheader("FIBER Overview")
    if not fiber_data:
        st.info("Keine FIBER-Daten gefunden.")
        return

    st.subheader("Sync")
    render_metric_rows(
        [
            ("Downstream", format_sync_rate(fiber_data.get("downstream_rate_kbits"))),
            ("Upstream", format_sync_rate(fiber_data.get("upstream_rate_kbits"))),
        ],
        columns=2,
    )

    st.subheader("OLT")
    render_metric_rows(
        [
            ("OLT Vendor", fiber_data.get("olt_vendor") or "k.A."),
            ("OLT Vendor ID", fiber_data.get("olt_vendor_id") or "k.A."),
            ("OLT Version", fiber_data.get("olt_version") or "k.A."),
        ],
        columns=3,
    )

    st.subheader("SFP")
    render_metric_rows(
        [
            ("Label", fiber_data.get("sfp_label") or "k.A."),
            ("Vendor", fiber_data.get("sfp_vendor") or "k.A."),
            ("Part Number", fiber_data.get("sfp_part_number") or "k.A."),
            ("Serial", fiber_data.get("sfp_serial") or "k.A."),
        ],
        columns=4,
    )

    st.subheader("VLAN Regeln")
    vlan_rules = fiber_data.get("vlan_rules", [])
    if vlan_rules:
        st.dataframe(pd.DataFrame(vlan_rules), use_container_width=True)
    else:
        st.info("Keine VLAN-Regeln gefunden.")

    st.subheader("FIBER Werte")
    render_metric_rows(
        [
            (
                "Temperatur (°C)",
                f"{fiber_data['temperature_c']:.1f}" if fiber_data.get("temperature_c") is not None else "k.A.",
            ),
            (
                "Supply Voltage (V)",
                f"{fiber_data['supply_voltage_v']:.3f}"
                if fiber_data.get("supply_voltage_v") is not None
                else "k.A.",
            ),
            ("Tx Bias (mA)", f"{fiber_data['tx_bias_ma']:.1f}" if fiber_data.get("tx_bias_ma") is not None else "k.A."),
            (
                "Tx Optical (dBm)",
                f"{fiber_data['tx_optical_dbm']:.1f}" if fiber_data.get("tx_optical_dbm") is not None else "k.A.",
            ),
            (
                "Rx Optical (dBm)",
                f"{fiber_data['rx_optical_dbm']:.1f}" if fiber_data.get("rx_optical_dbm") is not None else "k.A.",
            ),
            (
                "APD Voltage (V)",
                f"{fiber_data['apd_voltage_v']:.1f}" if fiber_data.get("apd_voltage_v") is not None else "k.A.",
            ),
        ],
        columns=3,
    )

    st.subheader("PLOAM Status")
    render_metric_rows(
        [
            ("Current PLOAM State", fiber_data.get("ploam_state") or "k.A."),
            ("Emergency Alarm State", fiber_data.get("ploam_alarm") or "k.A."),
        ],
        columns=2,
    )


def extract_docsis_state(text: str) -> str:
    start_marker = "##### BEGIN SECTION DOCSIS Supportdata cable"
    end_marker = "##### END SECTION DOCSIS"
    pattern = re.compile(
        rf"{re.escape(start_marker)}\n(.*?)(?=^\s*{re.escape(end_marker)}\s*$)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    if match:
        return match.group(1)

    showdocsis_marker = "showdocsisstate:"
    start_index = text.find(showdocsis_marker)
    if start_index == -1:
        return ""
    end_index = text.find(end_marker, start_index)
    if end_index == -1:
        return text[start_index:]
    return text[start_index:end_index]


def parse_docsis_value(text: str, label: str) -> Optional[str]:
    match = re.search(rf"^\s*{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def parse_table_rows(section: str, start_marker: str, columns: List[str]) -> List[dict]:
    start_index = section.find(start_marker)
    if start_index == -1:
        return []
    lines = section[start_index:].splitlines()
    data_lines = []
    separator_found = False
    for line in lines:
        if not separator_found:
            if re.match(r"\s*-{2,}\+", line):
                separator_found = True
            continue
        if not line.strip():
            break
        if line.strip().startswith("|"):
            continue
        if re.match(r"\s*-{2,}", line):
            continue
        if "|" not in line:
            break
        data_lines.append(line)

    rows = []
    for line in data_lines:
        parts = [part.strip() for part in line.split("|")]
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        if len(parts) < len(columns):
            continue
        row = dict(zip(columns, parts[: len(columns)]))
        rows.append(row)
    return rows




def is_plausible_channel(active: str, frequency: Optional[float], power: Optional[float], modulation: str) -> bool:
    if active.strip().upper() != "YES":
        return False
    if frequency is None or frequency <= 0:
        return False
    if power is None or abs(power) < 0.1:
        return False
    if modulation.strip().lower() in {"er", "none", "0"}:
        return False
    return True


def parse_docsis_channels(text: str) -> dict:
    section = extract_docsis_state(text)
    if not section:
        return {}

    operational_mode = parse_docsis_value(section, "Operational mode")
    frequency_plan = parse_docsis_value(section, "Frequency plan")
    modem_status = parse_docsis_value(section, "Modem status")

    upstream_columns = [
        "ID",
        "Active",
        "Frequency",
        "SymRate",
        "ChWidth",
        "Attenuation",
        "Power",
        "Power16",
        "P-High",
        "P-Low",
        "P-DRW-High",
        "P-DRW-Low",
        "DeltaPF",
        "Mod",
        "Mux",
    ]
    downstream_columns = [
        "ID",
        "Active",
        "Frequency",
        "Primary",
        "Power",
        "MSE",
        "CorrWords",
        "UncorrWords",
        "QAMLock",
        "FECLock",
        "MPEGLock",
        "Mod",
        "Annex",
    ]
    ofdm_columns = [
        "ID",
        "Active",
        "Frequency",
        "Primary",
        "PLC Freq",
        "Power",
        "MER",
        "CorrWords",
        "UncorrWords",
        "Max Mod",
        "Rolloff Period",
        "Cyclic Prefix",
        "FFT Size",
    ]

    upstream_rows = parse_table_rows(section, "Single-Carrier Channels", upstream_columns)
    downstream_rows = parse_table_rows(section, "Single-Carrier Receivers", downstream_columns)
    ofdm_rows = parse_table_rows(section, "Multi-Carrier (OFDM) Receivers", ofdm_columns)

    upstream_channels = []
    for row in upstream_rows:
        frequency = parse_channel_float(row["Frequency"])
        power = parse_channel_float(row["Power"])
        if not is_plausible_channel(row["Active"], frequency, power, row["Mod"]):
            continue
        upstream_channels.append(
            {
                "ID": parse_int(row["ID"]),
                "Aktiv": row["Active"],
                "Frequenz (MHz)": frequency,
                "Power (dBmV)": power,
                "Modulation": row["Mod"],
            }
        )

    downstream_channels = []
    for row in downstream_rows:
        frequency = parse_channel_float(row["Frequency"])
        power = parse_channel_float(row["Power"])
        mse = parse_channel_float(row["MSE"])
        if not is_plausible_channel(row["Active"], frequency, power, row["Mod"]):
            continue
        if mse is None or abs(mse) < 0.1:
            continue
        downstream_channels.append(
            {
                "ID": parse_int(row["ID"]),
                "Aktiv": row["Active"],
                "Frequenz (MHz)": frequency,
                "Power (dBmV)": power,
                "MSE (dB)": mse,
                "CorrWords": parse_int(row["CorrWords"]) or 0,
                "UncorrWords": parse_int(row["UncorrWords"]) or 0,
                "Modulation": row["Mod"],
            }
        )

    ofdm_channels = []
    for row in ofdm_rows:
        freq_match = re.match(r"([\d.]+)\s*-\s*([\d.]+)", row["Frequency"])
        if not freq_match:
            continue
        freq_start = parse_channel_float(freq_match.group(1))
        freq_end = parse_channel_float(freq_match.group(2))
        power = parse_channel_float(row["Power"])
        mer = parse_channel_float(row["MER"])
        if row["Active"].strip().upper() != "YES":
            continue
        if freq_start is None or freq_start <= 0 or freq_end is None or freq_end <= 0:
            continue
        if power is None or abs(power) < 0.1:
            continue
        if mer is None or mer <= 0:
            continue
        ofdm_channels.append(
            {
                "ID": parse_int(row["ID"]),
                "Aktiv": row["Active"],
                "Frequenz (MHz)": f"{freq_start:.3f} - {freq_end:.3f}",
                "PLC Freq (MHz)": parse_channel_float(row["PLC Freq"]),
                "Power (dBmV)": power,
                "MER (dB)": mer,
                "CorrWords": parse_int(row["CorrWords"]) or 0,
                "UncorrWords": parse_int(row["UncorrWords"]) or 0,
                "Max Mod": row["Max Mod"],
            }
        )

    return {
        "operational_mode": operational_mode,
        "frequency_plan": frequency_plan,
        "modem_status": modem_status,
        "upstream_channels": upstream_channels,
        "downstream_channels": downstream_channels,
        "ofdm_channels": ofdm_channels,
        "spectrum_points": parse_cable_spectrum(text),
    }


def parse_cable_spectrum(text: str) -> List[dict]:
    spectrum_section = extract_section_between(
        text,
        "##### BEGIN SECTION DOCSIS cable spectrum",
        "##### END SECTION DOCSIS cable spectrum",
    )
    if not spectrum_section:
        return []

    data_line = next((line.strip() for line in spectrum_section.splitlines() if line.strip() and not line.startswith("#")), "")
    if not data_line:
        return []

    values: List[int] = []
    for raw_value in data_line.split(","):
        normalized = raw_value.strip()
        if not normalized:
            continue
        try:
            values.append(int(normalized))
        except ValueError:
            return []

    if len(values) < 4:
        return []

    min_freq_hz, max_freq_hz, step_hz = values[0], values[1], values[2]
    if step_hz <= 0 or max_freq_hz < min_freq_hz:
        return []

    amplitudes = values[3:]
    points = []
    for index, amplitude_raw in enumerate(amplitudes):
        frequency_hz = min_freq_hz + index * step_hz
        if frequency_hz > max_freq_hz:
            break
        points.append(
            {
                "Frequenz (MHz)": round(frequency_hz / 1_000_000, 3),
                "Pegel (dB)": amplitude_raw / 10,
            }
        )
    return points




def build_cable_usage_ranges(docsis_data: dict, spectrum_points: List[dict]) -> List[dict]:
    ranges: List[dict] = []

    for channel in docsis_data.get("ofdm_channels", []):
        range_value = _parse_frequency_range(channel.get("Frequenz (MHz)"))
        if range_value is None:
            continue
        ranges.append(
            {
                "Kategorie": "Verwendeter DOCSIS 3.1-Kanal",
                "Start (MHz)": range_value[0],
                "Ende (MHz)": range_value[1],
            }
        )

    downstream_centers = []
    for channel in docsis_data.get("downstream_channels", []):
        center = parse_channel_float(channel.get("Frequenz (MHz)"))
        if center is None:
            continue
        downstream_centers.append(center)
        ranges.append(
            {
                "Kategorie": "Verwendeter DOCSIS 3.0-Kanal",
                "Start (MHz)": max(0.0, center - 4.0),
                "Ende (MHz)": center + 4.0,
            }
        )

    plc_frequencies = []
    for channel in docsis_data.get("ofdm_channels", []):
        plc_frequency = parse_channel_float(channel.get("PLC Freq (MHz)"))
        if plc_frequency is None or plc_frequency <= 0:
            continue
        plc_frequencies.append(plc_frequency)
        ranges.append(
            {
                "Kategorie": "PLC",
                "Start (MHz)": plc_frequency - 0.2,
                "Ende (MHz)": plc_frequency + 0.2,
            }
        )

    occupied_ranges = [
        (entry["Start (MHz)"], entry["Ende (MHz)"])
        for entry in ranges
        if entry["Kategorie"] in {"Verwendeter DOCSIS 3.1-Kanal", "Verwendeter DOCSIS 3.0-Kanal"}
    ]

    def is_occupied(freq: float) -> bool:
        return any(start <= freq <= end for start, end in occupied_ranges)

    outside_docsis = []
    for point in spectrum_points:
        frequency = parse_channel_float(point.get("Frequenz (MHz)"))
        level = parse_channel_float(point.get("Pegel (dB)"))
        if frequency is None or level is None or is_occupied(frequency):
            continue
        outside_docsis.append((frequency, level))

    def append_segments(category: str, values: List[Tuple[float, float]], threshold: float, is_tv: bool) -> None:
        start = None
        end = None
        for frequency, level in values:
            match = level > threshold if is_tv else level <= threshold
            if match:
                if start is None:
                    start = frequency
                end = frequency
            elif start is not None and end is not None:
                ranges.append({"Kategorie": category, "Start (MHz)": start, "Ende (MHz)": end})
                start = None
                end = None
        if start is not None and end is not None:
            ranges.append({"Kategorie": category, "Start (MHz)": start, "Ende (MHz)": end})

    if outside_docsis:
        levels = [level for _, level in outside_docsis]
        threshold = min(-15.0, (sum(levels) / len(levels)) - 5.0)
        append_segments("TV-Signal", outside_docsis, threshold, is_tv=True)
        append_segments("Ausschlussbereich", outside_docsis, threshold, is_tv=False)

    return [entry for entry in ranges if entry["Ende (MHz)"] > entry["Start (MHz)"]]


def connection_quality_label(rssi: int, quality: int) -> str:
    if rssi != 0:
        if rssi >= -55:
            return "Sehr gut"
        if rssi >= -67:
            return "Gut"
        if rssi >= -75:
            return "Mittel"
        return "Schwach"
    if quality >= 80:
        return "Sehr gut"
    if quality >= 60:
        return "Gut"
    if quality > 0:
        return "Mittel"
    return "Unbekannt"


def render_dsl_charts(dsl_data: dict) -> None:
    st.subheader("DSL Spektrum (SNR & HLOG)")
    if (
        not dsl_data["Bits Array DS"]
        and not dsl_data["Bits Array US"]
        and not dsl_data["SNR Array DS"]
        and not dsl_data["SNR Array US"]
        and not dsl_data["HLOG DS Array"]
        and not dsl_data["HLOG US Array"]
    ):
        st.info("Keine DSL-Spektrumsdaten gefunden.")
        return
    chart_specs = [
        ("Bits", "Bits Array DS", "Bits Array US"),
        ("SNR", "SNR Array DS", "SNR Array US"),
        ("HLOG", "HLOG DS Array", "HLOG US Array"),
    ]

    for label, ds_key, us_key in chart_specs:
        ds_values = dsl_data[ds_key]
        us_values = dsl_data[us_key]
        if not ds_values and not us_values:
            continue
        fig = go.Figure()
        if ds_values:
            fig.add_trace(
                go.Scatter(
                    y=ds_values,
                    mode="lines",
                    name="DS",
                    line={"color": "#1f77b4"},
                )
            )
        if us_values:
            fig.add_trace(
                go.Scatter(
                    y=us_values,
                    mode="lines",
                    name="US",
                    line={"color": "#ff7f0e"},
                )
            )
        fig.update_layout(
            title=f"{label} (DS/US)",
            xaxis_title="Ton",
            yaxis_title=label,
        )
        st.plotly_chart(fig, use_container_width=True)


def format_mbit(value_kbits: Optional[int]) -> str:
    if value_kbits is None:
        return "k.A."
    return f"{value_kbits / 1000:.1f} Mbit/s"


def format_sync_rate(value_kbits: Optional[int]) -> str:
    if value_kbits is None:
        return "k.A."
    mbit = value_kbits / 1000
    if mbit >= 1000:
        return f"{mbit / 1000:.1f} Gbit/s"
    return f"{mbit:.1f} Mbit/s"


def format_db(value: Optional[float]) -> str:
    if value is None:
        return "k.A."
    return f"{value:.1f} dB"


def format_count(value: Optional[int]) -> str:
    if value is None:
        return "k.A."
    return f"{value:,}".replace(",", ".")


def format_meters(value: Optional[int]) -> str:
    if value is None:
        return "k.A."
    return f"{value} m"


def format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "k.A."
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MB"
    if value >= 1_000:
        return f"{value / 1_000:.1f} KB"
    return f"{value} B"


def format_bool(value: bool) -> str:
    return "Ja" if value else "Nein"


def render_metric_rows(metrics: List[tuple[str, str]], columns: int = 3) -> None:
    if not metrics:
        return
    for idx in range(0, len(metrics), columns):
        row = metrics[idx : idx + columns]
        cols = st.columns(len(row))
        for column, (label, value) in zip(cols, row):
            column.metric(label, value)


def assess_line_quality(metrics: dict) -> str:
    ds_margin = metrics.get("ds_margin_db")
    ds_attenuation = metrics.get("ds_attenuation_db")
    ds_crc = metrics.get("ds_total_crc") or 0
    us_crc = metrics.get("us_total_crc") or 0
    ds_es = metrics.get("ds_es") or 0
    us_es = metrics.get("us_es") or 0
    resyncs = metrics.get("resyncs_24h") or 0
    retrains = metrics.get("retrains_24h") or 0

    if ds_crc + us_crc + ds_es + us_es + resyncs + retrains > 0:
        return "Auffällig: Fehler/Resyncs erkannt – Leitung beobachten."
    if ds_margin is not None and ds_attenuation is not None:
        if ds_margin >= 8 and ds_attenuation <= 20:
            return "Sehr gut: stabile Reserve bei geringer Dämpfung."
        if ds_margin >= 6 and ds_attenuation <= 30:
            return "Gut: solide Reserve und moderate Dämpfung."
    return "Mittel: Werte ok, aber Reserve/Dämpfung könnten besser sein."


def assess_cable_quality(docsis_data: dict) -> tuple[str, str]:
    downstream = docsis_data.get("downstream_channels", [])
    ofdm = docsis_data.get("ofdm_channels", [])

    corr_total = sum(channel.get("CorrWords", 0) for channel in downstream) + sum(
        channel.get("CorrWords", 0) for channel in ofdm
    )
    uncorr_total = sum(channel.get("UncorrWords", 0) for channel in downstream) + sum(
        channel.get("UncorrWords", 0) for channel in ofdm
    )
    power_values = [channel.get("Power (dBmV)") for channel in downstream + ofdm if channel.get("Power (dBmV)") is not None]
    mse_values = [channel.get("MSE (dB)") for channel in downstream if channel.get("MSE (dB)") is not None]
    mer_values = [channel.get("MER (dB)") for channel in ofdm if channel.get("MER (dB)") is not None]

    notes = []
    status = "success"

    if uncorr_total > 0:
        notes.append("Unkorrigierbare Fehler (UncorrWords) vorhanden.")
        status = "warning"
    if corr_total > 1_000_000:
        notes.append("Viele korrigierte Fehler (CorrWords) – beobachten.")
        status = "warning"

    if power_values:
        avg_power = sum(power_values) / len(power_values)
        if -10 <= avg_power <= 10:
            pass
        elif -15 <= avg_power <= 15:
            notes.append("Power leicht außerhalb des Idealbereichs (±10 dBmV).")
            status = "warning"
        else:
            notes.append("Power deutlich außerhalb des Idealbereichs (±10 dBmV).")
            status = "warning"

    if mse_values:
        avg_mse = sum(mse_values) / len(mse_values)
        if avg_mse <= -33:
            pass
        elif avg_mse <= -30:
            notes.append("MSE im Grenzbereich (DS).")
            status = "warning"
        else:
            notes.append("MSE auffällig (DS) – Signalqualität prüfen.")
            status = "warning"

    if mer_values:
        avg_mer = sum(mer_values) / len(mer_values)
        if avg_mer >= 38:
            pass
        elif avg_mer >= 33:
            notes.append("MER im Grenzbereich (OFDM).")
            status = "warning"
        else:
            notes.append("MER auffällig (OFDM) – Signalqualität prüfen.")
            status = "warning"

    if not notes:
        return "Leitung wirkt stabil (Power/MSE/MER im grünen Bereich).", "success"
    return " ".join(notes), status


def assess_cable_limits(docsis_data: dict) -> tuple[str, str]:
    downstream = docsis_data.get("downstream_channels", [])
    ofdm = docsis_data.get("ofdm_channels", [])
    upstream = docsis_data.get("upstream_channels", [])

    status = "success"
    lines = []

    ds_powers = [channel.get("Power (dBmV)") for channel in downstream if channel.get("Power (dBmV)") is not None]
    ofdm_powers = [channel.get("Power (dBmV)") for channel in ofdm if channel.get("Power (dBmV)") is not None]
    us_powers = [channel.get("Power (dBmV)") for channel in upstream if channel.get("Power (dBmV)") is not None]

    power_values = ds_powers + ofdm_powers
    if power_values:
        avg_power = sum(power_values) / len(power_values)
        min_power = min(power_values)
        max_power = max(power_values)
        if -10 <= avg_power <= 10:
            verdict = "im Soll"
        elif -15 <= avg_power <= 15:
            verdict = "leicht außerhalb"
            status = "warning"
        else:
            verdict = "außerhalb"
            status = "warning"
        lines.append(
            f"**Power DS/OFDM**: Ø {avg_power:.1f} dBmV (Bereich {min_power:.1f}–{max_power:.1f}) → {verdict} "
            "(Ziel: ±10 dBmV)."
        )
    else:
        lines.append("**Power DS/OFDM**: keine verwertbaren Werte gefunden.")

    if us_powers:
        avg_us_power = sum(us_powers) / len(us_powers)
        min_us_power = min(us_powers)
        max_us_power = max(us_powers)
        if 35 <= avg_us_power <= 50:
            verdict = "im Soll"
        elif 32 <= avg_us_power <= 51:
            verdict = "Grenzbereich"
            status = "warning"
        else:
            verdict = "außerhalb"
            status = "warning"
        lines.append(
            f"**Power US**: Ø {avg_us_power:.1f} dBmV (Bereich {min_us_power:.1f}–{max_us_power:.1f}) → {verdict} "
            "(Ziel: 35–50 dBmV)."
        )
    else:
        lines.append("**Power US**: keine verwertbaren Werte gefunden.")

    mse_values = [channel.get("MSE (dB)") for channel in downstream if channel.get("MSE (dB)") is not None]
    if mse_values:
        avg_mse = sum(mse_values) / len(mse_values)
        min_mse = min(mse_values)
        max_mse = max(mse_values)
        if avg_mse <= -33:
            verdict = "im Soll"
        elif avg_mse <= -30:
            verdict = "Grenzbereich"
            status = "warning"
        else:
            verdict = "außerhalb"
            status = "warning"
        lines.append(
            f"**MSE DS**: Ø {avg_mse:.1f} dB (Bereich {min_mse:.1f}–{max_mse:.1f}) → {verdict} "
            "(Ziel: ≤ -33 dB)."
        )
    else:
        lines.append("**MSE DS**: keine verwertbaren Werte gefunden.")

    modulation_values = []
    modulation_values.extend(
        mod for mod in (channel.get("Modulation") for channel in downstream + upstream) if mod
    )
    modulation_values.extend(mod for mod in (channel.get("Max Mod") for channel in ofdm) if mod)
    if modulation_values:
        unique_mods = sorted(set(modulation_values))
        invalid_mods = [mod for mod in unique_mods if "QAM" not in mod.upper() and "OFDM" not in mod.upper()]
        if invalid_mods:
            unique_invalid = ", ".join(invalid_mods)
            lines.append(f"**Modulation**: auffällig ({unique_invalid}).")
            status = "warning"
        else:
            lines.append(f"**Modulation**: typische QAM/OFDM-Werte erkannt ({', '.join(unique_mods)}).")
    else:
        lines.append("**Modulation**: keine verwertbaren Werte gefunden.")

    ds_freqs = [channel.get("Frequenz (MHz)") for channel in downstream if channel.get("Frequenz (MHz)") is not None]
    us_freqs = [channel.get("Frequenz (MHz)") for channel in upstream if channel.get("Frequenz (MHz)") is not None]
    ofdm_ranges = [channel.get("Frequenz (MHz)") for channel in ofdm if channel.get("Frequenz (MHz)")]

    out_of_range = []
    if ds_freqs:
        out_of_range.extend([freq for freq in ds_freqs if freq < 110 or freq > 1218])
    if us_freqs:
        out_of_range.extend([freq for freq in us_freqs if freq < 5 or freq > 85])
    for freq_range in ofdm_ranges:
        match = re.match(r"([\d.]+)\s*-\s*([\d.]+)", str(freq_range))
        if not match:
            continue
        start = parse_channel_float(match.group(1))
        end = parse_channel_float(match.group(2))
        if start is None or end is None:
            continue
        if start < 110 or end > 1218:
            out_of_range.append(freq_range)

    if ds_freqs or us_freqs or ofdm_ranges:
        if out_of_range:
            examples = ", ".join(sorted({str(value) for value in out_of_range})[:3])
            extra = f" Beispiele: {examples}." if examples else ""
            lines.append(
                "**Frequenz**: "
                f"{len(out_of_range)} Kanal(e) außerhalb typischer DOCSIS-Bänder "
                "(DS 110–1218 MHz, US 5–85 MHz)." + extra
            )
            status = "warning"
        else:
            lines.append("**Frequenz**: alle Kanäle innerhalb typischer DOCSIS-Bänder (DS 110–1218 MHz, US 5–85 MHz).")
    else:
        lines.append("**Frequenz**: keine verwertbaren Werte gefunden.")

    return "\n".join(f"- {line}" for line in lines), status


def render_dsl_metrics(metrics: dict) -> None:
    st.subheader("DSL Leitungswerte")
    if not metrics:
        st.info("Keine detaillierten DSL-Leitungswerte gefunden.")
        return

    render_metric_rows(
        [
            ("Leitungslänge", format_meters(metrics.get("loop_length_m"))),
            ("Sync Downstream", format_mbit(metrics.get("ds_rate_kbits"))),
            ("Sync Upstream", format_mbit(metrics.get("us_rate_kbits"))),
            ("SNR Downstream", format_db(metrics.get("ds_margin_db"))),
            ("SNR Upstream", format_db(metrics.get("us_margin_db"))),
            ("Leitungsdämpfung DS", format_db(metrics.get("ds_attenuation_db"))),
            ("Leitungsdämpfung US", format_db(metrics.get("us_attenuation_db"))),
            ("FEC (DS/US)", f"{format_count(metrics.get('ds_total_fec'))} / {format_count(metrics.get('us_total_fec'))}"),
            ("CRC (DS/US)", f"{format_count(metrics.get('ds_total_crc'))} / {format_count(metrics.get('us_total_crc'))}"),
            ("ES (DS/US)", f"{format_count(metrics.get('ds_es'))} / {format_count(metrics.get('us_es'))}"),
            ("Resyncs (24h)", format_count(metrics.get("resyncs_24h"))),
            ("Retrains (24h)", format_count(metrics.get("retrains_24h"))),
        ],
        columns=3,
    )

    bridgetap_found = metrics.get("bridgetap_found")
    if bridgetap_found is True:
        length = metrics.get("bridgetap_length_m")
        if length is not None:
            st.warning(f"Bridge Tap erkannt (ca. {length} m).")
        else:
            st.warning("Bridge Tap erkannt.")
    elif bridgetap_found is False:
        st.info("Keine Bridge Taps erkannt.")

    st.success(assess_line_quality(metrics))


def render_cable_dashboard(docsis_data: dict) -> None:
    st.subheader("DOCSIS Überblick")
    if not docsis_data:
        st.info("Keine DOCSIS-Daten gefunden.")
        return

    downstream = docsis_data.get("downstream_channels", [])
    ofdm = docsis_data.get("ofdm_channels", [])
    upstream = docsis_data.get("upstream_channels", [])

    corr_total = sum(channel.get("CorrWords", 0) for channel in downstream) + sum(
        channel.get("CorrWords", 0) for channel in ofdm
    )
    uncorr_total = sum(channel.get("UncorrWords", 0) for channel in downstream) + sum(
        channel.get("UncorrWords", 0) for channel in ofdm
    )

    render_metric_rows(
        [
            ("Operational Mode", docsis_data.get("operational_mode") or "k.A."),
            ("Frequency Plan", docsis_data.get("frequency_plan") or "k.A."),
            ("Modem Status", docsis_data.get("modem_status") or "k.A."),
            ("Downstream Kanäle", format_count(len(downstream))),
            ("OFDM Kanäle", format_count(len(ofdm))),
            ("Upstream Kanäle", format_count(len(upstream))),
            ("CorrWords (gesamt)", format_count(corr_total)),
            ("UncorrWords (gesamt)", format_count(uncorr_total)),
        ],
        columns=4,
    )

    st.subheader("Downstream Kanäle (DS)")
    if downstream:
        st.dataframe(pd.DataFrame(downstream), use_container_width=True)
    else:
        st.info("Keine plausiblen Downstream-Kanäle gefunden.")

    if ofdm:
        st.markdown("**OFDM (Downstream)**")
        st.dataframe(pd.DataFrame(ofdm), use_container_width=True)

    st.subheader("Upstream Kanäle (US)")
    if upstream:
        st.dataframe(pd.DataFrame(upstream), use_container_width=True)
    else:
        st.info("Keine plausiblen Upstream-Kanäle gefunden.")

    st.subheader("Cable Spektrum")
    spectrum_points = docsis_data.get("spectrum_points", [])
    if spectrum_points:
        spectrum_df = pd.DataFrame(spectrum_points)
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=spectrum_df["Frequenz (MHz)"],
                y=spectrum_df["Pegel (dB)"],
                mode="lines",
                name="Sonstiges Signal",
                line={"color": "#6f6f6f", "width": 1},
            )
        )

        usage_ranges = build_cable_usage_ranges(docsis_data, spectrum_points)
        usage_colors = {
            "Verwendeter DOCSIS 3.0-Kanal": "#2f7fbf",
            "Verwendeter DOCSIS 3.1-Kanal": "#4bc0c0",
            "TV-Signal": "#b7cde2",
            "Ausschlussbereich": "#d6d6d6",
            "PLC": "#7a3eb1",
        }
        legend_drawn = set()
        for usage in usage_ranges:
            category = usage["Kategorie"]
            color = usage_colors.get(category, "#9f9f9f")
            show_legend = category not in legend_drawn
            legend_drawn.add(category)
            fig.add_vrect(
                x0=usage["Start (MHz)"],
                x1=usage["Ende (MHz)"],
                fillcolor=color,
                opacity=0.35 if category not in {"PLC", "Ausschlussbereich"} else 0.5,
                line_width=0,
            )
            if show_legend:
                fig.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="markers",
                        marker={"size": 12, "symbol": "square", "color": color},
                        name=category,
                    )
                )

        fig.update_layout(
            title="DOCSIS Cable Spektrum",
            xaxis_title="Frequenz (MHz)",
            yaxis_title="Pegel (dB)",
        )
        st.plotly_chart(fig, use_container_width=True)

        if usage_ranges:
            usage_df = pd.DataFrame(usage_ranges).sort_values(["Start (MHz)", "Ende (MHz)"])
            st.caption("Zuordnung der Frequenzbereiche")
            st.dataframe(usage_df, use_container_width=True, hide_index=True)
    else:
        st.info("Keine Cable-Spektrumsdaten gefunden.")

    assessment, status = assess_cable_quality(docsis_data)
    if status == "success":
        st.success(assessment)
    else:
        st.warning(assessment)

    st.subheader("Grenzwert-Einschätzung (MSE, Power, Mod, Frequenz)")
    limit_assessment, limit_status = assess_cable_limits(docsis_data)
    if limit_status == "success":
        st.success(limit_assessment)
    else:
        st.warning(limit_assessment)


def render_wlan_scan(networks: List[WifiNetwork]) -> None:
    st.subheader("WLAN Umgebung (Scan)")
    if not networks:
        st.info("Keine WLAN-Scan-Daten gefunden.")
        return
    df = pd.DataFrame([network.__dict__ for network in networks])
    df = df.sort_values("rssi", ascending=False)
    strongest = df.iloc[0] if not df.empty else None
    weakest = df.iloc[-1] if not df.empty else None
    render_metric_rows(
        [
            ("Netzwerke gesamt", format_count(len(df))),
            ("Stärkstes RSSI", f"{strongest['rssi']} dBm" if strongest is not None else "k.A."),
            ("Schwächstes RSSI", f"{weakest['rssi']} dBm" if weakest is not None else "k.A."),
        ],
        columns=3,
    )
    fig = px.bar(
        df,
        x="ssid",
        y="rssi",
        color="radioband",
        labels={"rssi": "RSSI (dBm)", "ssid": "SSID", "radioband": "Band"},
        title="Gefundene WLANs nach Signalstärke",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True)


def render_wlan_noisefloor(entries: List[WifiNoiseFloorEntry]) -> None:
    st.subheader("WLAN Noisefloor/Load")
    if not entries:
        st.info("Keine Noisefloor-Daten gefunden.")
        return

    df = pd.DataFrame([entry.__dict__ for entry in entries])
    df = df.sort_values(["band", "frequency_mhz"])

    melted = df.melt(
        id_vars=["frequency_mhz", "band"],
        value_vars=["noise_floor", "load"],
        var_name="Metrik",
        value_name="Wert",
    )
    melted["Metrik"] = melted["Metrik"].map(
        {"noise_floor": "Noise Floor", "load": "Load"}
    )
    fig = px.line(
        melted,
        x="frequency_mhz",
        y="Wert",
        color="Metrik",
        facet_row="band",
        markers=True,
        labels={"frequency_mhz": "Frequency (MHz)"},
        title="Noisefloor und Load pro Frequenz",
    )
    fig.for_each_annotation(lambda annotation: annotation.update(text=annotation.text.split("=")[-1]))
    st.plotly_chart(fig, use_container_width=True)


def render_wlan_clients(stations: List[WifiStation]) -> None:
    st.subheader("WLAN Clients")
    if not stations:
        st.info("Keine WLAN-Clientliste gefunden.")
        return

    rows = []
    for station in stations:
        rows.append(
            {
                "MAC": station.mac,
                "Interface": station.if_name,
                "Connect State": station.connect_state,
                "RSSI": station.rssi,
                "Qualität": station.quality,
                "Verbindung": connection_quality_label(station.rssi, station.quality),
                "Rate RX": station.rate_rx,
                "Rate TX": station.rate_tx,
                "Rate RX Max": station.rate_rx_max,
                "Rate TX Max": station.rate_tx_max,
            }
        )
    df = pd.DataFrame(rows)
    connected_df = df[df["Connect State"] > 0]
    disconnected_df = df[df["Connect State"] <= 0]

    render_metric_rows(
        [
            ("Clients gesamt", format_count(len(df))),
            ("Verbunden", format_count(len(connected_df))),
            ("Nicht verbunden", format_count(len(disconnected_df))),
        ],
        columns=3,
    )

    st.markdown("**Verbunden**")
    if connected_df.empty:
        st.info("Keine verbundenen WLAN-Clients gefunden.")
    else:
        st.dataframe(connected_df, use_container_width=True)

    st.markdown("**Nicht verbunden**")
    if disconnected_df.empty:
        st.info("Keine nicht verbundenen WLAN-Clients gefunden.")
    else:
        st.dataframe(disconnected_df, use_container_width=True)

    chart_df = connected_df.copy()
    chart_df["RSSI"] = pd.to_numeric(chart_df["RSSI"], errors="coerce")
    chart_df = chart_df[chart_df["RSSI"].notna()]
    if not chart_df.empty:
        fig = px.bar(
            chart_df,
            x="MAC",
            y="RSSI",
            title="RSSI pro WLAN Client",
            labels={"RSSI": "RSSI (dBm)"},
        )
        st.plotly_chart(fig, use_container_width=True)


def render_wlan_radio_load(radio_loads: List[WifiRadioLoad]) -> None:
    st.subheader("WLAN Radio Load")
    if not radio_loads:
        st.info("Keine WLAN-Radio-Load-Daten gefunden.")
        return

    for index, radio in enumerate(radio_loads):
        with st.expander(format_radio_label(radio.radio_id)):
            if radio.error:
                st.warning(radio.error)
                continue
            if radio.dataframe.empty:
                st.info("Keine verwertbaren Radio-Load-Daten.")
                continue

            df = radio.dataframe
            fig = px.line(
                df,
                x="Sekunde",
                y=["Global Usage (%)", "Own TX Usage (%)"],
                labels={"value": "Auslastung (%)", "variable": "Serie"},
                title="Auslastung über Zeit",
            )
            st.plotly_chart(
                fig,
                use_container_width=True,
                key=f"wlan_radio_load_{radio.radio_id}_{index}",
            )


def render_lan_ports(ports: List[LanPort]) -> None:
    st.subheader("LAN/WAN Ports")
    if not ports:
        st.info("Keine LAN/WAN-Portinformationen gefunden.")
        return
    df = pd.DataFrame([port.__dict__ for port in ports])
    up_count = len(df[df["status"] == "up"])
    down_count = len(df[df["status"] == "down"])
    render_metric_rows(
        [
            ("Ports gesamt", format_count(len(df))),
            ("Ports aktiv", format_count(up_count)),
            ("Ports inaktiv", format_count(down_count)),
        ],
        columns=3,
    )
    fig = px.bar(
        df,
        x="port",
        y="status",
        color="status",
        title="Port-Status",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True)


def render_lan_clients(clients: List[NeighbourClient]) -> None:
    st.subheader("LAN Clients")
    lan_clients = [client for client in clients if client.connection_type == "LAN"]
    if not lan_clients:
        st.info("Keine LAN-Clients gefunden.")
        return

    rows = []
    for client in lan_clients:
        rows.append(
            {
                "MAC": client.mac,
                "Name": client.name or "k.A.",
                "IP": client.ip_address or "k.A.",
                "Interface": client.interface or "k.A.",
                "LAN-Port": client.lan_port or "k.A.",
                "Speed": client.speed or "k.A.",
                "Verbunden": "Ja" if client.is_online else "Nein",
            }
        )
    df = pd.DataFrame(rows)

    connected_df = df[df["Verbunden"] == "Ja"]
    disconnected_df = df[df["Verbunden"] == "Nein"]

    render_metric_rows(
        [
            ("Clients gesamt", format_count(len(df))),
            ("Verbunden", format_count(len(connected_df))),
            ("Nicht verbunden", format_count(len(disconnected_df))),
        ],
        columns=3,
    )

    st.markdown("**Verbunden**")
    if connected_df.empty:
        st.info("Keine verbundenen LAN-Clients gefunden.")
    else:
        st.dataframe(connected_df, use_container_width=True)

    st.markdown("**Nicht verbunden**")
    if disconnected_df.empty:
        st.info("Keine nicht verbundenen LAN-Clients gefunden.")
    else:
        st.dataframe(disconnected_df, use_container_width=True)


def render_telephony(accounts: List[TelephonyAccount]) -> None:
    st.subheader("Telefonie (VoIP)")
    if not accounts:
        st.info("Keine VoIP-Registrierungen gefunden.")
        return

    summary_rows = []
    for idx, account in enumerate(accounts):
        encrypted = account.transport.lower().startswith("tls") or bool(account.cipher)
        encryption_label = "Ja"
        if account.transport:
            encryption_label = f"Ja ({account.transport})" if encrypted else f"Nein ({account.transport})"
        summary_rows.append(
            {
                "Anbieter": account.provider,
                "Rufnummer": account.number,
                "Registriert": format_bool(account.registered),
                "Verschlüsselung": encryption_label,
                "SIP-Interface": account.sip_interface,
                "Port": account.port,
                "Erreichbarkeit %": account.reachability,
            }
        )

    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    for idx, account in enumerate(accounts):
        encrypted = account.transport.lower().startswith("tls") or bool(account.cipher)
        title = f"{account.number} ({account.provider})"
        with st.expander(title):
            col1, col2, col3 = st.columns(3)
            col1.metric("Registriert", format_bool(account.registered))
            col1.metric("Verschlüsselung", "Ja" if encrypted else "Nein")
            col1.metric("Cipher", account.cipher or "k.A.")

            col2.metric("RX", format_bytes(account.rx_bytes))
            col2.metric("TX", format_bytes(account.tx_bytes))
            col2.metric("Verlorene Pakete", format_count(account.lost_pkts))

            col3.metric("RX Pakete", format_count(account.rx_pkts))
            col3.metric("TX Pakete", format_count(account.tx_pkts))
            col3.metric("Call Time", account.total_call_time or "k.A.")

            call_rows = []
            if account.outgoing_attempted is not None:
                call_rows.append(
                    {
                        "Richtung": "Ausgehend",
                        "Versucht": account.outgoing_attempted,
                        "Angenommen": account.outgoing_answered,
                        "Verbunden": account.outgoing_connected,
                        "Fehlgeschlagen": account.outgoing_failed,
                    }
                )
            if account.incoming_received is not None:
                call_rows.append(
                    {
                        "Richtung": "Eingehend",
                        "Versucht": account.incoming_received,
                        "Angenommen": account.incoming_answered,
                        "Verbunden": account.incoming_connected,
                        "Fehlgeschlagen": account.incoming_failed,
                    }
                )
            if call_rows:
                call_df = pd.DataFrame(call_rows)
                st.dataframe(call_df, use_container_width=True)
                chart_df = call_df.melt(id_vars=["Richtung"], value_vars=["Versucht", "Angenommen", "Verbunden", "Fehlgeschlagen"])
                fig = px.bar(
                    chart_df,
                    x="Richtung",
                    y="value",
                    color="variable",
                    barmode="group",
                    title="Call-Übersicht",
                    labels={"value": "Anzahl"},
                )
                st.plotly_chart(fig, use_container_width=True, key=f"voip-call-chart-{idx}")

            if account.dropped_calls is not None:
                st.metric("Dropped Calls", format_count(account.dropped_calls))
            if account.loopback_connected is not None:
                st.metric(
                    "Direct Loopback (connected/failed)",
                    f"{format_count(account.loopback_connected)} / {format_count(account.loopback_failed)}",
                )


def render_internet_connection(connection: Optional[InternetConnection]) -> None:
    st.subheader("Internet")
    if not connection:
        st.info("Keine Internetdaten gefunden.")
        return

    info_columns = st.columns(3)
    info_columns[0].metric("Verbindung", connection.name)
    info_columns[1].metric("Zugangsart", connection.access_type)
    info_columns[2].metric("VLAN", connection.vlan or "Nicht aktiv")

    rows = [
        {
            "IP-Version": "IPv4",
            "IP-Adresse": connection.ipv4_address or "-",
            "DNS": ", ".join(connection.ipv4_dns) if connection.ipv4_dns else "-",
            "Masqadresse": connection.ipv4_masq or "-",
        },
        {
            "IP-Version": "IPv6",
            "IP-Adresse": connection.ipv6_address or "-",
            "DNS": ", ".join(connection.ipv6_dns) if connection.ipv6_dns else "-",
            "Masqadresse": connection.ipv6_masq or "-",
        },
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_port_forwardings(forwardings: List[PortForwarding]) -> None:
    st.subheader("Portfreigaben (IPv4)")
    if not forwardings:
        st.info("Keine IPv4-Portfreigaben gefunden.")
        return

    rows = []
    for entry in forwardings:
        rows.append(
            {
                "Dienst": entry.service,
                "Protokoll": entry.protocol,
                "Ziel": f"{entry.target_ip}:{entry.target_port}",
                "Öffentlich": f"{entry.public_ip}:{entry.public_port}",
                "Bezeichnung": entry.description or "-",
                "allow-only-from": entry.allow_only_from or "-",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_network_utilization(sections: List[AvmCounterSection]) -> None:
    st.subheader("Netzauslastung")
    if not sections:
        st.info("Keine AVM-Counter-RRD-Daten gefunden.")
        return

    summary = summarize_avm_counter_values(sections)
    if not summary["entries"]:
        st.info("Keine auswertbaren Netzauslastungswerte in den AVM-Counter-Daten gefunden.")
        return

    metric_cols = st.columns(5)
    metric_cols[0].metric("Messpunkte", format_count(summary["total_entries"]))
    metric_cols[1].metric("Stale Datenpunkte (>300s)", format_count(summary["stale_entries"]))
    metric_cols[2].metric("RX gesamt", format_bytes(summary["total_rx"]))
    metric_cols[3].metric("TX gesamt", format_bytes(summary["total_tx"]))
    metric_cols[4].metric("Gesamtverkehr", format_bytes(summary["total_traffic"]))

    utilization_cols = st.columns(2)
    rx_share = summary["rx_share_pct"]
    tx_share = summary["tx_share_pct"]
    utilization_cols[0].metric("RX-Anteil am Verkehr", f"{rx_share:.1f} %" if rx_share is not None else "-")
    utilization_cols[1].metric("TX-Anteil am Verkehr", f"{tx_share:.1f} %" if tx_share is not None else "-")
    st.caption("Netzauslastung in % = Anteil von RX/TX am insgesamt erfassten Verkehr (AVM-Counter).")

    top_categories = summary["top_categories"]
    if top_categories:
        category_df = pd.DataFrame(top_categories)
        category_df["Gesamtverkehr"] = category_df["RX gesamt"] + category_df["TX gesamt"]
        total_traffic = summary["total_traffic"]
        category_df["Auslastung (%)"] = (
            (category_df["Gesamtverkehr"] / total_traffic * 100).round(2) if total_traffic > 0 else 0.0
        )

        chart_df = category_df.nlargest(8, "Gesamtverkehr").copy()
        chart_df["RX"] = chart_df["RX gesamt"]
        chart_df["TX"] = chart_df["TX gesamt"]
        melted = chart_df.melt(
            id_vars=["Kategorie"],
            value_vars=["RX", "TX"],
            var_name="Richtung",
            value_name="Bytes",
        )
        fig = px.bar(
            melted,
            x="Kategorie",
            y="Bytes",
            color="Richtung",
            barmode="group",
            title="Top-Kategorien nach aggregiertem Verkehr",
            labels={"Bytes": "Bytes", "Kategorie": "Kategorie"},
        )
        st.plotly_chart(fig, use_container_width=True)

        display_df = category_df.drop(columns=["Gesamtverkehr"]).copy()
        display_df["RX gesamt"] = display_df["RX gesamt"].apply(format_bytes)
        display_df["TX gesamt"] = display_df["TX gesamt"].apply(format_bytes)
        st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_ar7_overview(ar7_overview: Ar7Overview) -> None:
    st.subheader("AR7-Konfiguration")
    if not ar7_overview.mode:
        st.info("Keine AR7-Konfigurationsdaten gefunden.")
        return

    mode_label = _mode_label(ar7_overview.mode)
    st.markdown(f"**Betriebsart:** {mode_label} (`{ar7_overview.mode}`)")

    def _render_dsl_ifaces() -> None:
        if not ar7_overview.dsl_ifaces:
            return
        st.markdown("**DSL-Interfaces (dslifaces)**")
        df = pd.DataFrame(
            [
                {
                    "Name": entry.name or "k.A.",
                    "Aktiv": entry.enabled or "k.A.",
                    "DSL Encapsulation": entry.dsl_encap or "k.A.",
                    "DSL Interface": entry.dsl_interface_name or "k.A.",
                    "Stackmode": entry.stackmode or "k.A.",
                    "Gewicht": entry.weight or "k.A.",
                    "VLAN Encapsulation": entry.vlan_encap or "k.A.",
                    "VLAN ID": entry.vlan_id or "k.A.",
                    "VLAN Priorität": entry.vlan_prio or "k.A.",
                }
                for entry in ar7_overview.dsl_ifaces
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

    if ar7_overview.mode == "dsldmode_full_bridge":
        st.markdown("**Bridge-Interfaces (brinterfaces)**")
        if not ar7_overview.bridge_interfaces:
            st.info("Keine Bridge-Interfaces gefunden.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "Name": entry.name or "k.A.",
                        "IP-Adresse": entry.ipaddr or "k.A.",
                        "Netzmaske": entry.netmask or "k.A.",
                        "DHCP Start": entry.dhcp_start or "k.A.",
                        "DHCP Ende": entry.dhcp_end or "k.A.",
                    }
                    for entry in ar7_overview.bridge_interfaces
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        _render_dsl_ifaces()
        return

    if ar7_overview.mode == "dsldmode_router":
        active_provider = ar7_overview.active_provider or "k.A."
        st.markdown(f"**Active Provider:** {active_provider}")

        st.markdown("**VCCS (VPI/VCI, Encapsulation)**")
        if not ar7_overview.vccs:
            st.info("Keine VCCS-Einträge gefunden.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "VPI": entry.vpi or "k.A.",
                        "VCI": entry.vci or "k.A.",
                        "DSL Encapsulation": entry.dsl_encap or "k.A.",
                    }
                    for entry in ar7_overview.vccs
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("**VLAN-Konfiguration**")
        if not ar7_overview.vlans:
            st.info("Keine VLAN-Einträge gefunden.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "VLAN ID": entry.vlanid or "k.A.",
                        "VLAN Priorität": entry.vlanprio or "k.A.",
                        "TOS": entry.tos or "k.A.",
                    }
                    for entry in ar7_overview.vlans
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        _render_dsl_ifaces()
        return

    _render_dsl_ifaces()
    st.info("AR7-Modus ist nicht ausgewertet.")



def _cidr_from_netmask(netmask: Optional[str]) -> str:
    if not netmask:
        return ""
    try:
        bits = sum(bin(int(octet)).count("1") for octet in netmask.split("."))
        return f"/{bits}"
    except ValueError:
        return ""


def render_network_settings(settings: Ar7NetworkSettings) -> None:
    st.subheader("Netzwerkeinstellungen")

    lan = settings.interfaces.get("lan")
    guest = settings.interfaces.get("guest")
    service = settings.interfaces.get("lan:0")

    def _network_line(interface: Optional[Ar7Interface], fallback: str = "k.A.") -> str:
        if not interface or not interface.ipaddr:
            return fallback
        return f"{interface.ipaddr}{_cidr_from_netmask(interface.netmask)}"

    dhcp_range = "k.A."
    if lan and lan.dhcp_start and lan.dhcp_end and lan.dhcp_start != "0.0.0.0" and lan.dhcp_end != "0.0.0.0":
        dhcp_range = f"{lan.dhcp_start} - {lan.dhcp_end}"

    dns_line = ", ".join(settings.dns_servers) if settings.dns_servers else "k.A."
    hidden_line = ", ".join(settings.hidden_menus) if settings.hidden_menus else "keine sichtbar"

    st.markdown(
        textwrap.dedent(
            f"""            <div class="network-settings-grid">
                <div class="network-settings-panel network-overview">
                    <h3>Netzwerkübersicht</h3>
                    <div class="network-card lan">
                        <div class="network-card-header">LAN Netzwerk</div>
                        <ul>
                            <li>{escape_html(_network_line(lan))}</li>
                            <li>DHCP: {escape_html(dhcp_range)}</li>
                            <li>DNS: {escape_html(dns_line)}</li>
                        </ul>
                    </div>
                    <div class="network-card guest">
                        <div class="network-card-header">Gastnetz</div>
                        <ul><li>{escape_html(_network_line(guest))}</li></ul>
                    </div>
                    <div class="network-card service">
                        <div class="network-card-header">Servicenetz</div>
                        <ul><li>{escape_html(_network_line(service))}</li></ul>
                    </div>
                </div>
                <div class="network-settings-panel network-info">
                    <h3>Internet &amp; WAN</h3>
                    <ul>
                        <li>Modus: <strong>{escape_html(_mode_label(settings.mode))}</strong></li>
                        <li>IPv4: <strong>{escape_html(_ipv4_label(settings.ipv4_mode))}</strong></li>
                        <li>IPv6: <strong>{escape_html(_ipv6_label(settings.ipv6_mode))}</strong></li>
                        <li>MTU: <strong>{escape_html(settings.mtu or 'k.A.')}</strong></li>
                        <li>WAN VLAN: <strong>{escape_html(_format_toggle_state(settings.wan_vlan))}</strong></li>
                        <li>TR-069: <strong>{escape_html(_format_toggle_state(settings.tr069))}</strong></li>
                        <li>SNMP auf WAN: <strong>{escape_html(_format_toggle_state(settings.snmp_wan))}</strong></li>
                    </ul>
                    <h3>Services &amp; Einstellungen</h3>
                    <ul>
                        <li>DynDNS: <strong>{escape_html(_format_toggle_state(settings.dyn_dns))}</strong></li>
                        <li>E-Mail Reports: <strong>{escape_html(_format_toggle_state(settings.email_reports))}</strong></li>
                        <li>Expertenmodus: <strong>{escape_html(_format_toggle_state(settings.expert_mode))}</strong></li>
                        <li>Versteckte Menüs: <strong>{escape_html(hidden_line)}</strong></li>
                    </ul>
                </div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )
def render_events(events: List[EventEntry]) -> None:
    st.subheader("Events")
    if not events:
        st.info("Keine Events gefunden.")
        return
    df = pd.DataFrame([event.__dict__ for event in events])
    st.dataframe(df, use_container_width=True)


def render_mesh_topology(mesh: MeshTopology) -> None:
    st.subheader("Mesh Topologie")
    if mesh.error:
        st.warning(mesh.error)
    if not mesh.nodes:
        st.info("Keine Mesh-Topologie gefunden.")
        return

    nodes_by_uid = {node.get("uid"): node for node in mesh.nodes if node.get("uid")}

    disconnected_client_uids = set()
    for uid, node in nodes_by_uid.items():
        role = (node.get("mesh_role") or "").lower()
        capabilities = set(node.get("device_capabilities") or [])
        is_infra = role in {"master", "slave"} or "ROUTER" in capabilities or "WLAN_ACCESS_POINT" in capabilities
        if not is_infra and not is_mesh_client_connected(node, mesh.links):
            disconnected_client_uids.add(uid)

    positions = build_mesh_positions(mesh, disconnected_client_uids)

    disconnected_clients_rows: List[dict] = []
    visual_nodes: List[dict] = []
    visible_uids: set[str] = set()

    for uid, (x, y) in positions.items():
        node = nodes_by_uid.get(uid, {})
        name = node.get("device_friendly_name") or node.get("device_name") or uid
        node_type = node.get("device_type") or "Gerät"
        role = (node.get("mesh_role") or "").lower()
        capabilities = set(node.get("device_capabilities") or [])
        is_infra = role in {"master", "slave"} or "ROUTER" in capabilities or "WLAN_ACCESS_POINT" in capabilities
        if role == "master":
            role_label = "Master"
        elif role == "slave":
            role_label = "Repeater"
        else:
            role_label = "Client"
        hover_lines = [
            f"<b>{escape_html(name)}</b>",
            f"Rolle: {role_label}",
            f"Typ: {escape_html(node_type)}",
            f"MAC: {escape_html(node.get('device_mac_address') or 'k.A.')}",
        ]
        if is_infra:
            visual_nodes.append(
                {
                    "uid": uid,
                    "name": name,
                    "role": role_label,
                    "type": "infra",
                    "x": x,
                    "y": y,
                }
            )
            visible_uids.add(uid)
        else:
            is_connected = uid not in disconnected_client_uids
            if is_connected:
                visual_nodes.append(
                    {
                        "uid": uid,
                        "name": name,
                        "role": role_label,
                        "type": "client",
                        "x": x,
                        "y": y,
                    }
                )
                visible_uids.add(uid)
            else:
                disconnected_clients_rows.append(
                    {
                        "Name": name,
                        "MAC": node.get("device_mac_address") or "k.A.",
                    }
                )

    visual_links: List[dict] = []
    for link in mesh.links:
        source = link.get("node_1_uid")
        target = link.get("node_2_uid")
        if source in visible_uids and target in visible_uids:
            visual_links.append({"source": source, "target": target})

    left_col, right_col = st.columns([1, 3])
    with left_col:
        st.markdown("**Nicht verbundene Clients**")
        if disconnected_clients_rows:
            st.dataframe(pd.DataFrame(disconnected_clients_rows), use_container_width=True, hide_index=True)
        else:
            st.success("Alle erkannten Clients sind aktuell verbunden.")

    with right_col:
        st.markdown("**Mesh-Ansicht**")
        if not visual_nodes:
            st.info("Keine verbundenen Geräte für die Topologie-Ansicht gefunden.")
        else:
            min_x = min(node["x"] for node in visual_nodes)
            max_x = max(node["x"] for node in visual_nodes)
            min_y = min(node["y"] for node in visual_nodes)
            max_y = max(node["y"] for node in visual_nodes)
            span_x = max(1.0, max_x - min_x)
            span_y = max(1.0, max_y - min_y)

            for node in visual_nodes:
                node["x"] = round(((node["x"] - min_x) / span_x) * 760 + 20, 2)
                node["y"] = round(((node["y"] - min_y) / span_y) * 400 + 20, 2)

            graph_payload = {"nodes": visual_nodes, "links": visual_links}
            graph_payload_b64 = base64.b64encode(json.dumps(graph_payload, ensure_ascii=False).encode("utf-8")).decode("ascii")

            components.html(
                f"""
                <div id="mesh-wrapper" style="border:1px solid #d7dbe2;border-radius:8px;background:#fbfcff;height:470px;position:relative;overflow:hidden;">
                    <svg id="mesh-lines" width="100%" height="100%" style="position:absolute;top:0;left:0;pointer-events:none;"></svg>
                </div>
                <script>
                    const payload = JSON.parse(atob('{graph_payload_b64}'));
                    const wrapper = document.getElementById('mesh-wrapper');
                    const svg = document.getElementById('mesh-lines');
                    const storageKey = 'support-data-view-mesh-layout-v1';
                    const saved = JSON.parse(localStorage.getItem(storageKey) || '{{}}');

                    const nodeMap = new Map();
                    const animatedNodes = [];
                    const fritzIcon = `
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style="margin-right:6px;vertical-align:middle;">
                            <rect x="2.5" y="8" width="19" height="10.5" rx="2.5" fill="rgba(255,255,255,0.25)"></rect>
                            <path d="M7 8c0-2.8 2.2-5 5-5s5 2.2 5 5" stroke="white" stroke-width="1.6" stroke-linecap="round"></path>
                            <path d="M9 8c0-1.7 1.3-3 3-3s3 1.3 3 3" stroke="white" stroke-width="1.6" stroke-linecap="round"></path>
                            <circle cx="8" cy="15" r="1.2" fill="white"></circle>
                            <circle cx="12" cy="15" r="1.2" fill="white"></circle>
                            <circle cx="16" cy="15" r="1.2" fill="white"></circle>
                        </svg>
                    `;

                    payload.nodes.forEach(node => {{
                        const div = document.createElement('div');
                        const persisted = saved[node.uid];
                        const nodeX = persisted && Number.isFinite(persisted.x) ? persisted.x : node.x;
                        const nodeY = persisted && Number.isFinite(persisted.y) ? persisted.y : node.y;
                        div.dataset.uid = node.uid;
                        div.dataset.type = node.type;
                        div.dataset.dragging = 'false';
                        div.style.position = 'absolute';
                        div.style.left = `${{nodeX}}px`;
                        div.style.top = `${{nodeY}}px`;
                        div.style.cursor = 'grab';
                        div.style.userSelect = 'none';
                        div.style.padding = node.type === 'infra' ? '8px 10px' : '6px 9px';
                        div.style.borderRadius = '10px';
                        div.style.fontSize = '12px';
                        div.style.fontWeight = '600';
                        div.style.boxShadow = '0 2px 8px rgba(0,0,0,0.10)';
                        div.style.border = '1px solid #ffffff';
                        div.style.background = node.type === 'infra' ? '#1f77b4' : '#2ca02c';
                        div.style.color = '#ffffff';
                        if (node.type === 'infra') {{
                            div.innerHTML = `${{fritzIcon}}<span>${{node.name}}</span><span style="opacity:0.85;margin-left:6px;">${{node.role}}</span>`;
                        }} else {{
                            div.innerText = `${{node.name}}`;
                        }}
                        wrapper.appendChild(div);
                        nodeMap.set(node.uid, div);
                        animatedNodes.push({{
                            uid: node.uid,
                            div,
                            phase: Math.random() * Math.PI * 2,
                            drift: node.type === 'infra' ? 2.5 : 4.0,
                        }});
                    }});

                    const drawLinks = () => {{
                        svg.innerHTML = '';
                        const wrapperRect = wrapper.getBoundingClientRect();
                        payload.links.forEach(link => {{
                            const source = nodeMap.get(link.source);
                            const target = nodeMap.get(link.target);
                            if (!source || !target) return;
                            const sourceRect = source.getBoundingClientRect();
                            const targetRect = target.getBoundingClientRect();
                            const x1 = sourceRect.left - wrapperRect.left + sourceRect.width / 2;
                            const y1 = sourceRect.top - wrapperRect.top + sourceRect.height / 2;
                            const x2 = targetRect.left - wrapperRect.left + targetRect.width / 2;
                            const y2 = targetRect.top - wrapperRect.top + targetRect.height / 2;
                            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                            line.setAttribute('x1', x1);
                            line.setAttribute('y1', y1);
                            line.setAttribute('x2', x2);
                            line.setAttribute('y2', y2);
                            line.setAttribute('stroke', '#8b8f97');
                            line.setAttribute('stroke-width', '2');
                            svg.appendChild(line);
                        }});
                    }};

                    nodeMap.forEach((nodeDiv, uid) => {{
                        let startX = 0;
                        let startY = 0;
                        let dragging = false;

                        const onPointerMove = (event) => {{
                            if (!dragging) return;
                            const deltaX = event.clientX - startX;
                            const deltaY = event.clientY - startY;
                            const currentX = parseFloat(nodeDiv.style.left) + deltaX;
                            const currentY = parseFloat(nodeDiv.style.top) + deltaY;
                            const maxX = wrapper.clientWidth - nodeDiv.offsetWidth;
                            const maxY = wrapper.clientHeight - nodeDiv.offsetHeight;
                            nodeDiv.style.left = `${{Math.max(0, Math.min(maxX, currentX))}}px`;
                            nodeDiv.style.top = `${{Math.max(0, Math.min(maxY, currentY))}}px`;
                            startX = event.clientX;
                            startY = event.clientY;
                            drawLinks();
                        }};

                        const onPointerUp = () => {{
                            if (!dragging) return;
                            dragging = false;
                            nodeDiv.style.cursor = 'grab';
                            nodeDiv.dataset.dragging = 'false';
                            const latest = JSON.parse(localStorage.getItem(storageKey) || '{{}}');
                            latest[uid] = {{ x: parseFloat(nodeDiv.style.left), y: parseFloat(nodeDiv.style.top) }};
                            localStorage.setItem(storageKey, JSON.stringify(latest));
                            window.removeEventListener('pointermove', onPointerMove);
                            window.removeEventListener('pointerup', onPointerUp);
                        }};

                        nodeDiv.addEventListener('pointerdown', (event) => {{
                            dragging = true;
                            startX = event.clientX;
                            startY = event.clientY;
                            nodeDiv.style.cursor = 'grabbing';
                            nodeDiv.dataset.dragging = 'true';
                            nodeDiv.style.transform = 'translate(0px, 0px)';
                            window.addEventListener('pointermove', onPointerMove);
                            window.addEventListener('pointerup', onPointerUp);
                        }});
                    }});

                    const animateMesh = (time) => {{
                        animatedNodes.forEach(item => {{
                            if (item.div.dataset.dragging === 'true') return;
                            const offsetX = Math.sin(time / 1600 + item.phase) * item.drift;
                            const offsetY = Math.cos(time / 1900 + item.phase) * item.drift;
                            item.div.style.transform = `translate(${{offsetX}}px, ${{offsetY}}px)`;
                        }});
                        drawLinks();
                        window.requestAnimationFrame(animateMesh);
                    }};

                    window.requestAnimationFrame(() => {{
                        window.requestAnimationFrame(drawLinks);
                    }});

                    window.requestAnimationFrame(animateMesh);
                    window.addEventListener('resize', () => drawLinks());
                </script>
                """,
                height=480,
            )





def render_dect_devices(devices: List[DectDevice]) -> None:
    st.subheader("DECTDeviceInfo")
    if not devices:
        st.info("Keine DECT-Handgeräte gefunden.")
        return

    rows = []
    for device in devices:
        all_rssi = device.hg_rssi_values or device.rssi_values
        avg_rssi = sum(all_rssi) / len(all_rssi) if all_rssi else None
        rows.append(
            {
                "Name": device.name,
                "HGID": device.hgid,
                "Model": device.model or "k.A.",
                "IPUI": device.ipui or "k.A.",
                "Codec": device.curr_codec or "k.A.",
                "Ø RSSI (dBm)": round(avg_rssi, 1) if avg_rssi is not None else "k.A.",
                "Verbindung": assess_dect_rssi(avg_rssi),
                "FW": device.fw_version or "k.A.",
                "NoEmission": device.no_emission if device.no_emission is not None else "k.A.",
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def parse_ratelimiter_runtime(text: str) -> List[RatelimiterRuntimeEntry]:
    scopes = {
        "ratelimitlanset:": "LAN",
        "ratelimitwanset:": "WAN",
        "ratelimitearlylanset:": "LAN (Early)",
    }
    line_pattern = re.compile(
        r"^\s*\d+:\s*(?P<rule>.+?)\s*\(ratelimit\)\s*=>\s*\d+\s*\(#\s*(?P<hits>\d+),\s*blocked\s*#\s*(?P<blocked>\d+)\)\s*pakets\s*(?P<packets>\d+)\s*interval\s*(?P<interval>\d+)\s*seconds",
        re.IGNORECASE,
    )

    current_scope = "Unbekannt"
    rows: List[RatelimiterRuntimeEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in scopes:
            current_scope = scopes[stripped]
            continue
        match = line_pattern.match(line)
        if not match:
            continue
        rows.append(
            RatelimiterRuntimeEntry(
                scope=current_scope,
                rule=match.group("rule"),
                packets=int(match.group("packets")),
                interval_seconds=int(match.group("interval")),
                hits=int(match.group("hits")),
                blocked=int(match.group("blocked")),
            )
        )
    return rows


def parse_ratelimiter_config(text: str) -> List[RatelimiterConfigEntry]:
    entry_pattern = re.compile(
        r"enabled\s*=\s*(?P<enabled>yes|no);"
        r"\s*name\s*=\s*\"(?P<name>[^\"]+)\";"
        r".*?iface\s*=\s*(?P<iface>[\w_]+);"
        r"\s*rule\s*=\s*\"(?P<rule>[^\"]+)\";"
        r"\s*packets\s*=\s*(?P<packets>\d+);"
        r"\s*interval\s*=\s*(?P<interval>[^;]+);"
        r"\s*early\s*=\s*(?P<early>\d+);",
        re.DOTALL | re.IGNORECASE,
    )

    rows: List[RatelimiterConfigEntry] = []
    for match in entry_pattern.finditer(text):
        rows.append(
            RatelimiterConfigEntry(
                enabled=match.group("enabled").lower() == "yes",
                name=match.group("name"),
                iface=match.group("iface"),
                rule=match.group("rule"),
                packets=int(match.group("packets")),
                interval=match.group("interval").strip(),
                early=int(match.group("early")),
            )
        )
    return rows



def parse_hardware_ratelimiter_sessions(text: str) -> List[HardwareRatelimiterSession]:
    sessions: List[HardwareRatelimiterSession] = []
    current: Dict[str, str] = {}

    def flush() -> None:
        if not current:
            return
        if current.get("accelerator", "").strip().lower() != "ratelimiter":
            current.clear()
            return

        source_ip = current.get("source ipv4")
        destination_ip = current.get("destination ipv4")
        if not source_ip or not destination_ip:
            current.clear()
            return

        def parse_int(field: str) -> int:
            value = current.get(field)
            if not value:
                return 0
            match = re.search(r"\d+", value)
            return int(match.group(0)) if match else 0

        def parse_port(field: str) -> Optional[int]:
            value = current.get(field)
            if not value:
                return None
            match = re.search(r"\d+", value)
            return int(match.group(0)) if match else None

        catchall = current.get("covered by catchall", "").strip().lower() == "yes"
        sessions.append(
            HardwareRatelimiterSession(
                source_ip=source_ip.strip(),
                destination_ip=destination_ip.strip(),
                source_port=parse_port("source port"),
                destination_port=parse_port("destination port"),
                matched_packets=parse_int("matched packets"),
                matched_bytes=parse_int("matched bytes"),
                rule_type=current.get("rule type", "").strip() or None,
                catchall=catchall,
            )
        )
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key == "accelerator" and current:
            flush()
        current[key] = value.strip()

    flush()
    return sessions


def analyze_hardware_ratelimiter_sessions(sessions: List[HardwareRatelimiterSession]) -> dict:
    unique_sources = sorted({session.source_ip for session in sessions})
    limited_ports = sorted(
        {
            session.destination_port
            for session in sessions
            if session.destination_port is not None
        }
    )
    total_packets = sum(session.matched_packets for session in sessions)
    total_bytes = sum(session.matched_bytes for session in sessions)

    assessment: List[str] = []
    if sessions:
        assessment.append("Hardware Rate Limiting aktiv, normaler Schutzmechanismus.")

    if any(session.catchall for session in sessions):
        assessment.append("Catch-all Rate-Limiter-Regel aktiv.")

    if any(port in {443, 499} for port in limited_ports):
        assessment.append("Management-Port wird durch Rate-Limiter geschützt.")

    if len(unique_sources) >= 8:
        assessment.append("Auffälliger Traffic von externer IP erkannt (viele Source-Adressen).")

    if total_packets >= 100_000:
        assessment.append("Sehr hohe Paketanzahl erkannt, möglicher Flood.")
    elif sessions:
        assessment.append("Kein Hinweis auf Flood oder Überlastung.")

    external_sources = 0
    for source in unique_sources:
        try:
            addr = ipaddress.ip_address(source)
            if not (addr.is_private or addr.is_loopback or addr.is_link_local):
                external_sources += 1
        except ValueError:
            continue
    if external_sources >= 3:
        assessment.append("Mehrere externe Source-IPs beteiligt, mögliches Scan-Muster.")

    return {
        "sessions": [
            {
                "source_ip": session.source_ip,
                "destination_ip": session.destination_ip,
                "source_port": session.source_port,
                "destination_port": session.destination_port,
                "matched_packets": session.matched_packets,
                "matched_bytes": session.matched_bytes,
                "rule_type": session.rule_type,
                "catchall": session.catchall,
            }
            for session in sessions
        ],
        "summary": {
            "total_sessions": len(sessions),
            "total_packets": total_packets,
            "total_bytes": total_bytes,
            "unique_sources": unique_sources,
            "limited_ports": limited_ports,
        },
        "assessment": assessment,
    }


def parse_drop_indicators(text: str) -> Dict[str, int]:
    patterns = {
        "icmp_rate_limit": r"icmp\s*rate\s*limit",
        "echo_request_rate_limit": r"echo\s*request",
        "frag_freemem": r"frag\s*:\s*freemem",
        "tcp_checksum_wrong": r"tcp\s*checksum\s*wrong",
        "reject_not_possible": r"reject\s*not\s*possible",
    }
    counters = {key: 0 for key in patterns}
    for line in text.splitlines():
        lowered = line.lower()
        for key, pattern in patterns.items():
            if not re.search(pattern, lowered):
                continue
            numbers = re.findall(r"\d+", line)
            counters[key] += int(numbers[-1]) if numbers else 1
    return {key: value for key, value in counters.items() if value > 0}


def parse_offload_indicators(text: str) -> Dict[str, int]:
    patterns = {
        "cpu_fallback": r"fallback\s+to\s+cpu|cpu\s+fallback",
        "session_evictions": r"evictions?",
        "session_flushes": r"flush(es)?",
        "overflow": r"overflow",
        "not_synchronizable": r"not\s+synchroniz",
    }
    indicators = {key: 0 for key in patterns}
    lowered = text.lower()
    for key, pattern in patterns.items():
        indicators[key] = len(re.findall(pattern, lowered))
    return {key: value for key, value in indicators.items() if value > 0}



PPE_MODULE_NAMES = [
    "qca_nss_ppe",
    "qca_nss_ppe_qdisc",
    "qca_nss_ppe_ds",
    "qca_nss_ppe_lag",
    "qca_nss_ppe_bridge_mgr",
    "qca_nss_ppe_pppoe_mgr",
    "qca_nss_ppe_rule",
    "qca_nss_ppe_vlan",
    "qca_nss_ppe_vp",
    "qca_nss_dp",
    "qca_ssdk",
    "offload_pa",
    "offload_util",
]

PPE_COUNTER_SEVERITY = {
    "no free hws": "critical",
    "offload failed": "warning",
    "invalid egress mac": "warning",
    "add/remove vlan dev to ppe err": "critical",
    "add/remove pppoe dev to ppe err": "critical",
    "add/remove mac dev to ppe err": "critical",
    "ppe offload collision": "warning",
    "dev not registered in ppe": "warning",
    "fallback offloads": "info",
}

PPE_INFO_COUNTERS = {
    "flow flushed by hw",
    "flow flushed by sw",
    "created vlan devs",
    "created mac devs",
    "created pppoe devs",
    "max nb of same tuple offload",
}


def _extract_section_containing(text: str, needle: str) -> str:
    index = text.lower().find(needle.lower())
    if index == -1:
        return ""
    start_index = text.rfind("##### BEGIN SECTION", 0, index)
    if start_index == -1:
        start_index = max(0, index - 2000)
    end_index = text.find("##### END SECTION", index)
    if end_index == -1:
        end_index = min(len(text), index + 12000)
    return text[start_index:end_index]


def _extract_ppe_raw_blocks(text: str) -> Dict[str, str]:
    block_specs = {
        "brief": "##### BEGIN SECTION brief",
        "interfaces": "##### BEGIN SECTION interfaces",
        "synced_sessions": "##### BEGIN SECTION synced_sessions",
        "caps": "##### BEGIN SECTION caps",
        "ppe_if_map": "##### BEGIN SECTION ppe_if_map",
    }
    blocks = {name: extract_section_by_prefix(text, marker) for name, marker in block_specs.items()}
    fallbacks = {
        "brief": "HWPA ppe summary",
        "interfaces": "PPE device only",
        "synced_sessions": "HWPA synced sessions",
        "caps": "MAX HWPA PPE Sessions",
        "ppe_if_map": "ppe_if_map",
    }
    for name, needle in fallbacks.items():
        if not blocks[name]:
            blocks[name] = _extract_section_containing(text, needle)
    return blocks


def _parse_ppe_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = str(value).strip().rstrip(",;")
    if not value or value.upper() in {"NULL", "N/A", "-"}:
        return None
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(value)
    except ValueError:
        match = re.search(r"-?\d+", value)
        return int(match.group(0)) if match else None


def _classify_ppe_interface(name: str, iface_type: str = "") -> str:
    lower = name.lower()
    type_lower = iface_type.lower()
    if "pppoe" in type_lower or re.search(r"\.p\d+$", lower):
        return "PPPoE"
    if "vlan" in type_lower or re.search(r"\.v\d+(?:\.|$)", lower):
        return "VLAN"
    if "bridge" in type_lower or lower in {"lan", "guest"} or lower.startswith("br"):
        return "BRIDGE"
    if "lag" in type_lower or lower.startswith("bond") or lower.startswith("mld_"):
        return "LAG"
    if lower.startswith("ath") or "virtual" in type_lower or lower.startswith("wlan"):
        return "VIRTUAL/WLAN"
    if "physical" in type_lower or lower == "wan" or re.match(r"^(eth|ptm|dsl|adsl|wanmodem)\d*", lower):
        return "PHYSICAL"
    return "UNKNOWN"


def _describe_ppe_role(name: str, category: str) -> str:
    lower = name.lower()
    if lower == "wan":
        return "WAN physisch"
    if re.match(r"^wan\.v\d+\.p\d+$", lower):
        return "PPPoE Session auf WAN-VLAN"
    if re.match(r"^wan\.v\d+$", lower):
        return "WAN VLAN"
    if lower == "lan":
        return "LAN Bridge"
    if lower == "guest":
        return "Guest Bridge"
    if re.match(r"^eth\d+$", lower):
        return "LAN Port"
    if re.match(r"^ath\d+$", lower):
        return "WLAN Interface"
    if lower.startswith("bond"):
        return "LAG/Bonding"
    if lower in {"dsl", "adsl", "ptm0"} or lower.startswith(("dsl", "adsl")):
        return "DSL Kontext"
    if category == "VLAN":
        return "VLAN Device"
    if category == "PPPoE":
        return "PPPoE Device"
    return category.title() if category != "UNKNOWN" else "Unbekannt"


def _infer_base_from_name(name: str) -> Optional[str]:
    if re.search(r"\.p\d+$", name):
        return re.sub(r"\.p\d+$", "", name)
    if re.search(r"\.v\d+$", name):
        return name.split(".v", 1)[0]
    return None


def parse_ppe_if_map(section: str) -> List[dict]:
    entries: Dict[str, dict] = {}
    for line in section.splitlines():
        match = re.match(r"\s*Interface\.(\d+)\.([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if not match:
            continue
        index, key, value = match.groups()
        entries.setdefault(index, {})[key] = value

    rows: List[dict] = []
    number_to_name: Dict[int, str] = {}
    for entry in entries.values():
        iface_number = _parse_ppe_int(entry.get("iface_number"))
        name = entry.get("netdev_name", "")
        if iface_number is not None and name:
            number_to_name[iface_number] = name

    for entry in sorted(entries.values(), key=lambda item: _parse_ppe_int(item.get("iface_number")) or 0):
        name = entry.get("netdev_name", "")
        iface_type = entry.get("iface_type", "")
        parent_number = _parse_ppe_int(entry.get("parent_iface_number"))
        parent_name = number_to_name.get(parent_number) if parent_number is not None else None
        base_device = entry.get("base_dev") or entry.get("base_device") or _infer_base_from_name(name) or parent_name
        category = _classify_ppe_interface(name, iface_type)
        rows.append(
            {
                "ppe_index": _parse_ppe_int(entry.get("iface_number")),
                "device_type": iface_type or category,
                "interface_name": name,
                "port": _parse_ppe_int(entry.get("port_number")),
                "parent": parent_name or entry.get("parent_iface_number"),
                "base_device": base_device,
                "l3_interface": _parse_ppe_int(entry.get("l3_if_number")),
                "vsi": _parse_ppe_int(entry.get("vsi_number")),
                "category": category,
                "role": _describe_ppe_role(name, category),
            }
        )
    return rows


def parse_hwpa_interfaces(section: str) -> List[dict]:
    rows: List[dict] = []
    in_table = False
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        if stripped.startswith("Netdev") and "ppe_ifidx" in stripped:
            in_table = True
            continue
        if not in_table or stripped.startswith("#####") or stripped.startswith("PPE device only"):
            continue
        parts = stripped.split()
        if len(parts) < 8:
            continue
        name = parts[0]
        ppe_ifidx = _parse_ppe_int(parts[3])
        productive = _is_expected_ppe_netdev(name)
        if ppe_ifidx is not None and ppe_ifidx >= 0:
            status = "PPE gemappt"
            severity = "ok"
        elif productive:
            status = "Produktives Interface ohne PPE-Zuordnung"
            severity = "warning"
        else:
            status = "Nicht gemappt (unauffällig)"
            severity = "neutral"
        rows.append(
            {
                "netdev": name,
                "type": parts[1],
                "avm_pid": _parse_ppe_int(parts[2]),
                "ppe_ifidx": ppe_ifidx,
                "ppe_port": _parse_ppe_int(parts[4]),
                "rfs": parts[5],
                "hwpa_type": parts[7],
                "status": status,
                "severity": severity,
            }
        )
    return rows


def _is_expected_ppe_netdev(name: str) -> bool:
    lower = name.lower()
    ignored = ("lo", "sit", "ip6tnl", "xfrm", "trace", "wifi", "soc", "miireg", "ing", "mld-wifi")
    if lower.startswith(ignored):
        return False
    return bool(
        lower in {"wan", "lan", "guest"}
        or re.match(r"^(eth|ath)\d+$", lower)
        or re.search(r"\.v\d+(?:\.|$)", lower)
        or re.search(r"\.p\d+$", lower)
    )


def parse_ppe_device_only(section: str) -> List[dict]:
    marker_index = section.lower().find("ppe device only")
    if marker_index == -1:
        return []
    rows: List[dict] = []
    lines = section[marker_index:].splitlines()[2:]
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#####"):
            break
        parts = stripped.split()
        if len(parts) < 6:
            continue
        name = parts[0]
        mac = parts[6] if len(parts) >= 7 else ""
        category = _classify_ppe_interface(name, parts[2])
        rows.append(
            {
                "name": name,
                "ppe_port": _parse_ppe_int(parts[1]),
                "type": parts[2],
                "mtu": _parse_ppe_int(parts[3]),
                "base_device": None if parts[4] in {"-", "NULL"} else parts[4],
                "refs": _parse_ppe_int(parts[5]),
                "mac": mac,
                "category": category,
            }
        )
    return rows


def parse_common_ppe_offload_counters(section: str) -> List[dict]:
    start = section.lower().find("common ppe offload counter")
    if start == -1:
        return []
    chunk = section[start:]
    end_match = re.search(r"\n\s*(Accelerator state|Counter per accelerator|Offload counter per pid type)\s*:", chunk, re.IGNORECASE)
    if end_match:
        chunk = chunk[: end_match.start()]
    counters: List[dict] = []
    for line in chunk.splitlines()[1:]:
        match = re.match(r"\s*([^:]+?)\s*:\s*(-?\d+)\s*$", line)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group(1).strip().lower())
        value = int(match.group(2))
        severity = "neutral"
        if value > 0:
            severity = PPE_COUNTER_SEVERITY.get(name, "info" if name in PPE_INFO_COUNTERS else "neutral")
            if name == "ppe offload collision" and value < 10:
                severity = "info"
        counters.append({"counter": name, "value": value, "severity": severity})
    return counters


def parse_ppe_summary_and_state(section: str, caps_section: str) -> dict:
    used_match = re.search(r"used hws\s+(\d+)\s*/\s*(\d+)", section, re.IGNORECASE)
    free_match = re.search(r"free hws\s+(\d+)\s*/\s*(\d+)", section, re.IGNORECASE)
    max_match = re.search(r"MAX HWPA PPE Sessions\s*:\s*(\d+)", caps_section, re.IGNORECASE)
    state = {"ipv4": None, "ipv6": None, "ratelimiter": None}
    state_match = re.search(r"Accelerator state:\s*(.*?)(?:\n\s*\n|\nCounter per accelerator:|\Z)", section, re.IGNORECASE | re.DOTALL)
    if state_match:
        for key, value in re.findall(r"^\s*(ratelimiter|ipv6|ipv4)\s*:\s*(\w+)", state_match.group(1), re.IGNORECASE | re.MULTILINE):
            state[key.lower()] = value.lower()
    return {
        "used_hws": int(used_match.group(1)) if used_match else None,
        "free_hws": int(free_match.group(1)) if free_match else None,
        "max_hws": int(used_match.group(2)) if used_match else (int(free_match.group(2)) if free_match else (int(max_match.group(1)) if max_match else None)),
        "accelerator_state": state,
    }


def parse_ppe_sessions(section: str) -> dict:
    sessions: List[dict] = []
    blocks = re.split(r"(?=^HWS:\s*)", section, flags=re.MULTILINE)
    for block in blocks:
        if not block.strip().startswith("HWS:"):
            continue
        session = {"raw": block.strip()}
        hws_match = re.search(r"^HWS:\s*(\S+)", block, re.MULTILINE)
        accelerator_match = re.search(r"^accelerator:\s*(.+)$", block, re.MULTILINE | re.IGNORECASE)
        session["hws"] = hws_match.group(1) if hws_match else ""
        session["accelerator"] = accelerator_match.group(1).strip() if accelerator_match else "sonstige"
        for label, key in [
            ("source IPv4", "source_ip"),
            ("destination IPv4", "destination_ip"),
            ("source IPv6", "source_ip"),
            ("destination IPv6", "destination_ip"),
            ("source port", "source_port"),
            ("destination port", "destination_port"),
            ("protocol", "protocol"),
        ]:
            match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+)$", block, re.MULTILINE | re.IGNORECASE)
            if match:
                session[key] = match.group(1).strip()
        sessions.append(session)
    by_type = {"ratelimiter": 0, "ipv4": 0, "ipv6": 0, "sonstige": 0}
    for session in sessions:
        accelerator = str(session.get("accelerator", "")).lower()
        if "ratelimiter" in accelerator:
            by_type["ratelimiter"] += 1
        elif "ipv4" in accelerator:
            by_type["ipv4"] += 1
        elif "ipv6" in accelerator:
            by_type["ipv6"] += 1
        else:
            by_type["sonstige"] += 1
    return {"total": len(sessions), "by_type": by_type, "sessions": sessions}


def _hex_and_decimal(value: str) -> tuple[str, Optional[int]]:
    parsed = _parse_ppe_int(value)
    return (value, parsed)


def parse_ppe_mtu_mru(text: str) -> List[dict]:
    rows: List[dict] = []
    patterns = [
        r"port\s*(\d+)\D+mtu\s*[:=]?\s*(0x[0-9a-f]+|\d+)\D+mru\s*[:=]?\s*(0x[0-9a-f]+|\d+)",
        r"(\d+)\s+(0x[0-9a-f]+|\d+)\s+(0x[0-9a-f]+|\d+)",
    ]
    candidate_lines = [line.strip() for line in text.splitlines() if re.search(r"\b(MTU|MRU)\b", line, re.IGNORECASE)]
    for line in candidate_lines:
        if not re.search(r"\bport\b", line, re.IGNORECASE) and not re.match(r"^\d+\s+", line):
            continue
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if not match:
                continue
            port, mtu_raw, mru_raw = match.groups()[:3]
            mtu_hex, mtu_dec = _hex_and_decimal(mtu_raw)
            mru_hex, mru_dec = _hex_and_decimal(mru_raw)
            assessment = "Normal" if mtu_dec == 1500 and mru_dec == 1500 else "Hinweis"
            if mtu_dec == 1508 or mru_dec == 1508:
                assessment = "VLAN/PPPoE/RFC4638-Kontext möglich"
            elif (mtu_dec is not None and mtu_dec < 1400) or (mru_dec is not None and mru_dec < 1400):
                assessment = "Ungewöhnlich niedrig"
            elif (mtu_dec is not None and mtu_dec > 9000) or (mru_dec is not None and mru_dec > 9000):
                assessment = "Ungewöhnlich hoch"
            rows.append({"port": int(port), "mtu_hex": mtu_hex, "mtu_decimal": mtu_dec, "mru_hex": mru_hex, "mru_decimal": mru_dec, "assessment": assessment})
            break
    return rows


def parse_ppe_flow_control(text: str) -> List[dict]:
    rows: List[dict] = []
    for line in text.splitlines():
        if not re.search(r"flow[- ]?control|flow control|Illegal value", line, re.IGNORECASE):
            continue
        port_match = re.search(r"port\s*(\d+)", line, re.IGNORECASE)
        if not port_match:
            continue
        status_match = re.search(r"(?:status|flow[- ]?control)\s*[:=]?\s*([A-Za-z _-]+|Illegal value)", line, re.IGNORECASE)
        status = status_match.group(1).strip() if status_match else line.strip()
        assessment = "Hinweis: Illegal value" if "illegal value" in line.lower() else "Normal / informativ"
        rows.append({"port": int(port_match.group(1)), "status": status, "assessment": assessment})
    return rows


def parse_ppe_portshaper(text: str, ppe_devices: List[dict]) -> List[dict]:
    rows: List[dict] = []
    port_to_name = {row.get("port"): row.get("interface_name") for row in ppe_devices if row.get("port") is not None}
    for line in text.splitlines():
        if not re.search(r"port\s*shaper|portshaper", line, re.IGNORECASE):
            continue
        port_match = re.search(r"port\s*(\d+)", line, re.IGNORECASE)
        if not port_match:
            continue
        port = int(port_match.group(1))
        active = bool(re.search(r"\b(enable|enabled|active|on|yes)\b", line, re.IGNORECASE)) and not bool(re.search(r"\b(disable|disabled|off|no)\b", line, re.IGNORECASE))
        cir_match = re.search(r"\bCIR\s*[:=]?\s*(0x[0-9a-f]+|\d+)", line, re.IGNORECASE)
        cbs_match = re.search(r"\bCBS\s*[:=]?\s*(0x[0-9a-f]+|\d+)", line, re.IGNORECASE)
        frame_match = re.search(r"frame(?: mode)?\s*[:=]?\s*([A-Za-z0-9_-]+)", line, re.IGNORECASE)
        cir_raw = cir_match.group(1) if cir_match else ""
        cbs_raw = cbs_match.group(1) if cbs_match else ""
        iface = port_to_name.get(port, "")
        is_wan = iface.lower().startswith("wan") if iface else False
        assessment = "WAN-Portshaper aktiv" if active and is_wan else ("Aktiv" if active else "Inaktiv")
        rows.append(
            {
                "port": port,
                "interface": iface,
                "active": active,
                "cir_hex": cir_raw,
                "cir_decimal": _parse_ppe_int(cir_raw),
                "cbs_hex": cbs_raw,
                "cbs_decimal": _parse_ppe_int(cbs_raw),
                "frame_mode": frame_match.group(1) if frame_match else "",
                "assessment": assessment,
            }
        )
    return rows


def parse_ppe_kernel_modules(text: str) -> List[dict]:
    found: Dict[str, dict] = {}
    module_pattern = re.compile(r"^([A-Za-z0-9_]+)\s+(\d+)\s+(\S+)(?:\s+(.+))?$", re.MULTILINE)
    for match in module_pattern.finditer(text):
        name, size, used_count, used_by = match.groups()
        if name in PPE_MODULE_NAMES:
            found[name] = {"module": name, "size": int(size), "used_by": (used_by or used_count).strip(), "detected": True}
    return [found.get(name, {"module": name, "size": None, "used_by": "", "detected": False}) for name in PPE_MODULE_NAMES]


def build_ppe_device_tree(devices: List[dict]) -> List[str]:
    names = {row.get("interface_name") for row in devices if row.get("interface_name")}
    children: Dict[str, List[str]] = {name: [] for name in names}
    for row in devices:
        name = row.get("interface_name")
        parent = row.get("base_device") or row.get("parent")
        if name and parent in names and parent != name:
            children.setdefault(parent, []).append(name)
    child_names = {child for values in children.values() for child in values}
    roots = sorted(names - child_names)

    lines: List[str] = []

    def walk(node: str, prefix: str = "") -> None:
        lines.append(f"{prefix}{node}")
        node_children = sorted(children.get(node, []))
        for idx, child in enumerate(node_children):
            branch = "└─ " if idx == len(node_children) - 1 else "├─ "
            walk(child, prefix + branch)

    for root in roots:
        walk(root)
    return lines


def _severity_rank(severity: str) -> int:
    return {"ok": 0, "neutral": 0, "info": 1, "warning": 2, "critical": 3}.get(severity, 0)


def _severity_label(severity: str) -> str:
    return {"ok": "OK", "neutral": "OK", "info": "Hinweis", "warning": "Warnung", "critical": "Kritisch"}.get(severity, severity)


def _analyze_ppe_diagnosis(data: dict) -> dict:
    findings: List[dict] = []
    severity = "ok"
    modules = data["modules"]
    qca_nss_ppe_loaded = any(row["module"] == "qca_nss_ppe" and row["detected"] for row in modules)
    ppe_submodules_loaded = any(row["module"].startswith("qca_nss_ppe_") and row["detected"] for row in modules)

    if data["ppe_detected"] and not data["sessions"].get("total"):
        findings.append({"severity": "info", "message": "PPE erkannt, aber keine aktiven HWPA/PPE-Sessions gefunden."})
    if qca_nss_ppe_loaded and not data["raw_blocks"].get("brief"):
        findings.append({"severity": "critical", "message": "qca_nss_ppe ist geladen, aber der HWPA/PPE-Brief-Block fehlt."})
    if qca_nss_ppe_loaded and not data["raw_blocks"].get("ppe_if_map"):
        findings.append({"severity": "critical", "message": "qca_nss_ppe ist geladen, aber ppe_if_map fehlt."})
    if ppe_submodules_loaded and not qca_nss_ppe_loaded:
        findings.append({"severity": "info", "message": "qca_nss_ppe_* Module wurden gefunden, qca_nss_ppe selbst aber nicht."})

    counter_by_name = {row["counter"]: row for row in data["counters"]}
    for name in ["offload failed", "ppe offload collision", "dev not registered in ppe", "no free hws", "invalid egress mac", "fallback offloads"]:
        row = counter_by_name.get(name)
        if row and row["value"] > 0:
            findings.append({"severity": row["severity"], "message": f"Counter '{name}' ist {row['value']}."})
    for name in ["add/remove vlan dev to ppe err", "add/remove pppoe dev to ppe err", "add/remove mac dev to ppe err"]:
        row = counter_by_name.get(name)
        if row and row["value"] > 0:
            findings.append({"severity": "critical", "message": f"Registrierungsfehler: '{name}' ist {row['value']}."})

    if any(row.get("severity") == "warning" for row in data["hwpa_interfaces"]):
        findings.append({"severity": "warning", "message": "Mindestens ein produktives Interface hat ppe_ifidx -1."})

    has_vlan = data["counts"]["vlan_devices"] > 0
    has_pppoe = data["counts"]["pppoe_devices"] > 0
    module_detected = {row["module"]: row["detected"] for row in modules}
    if has_vlan and not module_detected.get("qca_nss_ppe_vlan"):
        findings.append({"severity": "warning", "message": "VLAN-Devices erkannt, aber qca_nss_ppe_vlan fehlt."})
    if has_pppoe and not module_detected.get("qca_nss_ppe_pppoe_mgr"):
        findings.append({"severity": "warning", "message": "PPPoE-Devices erkannt, aber qca_nss_ppe_pppoe_mgr fehlt."})

    for row in data["portshaper"]:
        if row.get("assessment") == "WAN-Portshaper aktiv":
            findings.append({"severity": "info", "message": "Auf dem WAN-Port ist ein PPE-Portshaper aktiv. Das kann für Upstream-Shaping oder Providerprofile relevant sein."})

    if not data["ppe_detected"]:
        findings.append({"severity": "info", "message": "Keine PPE/HWPA-Daten in den Supportdaten gefunden."})

    for message in data.get("network_correlation", {}).get("diagnostics", []):
        severity_hint = "warning" if "nicht in PPE registriert" in message and any(
            not row.get("ppe_registered") and row.get("networking_found") and (
                str(row.get("network_state", "")).upper() == "UP"
                or row.get("network_addresses")
                or row.get("detected_service") != "Unknown"
            )
            for row in data.get("ppeNetworkCorrelation", [])
        ) else "info"
        findings.append({"severity": severity_hint, "message": message})

    for finding in findings:
        if _severity_rank(finding["severity"]) > _severity_rank(severity):
            severity = finding["severity"]
    label = _severity_label(severity)
    return {"overall": label, "severity": severity, "findings": findings}


def _build_ppe_developer_summary(data: dict) -> str:
    assessment = data["assessment"]
    state = data["summary"].get("accelerator_state", {})
    active_bits = [name.upper() for name in ["ipv4", "ipv6", "ratelimiter"] if state.get(name) == "enabled"]
    if not data["ppe_detected"]:
        return "PPE/HWPA wurde in den Supportdaten nicht eindeutig erkannt. PPE-spezifische Blöcke oder qca_nss_ppe Module fehlen."
    correlation_rows = data.get("ppeNetworkCorrelation", [])
    wan_vlans = [row for row in correlation_rows if row.get("vlan_id") is not None and (str(row.get("parent_interface", "")).lower().startswith("wan") or row.get("interface_name", "").lower().startswith("wan"))]
    confirmed = [row for row in correlation_rows if row.get("ppe_registered") and row.get("networking_found")]
    service_sentences = []
    for row in correlation_rows:
        if row.get("detected_service") == "Unknown":
            continue
        reason = ""
        evidence_text = " ".join(row.get("evidence", []))
        if "PPPoE-Interface" in evidence_text:
            reason = " über ein PPPoE-Interface"
        elif re.search(r"\b(default|route)\b", evidence_text, re.IGNORECASE):
            reason = " über Routing-Hinweise"
        elif re.search(r"\b(igmp|multicast|iptv)\b", evidence_text, re.IGNORECASE):
            reason = " über Multicast-/IGMP-Hinweise"
        service_sentences.append(f"{row['interface_name']} wird{reason} dem Dienst {row['detected_service']} zugeordnet (Confidence {row['confidence']}).")
    unknown_rows = [row for row in correlation_rows if row.get("detected_service") == "Unknown"]
    session_text = "Aktive Hardware Sessions sind vorhanden." if data["sessions"].get("total") else "Aktive Hardware Sessions wurden nicht gefunden."
    error_text = "Keine PPE-Registrierungsfehler erkannt."
    if assessment["severity"] in {"warning", "critical"}:
        relevant = ", ".join(f["message"] for f in assessment["findings"] if f["severity"] in {"warning", "critical"})
        error_text = f"Offload- oder Registrierungsauffälligkeiten: {relevant}."
    parts = [
        "PPE/HWPA ist aktiv.",
        f"Aktive Accelerator: {', '.join(active_bits) if active_bits else 'k.A.'}.",
    ]
    if wan_vlans:
        parts.append(f"In der PPE/Networking-Korrelation wurden folgende WAN-VLANs erkannt: {', '.join(row['interface_name'] for row in wan_vlans)}.")
    if confirmed:
        parts.append(f"Die Networking-Sektion bestätigt {len(confirmed)} VLAN-Interface(s), die auch in der PPE sichtbar sind.")
    if service_sentences:
        parts.extend(service_sentences[:5])
    if unknown_rows:
        parts.append(f"{', '.join(row['interface_name'] for row in unknown_rows[:5])} konnte keinem Dienst eindeutig zugeordnet werden.")
    parts.extend([error_text, session_text])
    return " ".join(parts)



def _extract_named_sections(text: str, keywords: List[str]) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    pattern = re.compile(r"^##### BEGIN SECTION\s+(.+?)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        lower_title = title.lower()
        if not any(keyword.lower() in lower_title for keyword in keywords):
            continue
        end_marker = text.find("##### END SECTION", match.end())
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        end = end_marker if end_marker != -1 else next_start
        sections[title] = text[match.start():end]
    return sections


def _extract_networking_raw_blocks(text: str) -> Dict[str, str]:
    blocks = _extract_named_sections(text, ["network", "routing", "route", "provider", "dsld", "multid", "tr069", "cwmp", "voip", "igmp"])
    networking = "\n".join(raw for title, raw in blocks.items() if "network" in title.lower())
    if not networking:
        networking = _extract_section_containing(text, "Networking\n----------")
    if networking and not any("network" in title.lower() for title in blocks):
        blocks.setdefault("Networking", networking)
    return blocks


def _detect_vlan_from_name(name: str) -> Tuple[Optional[int], Optional[str]]:
    clean = name.strip().strip(":")
    match = re.match(r"^(?P<parent>.+?)\.v(?P<vlan>\d+)(?:\.|$)", clean, re.IGNORECASE)
    if match:
        return int(match.group("vlan")), match.group("parent")
    match = re.match(r"^(?P<parent>(?:eth|ptm|dsl|wan|net_upstream|pon)\d*)\.(?P<vlan>\d+)(?:\.|$)", clean, re.IGNORECASE)
    if match:
        return int(match.group("vlan")), match.group("parent")
    match = re.match(r"^vlan(?P<vlan>\d+)$", clean, re.IGNORECASE)
    if match:
        return int(match.group("vlan")), None
    return None, _infer_base_from_name(clean)


def _network_interface_type(name: str) -> str:
    lower = name.lower()
    vlan_id, _ = _detect_vlan_from_name(name)
    if vlan_id is not None:
        return "VLAN"
    if re.search(r"\.p\d+$", lower) or "pppoe" in lower:
        return "PPPoE"
    if lower.startswith("br") or lower in {"lan", "guest"}:
        return "BRIDGE"
    if lower.startswith(("eth", "ptm", "dsl", "wan", "pon", "net_upstream")):
        return "PHYSICAL"
    return "UNKNOWN"


def _get_or_create_network_interface(interfaces: Dict[str, dict], name: str) -> dict:
    vlan_id, parent = _detect_vlan_from_name(name)
    iface = interfaces.setdefault(
        name,
        {
            "name": name,
            "type": _network_interface_type(name),
            "state": "",
            "mac": "",
            "mtu": None,
            "parent": parent,
            "vlan_id": vlan_id,
            "ip_addresses": [],
            "ipv6_addresses": [],
            "bridge": "",
            "master": "",
            "routes": [],
            "services": [],
            "raw_evidence": [],
        },
    )
    if vlan_id is not None:
        iface["vlan_id"] = vlan_id
        iface["type"] = "VLAN"
    if parent and not iface.get("parent"):
        iface["parent"] = parent
    return iface


def _append_unique(row: dict, key: str, value: str) -> None:
    if value and value not in row.setdefault(key, []):
        row[key].append(value)


def _add_network_evidence(iface: dict, section: str, line: str) -> None:
    evidence = f"{section}: {line.strip()}"
    _append_unique(iface, "raw_evidence", evidence[:500])


def parse_network_interfaces_from_supportdata(text: str) -> Tuple[List[dict], Dict[str, str]]:
    raw_blocks = _extract_networking_raw_blocks(text)
    interfaces: Dict[str, dict] = {}
    iface_pattern = r"[A-Za-z][A-Za-z0-9_-]*(?:\.(?:v)?\d+)?(?:\.p\d+)?"
    for section, raw in raw_blocks.items():
        current: Optional[dict] = None
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            ip_link = re.match(rf"^\d+:\s+(?P<name>{iface_pattern})(?:@(?P<parent>{iface_pattern}))?:\s+<(?P<flags>[^>]*)>.*?\bmtu\s+(?P<mtu>\d+).*?(?:\bstate\s+(?P<state>\S+))?", stripped)
            if ip_link:
                name = ip_link.group("name")
                current = _get_or_create_network_interface(interfaces, name)
                if ip_link.group("parent"):
                    current["parent"] = ip_link.group("parent")
                current["mtu"] = _parse_ppe_int(ip_link.group("mtu"))
                flags = ip_link.group("flags") or ""
                current["state"] = ip_link.group("state") or ("UP" if "UP" in flags.split(",") else current.get("state", ""))
                master = re.search(r"\bmaster\s+(\S+)", stripped)
                if master:
                    current["master"] = master.group(1)
                _add_network_evidence(current, section, stripped)
                continue
            ifconfig = re.match(rf"^(?P<name>{iface_pattern})\s+(?:Link encap|flags=|HWaddr|mtu\s+)", stripped, re.IGNORECASE)
            if ifconfig:
                current = _get_or_create_network_interface(interfaces, ifconfig.group("name"))
                mac = re.search(r"(?:HWaddr|ether)\s+([0-9a-f:]{17})", stripped, re.IGNORECASE)
                if mac:
                    current["mac"] = mac.group(1)
                mtu = re.search(r"\bMTU[:=]?(\d+)|\bmtu\s+(\d+)", stripped, re.IGNORECASE)
                if mtu:
                    current["mtu"] = _parse_ppe_int(mtu.group(1) or mtu.group(2))
                if "UP" in stripped:
                    current["state"] = "UP"
                _add_network_evidence(current, section, stripped)
                continue
            if current:
                mac = re.search(r"(?:link/ether|HWaddr|ether)\s+([0-9a-f:]{17})", stripped, re.IGNORECASE)
                if mac:
                    current["mac"] = mac.group(1)
                    _add_network_evidence(current, section, stripped)
                mtu = re.search(r"\bMTU[:=]?(\d+)|\bmtu\s+(\d+)", stripped, re.IGNORECASE)
                if mtu:
                    current["mtu"] = _parse_ppe_int(mtu.group(1) or mtu.group(2))
                    _add_network_evidence(current, section, stripped)
                ipv4 = re.search(r"\binet(?: addr:|\s+)(\d+\.\d+\.\d+\.\d+(?:/\d+)?)", stripped)
                if ipv4:
                    _append_unique(current, "ip_addresses", ipv4.group(1))
                    _add_network_evidence(current, section, stripped)
                ipv6 = re.search(r"\binet6(?: addr:|\s+)([0-9a-f:]+(?:/\d+)?)", stripped, re.IGNORECASE)
                if ipv6:
                    _append_unique(current, "ipv6_addresses", ipv6.group(1))
                    _add_network_evidence(current, section, stripped)
            route_match = re.search(rf"\b(?:default|0\.0\.0\.0/0|route\s+\S+).*\b(?:dev|iface|interface|über|via)\s+(?P<name>{iface_pattern})\b", stripped, re.IGNORECASE)
            if route_match:
                iface = _get_or_create_network_interface(interfaces, route_match.group("name"))
                _append_unique(iface, "routes", stripped)
                _add_network_evidence(iface, section, stripped)
            bridge_match = re.search(rf"\b(?P<br>br\S*|lan|guest)\b.*\b(?P<name>{iface_pattern})\b|\b(?P<name2>{iface_pattern})\b.*\b(?:master|bridge)\s+(?P<br2>br\S*|lan|guest)\b", stripped, re.IGNORECASE)
            if bridge_match:
                name = bridge_match.group("name") or bridge_match.group("name2")
                bridge = bridge_match.group("br") or bridge_match.group("br2")
                if name and bridge and name != bridge:
                    iface = _get_or_create_network_interface(interfaces, name)
                    iface["bridge"] = bridge
                    iface["master"] = iface.get("master") or bridge
                    _add_network_evidence(iface, section, stripped)
            for name in set(re.findall(rf"\b{iface_pattern}\b", stripped)):
                vlan_id, _ = _detect_vlan_from_name(name)
                if vlan_id is None and not re.search(r"\.p\d+$", name):
                    continue
                iface = _get_or_create_network_interface(interfaces, name)
                service = _detect_service_from_text(stripped)
                if service != "Unknown":
                    _append_unique(iface, "services", service)
                _add_network_evidence(iface, section, stripped)
    return sorted(interfaces.values(), key=lambda row: row["name"]), raw_blocks




def _normalize_wan_service_name(service: str) -> str:
    return (service or "").strip().lower()


def _display_wan_service(service: str) -> str:
    normalized = _normalize_wan_service_name(service)
    return {
        "internet": "Internet",
        "iptv": "IPTV",
        "tr069": "TR-069",
        "tr-069": "TR-069",
        "voip": "VoIP",
        "voice": "VoIP",
        "mgmt": "Management",
        "management": "Management",
    }.get(normalized, service.title() if service else "Unknown")


def _new_wan_service_vlan(service: str, index: Optional[int] = None) -> dict:
    normalized = _normalize_wan_service_name(service)
    return {
        "service": normalized,
        "index": index,
        "state": "",
        "active": False,
        "sync_group": "",
        "logical_parent_interface": "",
        "physical_parent_interface": "",
        "encap": "",
        "encap_id": None,
        "medium": "",
        "mac": "",
        "vlan_id": None,
        "vlan_prio": None,
        "property": "",
        "tagtype": "",
        "tos": "",
        "ipv4_status": "",
        "ipv6_status": "",
        "ipv4_address": "",
        "ipv4_gateway": "",
        "ipv4_mtu": None,
        "routes": [],
        "ppp_configured": False,
        "tr069_activated": False,
        "detected_service": _display_wan_service(normalized),
        "confidence": "unknown",
        "evidence": [],
    }


def _merge_wan_service_value(row: dict, key: str, value, overwrite: bool = False) -> None:
    if value in (None, ""):
        return
    if overwrite or row.get(key) in (None, "", [], False):
        row[key] = value


def _add_wan_service_evidence(row: dict, evidence: str) -> None:
    if evidence and evidence not in row.setdefault("evidence", []):
        row["evidence"].append(evidence[:500])


def _upsert_wan_service_vlan(rows: Dict[str, dict], service: str, values: dict, evidence: List[str]) -> dict:
    normalized = _normalize_wan_service_name(service)
    row = rows.setdefault(normalized, _new_wan_service_vlan(normalized, values.get("index")))
    priority_fields = {"index", "service"}
    for key, value in values.items():
        if key in priority_fields:
            continue
        _merge_wan_service_value(row, key, value)
    if values.get("index") is not None and row.get("index") is None:
        row["index"] = values["index"]
    row["detected_service"] = _display_wan_service(row.get("service", normalized))
    for item in evidence:
        _add_wan_service_evidence(row, item)
    if row.get("vlan_id") is not None and row.get("service"):
        row["confidence"] = "high"
    return row


def parse_wan_service_vlans_from_networking(text: str) -> List[dict]:
    raw_blocks = _extract_networking_raw_blocks(text)
    rows: Dict[str, dict] = {}
    if not raw_blocks:
        return []

    name_re = re.compile(r"^(?P<index>\d+):\s+name\s+(?P<service>\S+)\s*\((?P<flags>[^)]*)\)", re.IGNORECASE)
    sync_re = re.compile(r"^(?P<index>\d+):\s+sync_group:\s*(?P<sync_group>\S+)", re.IGNORECASE)
    iface_re = re.compile(
        r"^(?P<index>\d+):\s+iface\s+(?P<logical>[\w.-]+)(?:/(?P<physical>[\w.-]+))?\s+"
        r"(?P<encap>[A-Za-z0-9_-]+)/(?:0x)?(?P<encap_id>\d+)(?:/(?P<medium>[\w.-]+))?\s+"
        r"(?P<mac>[0-9a-f:]{17})\s+stay\s+online\s+(?P<online>[01])\s+vlan\s+(?P<vlan>\d+)\s+prio\s+(?P<prio>\d+)"
        r"(?:\s+\(prop:\s*(?P<prop>[^)]*)\))?",
        re.IGNORECASE,
    )
    update_re = re.compile(
        r"wandmng_encap_update\(\):\s+wand_connection\((?P<service>[^)]+)\):\s+iface\s+(?P<iface>\S+)\s+"
        r"(?P<encap>[A-Za-z0-9_-]+)/(?:0x)?(?P<encap_id>\d+)\s+vlan\s+(?P<vlan>\d+)\s+fixed\s+prio\s+(?P<tagtype>0x[0-9a-f]+)\s+prio\s+(?P<prio>\d+)\s+tos\s+(?P<tos>0x[0-9a-f]+)",
        re.IGNORECASE,
    )
    internal_name_re = re.compile(r"^(?P<index>\d+):\s+name\s+(?P<service>\S+)\s+state\s+(?P<state>[^:]+):", re.IGNORECASE)

    compact_by_index: Dict[str, dict] = {}
    current_internal: Optional[dict] = None
    in_vlancfg = False
    for section, raw in raw_blocks.items():
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            name_match = name_re.match(stripped)
            if name_match:
                index = name_match.group("index")
                service = name_match.group("service")
                flags = name_match.group("flags") or ""
                state = flags.split(",", 1)[0].strip() if flags else ""
                values = {
                    "index": _parse_ppe_int(index),
                    "state": state,
                    "active": "active" in flags.lower(),
                }
                compact_by_index.setdefault(index, {})["service"] = service
                compact_by_index[index].update(values)
                _upsert_wan_service_vlan(rows, service, values, [f"Networking: name {service} ({flags})"])
                current_internal = None
                in_vlancfg = False
                continue

            sync_match = sync_re.match(stripped)
            if sync_match:
                index = sync_match.group("index")
                compact_by_index.setdefault(index, {})["sync_group"] = sync_match.group("sync_group")
                service = compact_by_index[index].get("service")
                if service:
                    _upsert_wan_service_vlan(rows, service, {"sync_group": sync_match.group("sync_group")}, [f"Networking: sync_group {sync_match.group('sync_group')}"])
                continue

            iface_match = iface_re.match(stripped)
            if iface_match:
                index = iface_match.group("index")
                service = compact_by_index.get(index, {}).get("service")
                if not service:
                    continue
                values = dict(compact_by_index.get(index, {}))
                values.update(
                    {
                        "index": _parse_ppe_int(index),
                        "logical_parent_interface": iface_match.group("logical"),
                        "physical_parent_interface": iface_match.group("physical") or "",
                        "encap": iface_match.group("encap"),
                        "encap_id": _parse_ppe_int(iface_match.group("encap_id")),
                        "medium": iface_match.group("medium") or "",
                        "mac": iface_match.group("mac"),
                        "vlan_id": _parse_ppe_int(iface_match.group("vlan")),
                        "vlan_prio": _parse_ppe_int(iface_match.group("prio")),
                        "property": iface_match.group("prop") or "",
                    }
                )
                evidence = f"Networking: iface {values['logical_parent_interface']}/{values['physical_parent_interface']} {values['encap']} vlan {values['vlan_id']} prio {values['vlan_prio']}"
                _upsert_wan_service_vlan(rows, service, values, [evidence])
                current_internal = None
                in_vlancfg = False
                continue

            update_match = update_re.search(stripped)
            if update_match:
                service = update_match.group("service")
                values = {
                    "logical_parent_interface": update_match.group("iface"),
                    "encap": update_match.group("encap"),
                    "encap_id": _parse_ppe_int(update_match.group("encap_id")),
                    "vlan_id": _parse_ppe_int(update_match.group("vlan")),
                    "vlan_prio": _parse_ppe_int(update_match.group("prio")),
                    "tagtype": update_match.group("tagtype"),
                    "tos": update_match.group("tos"),
                }
                _upsert_wan_service_vlan(rows, service, values, [f"Networking: wandmng_encap_update {service} {values['encap']} vlan {values['vlan_id']} prio {values['vlan_prio']}"])
                current_internal = None
                in_vlancfg = False
                continue

            internal_match = internal_name_re.match(stripped)
            if internal_match:
                service = internal_match.group("service")
                values = {"index": _parse_ppe_int(internal_match.group("index")), "state": internal_match.group("state").strip()}
                current_internal = _upsert_wan_service_vlan(rows, service, values, [f"wand internalview: name {service} state {values['state']}"])
                in_vlancfg = False
                continue

            if not current_internal:
                continue
            encap = re.match(r"^encap\s+(?P<encap>\S+)\s+\((?P<id>\d+)\)", stripped, re.IGNORECASE)
            if encap:
                _merge_wan_service_value(current_internal, "encap", encap.group("encap"))
                _merge_wan_service_value(current_internal, "encap_id", _parse_ppe_int(encap.group("id")))
                _add_wan_service_evidence(current_internal, f"wand internalview: encap {encap.group('encap')} ({encap.group('id')})")
                continue
            if stripped.lower() == "vlancfg":
                in_vlancfg = True
                continue
            if in_vlancfg:
                tagtype = re.match(r"^tagtype\s+(0x[0-9a-f]+)", stripped, re.IGNORECASE)
                if tagtype:
                    _merge_wan_service_value(current_internal, "tagtype", tagtype.group(1))
                    continue
                vlan_id = re.match(r"^id\s+(\d+)", stripped, re.IGNORECASE)
                if vlan_id:
                    current_internal["vlan_id"] = _parse_ppe_int(vlan_id.group(1))
                    _add_wan_service_evidence(current_internal, f"wand internalview: name {current_internal.get('service')}, vlancfg id {vlan_id.group(1)}")
                    continue
                prio = re.match(r"^prio\s+(\d+)", stripped, re.IGNORECASE)
                if prio:
                    current_internal["vlan_prio"] = _parse_ppe_int(prio.group(1))
                    continue
            for key, pattern in {
                "ipv4_status": r"^ipv4_connstatus\s+(\S+)",
                "ipv6_status": r"^ipv6_connstatus\s+(\S+)",
                "ipv4_address": r"^(?:ipv4_address|ipaddr|localip)\s+(\d+\.\d+\.\d+\.\d+)",
                "ipv4_gateway": r"^(?:ipv4_gateway|gateway|gw)\s+(\d+\.\d+\.\d+\.\d+)",
                "ipv4_mtu": r"^(?:ipv4_mtu|mtu)\s+(\d+)",
                "mac": r"^mac\s+([0-9a-f:]{17})",
            }.items():
                found = re.match(pattern, stripped, re.IGNORECASE)
                if found:
                    value = _parse_ppe_int(found.group(1)) if key == "ipv4_mtu" else found.group(1)
                    _merge_wan_service_value(current_internal, key, value)
                    _add_wan_service_evidence(current_internal, f"wand internalview: {stripped}")
            if re.match(r"^(?:route|default)\b", stripped, re.IGNORECASE):
                _append_unique(current_internal, "routes", stripped)
                _add_wan_service_evidence(current_internal, f"wand internalview: {stripped}")
            if re.match(r"^pppconfig\s+username/passwd\s+set", stripped, re.IGNORECASE):
                current_internal["ppp_configured"] = True
                _add_wan_service_evidence(current_internal, "wand internalview: pppconfig username/passwd set")
            tr069 = re.match(r"^tr069_activated\s+(yes|true|1)", stripped, re.IGNORECASE)
            if tr069:
                current_internal["tr069_activated"] = True
                _add_wan_service_evidence(current_internal, "wand internalview: tr069_activated yes")

    for row in rows.values():
        if row.get("vlan_id") is not None and row.get("service"):
            row["confidence"] = "high"
        if row.get("encap"):
            row["encap"] = row["encap"].upper() if row["encap"].lower() == "rbe" else row["encap"]
    return sorted(rows.values(), key=lambda row: (row.get("index") is None, row.get("index") if row.get("index") is not None else 999, row.get("service") or ""))


def _expected_ppe_vlan_name(service_vlan: dict) -> str:
    parent = service_vlan.get("physical_parent_interface") or service_vlan.get("logical_parent_interface") or "wan"
    vlan_id = service_vlan.get("vlan_id")
    return f"{parent}.v{vlan_id}" if vlan_id is not None else parent


def correlate_wan_service_vlans_with_ppe(service_vlans: List[dict], ppe_by_name: Dict[str, dict]) -> dict:
    rows: List[dict] = []
    matched_ppe_names = set()
    for service_vlan in service_vlans:
        expected = _expected_ppe_vlan_name(service_vlan)
        vlan_id = service_vlan.get("vlan_id")
        parent = service_vlan.get("physical_parent_interface") or service_vlan.get("logical_parent_interface") or ""
        found = []
        for name in ppe_by_name:
            found_vlan, found_parent = _detect_vlan_from_name(name)
            if found_vlan == vlan_id and (not parent or not found_parent or found_parent == parent or name.startswith(f"{parent}.")):
                found.append(name)
        pppoe_found = any(re.search(r"\.p\d+$", name) for name in found)
        vlan_found = any(not re.search(r"\.p\d+$", name) for name in found)
        matched_ppe_names.update(found)
        if vlan_found:
            assessment = "OK"
        else:
            assessment = "Hinweis"
        if service_vlan.get("detected_service") == "Internet" and service_vlan.get("active") and not vlan_found:
            assessment = "Hinweis"
        rows.append(
            {
                "service": service_vlan.get("service"),
                "detected_service": service_vlan.get("detected_service"),
                "vlan_id": vlan_id,
                "networking_found": True,
                "ppe_registered": vlan_found,
                "expected_ppe_device": expected,
                "found_ppe_devices": sorted(found),
                "pppoe_ppe_device_found": pppoe_found,
                "assessment": assessment,
                "evidence": service_vlan.get("evidence", [])[:10],
            }
        )
    unmatched = []
    for name, ppe in ppe_by_name.items():
        if name in matched_ppe_names or ppe.get("category") != "VLAN":
            continue
        vlan_id, parent = _detect_vlan_from_name(name)
        unmatched.append(
            {
                "interface_name": name,
                "vlan_id": vlan_id,
                "parent_interface": parent or ppe.get("base_device") or ppe.get("parent") or "",
                "assessment": "Warnung",
                "evidence": [f"PPE: {name} keinem Networking-Dienst zugeordnet"],
            }
        )
    return {"service_vlan_ppe_rows": rows, "unmatched_ppe_vlans": unmatched}


def _detect_service_from_text(text: str) -> str:
    lower = text.lower()
    if "igmp" in lower or "iptv" in lower or "tvswitching" in lower or re.search(r"\bmulticast\b.*\b(route|routing|proxy|snoop|group)", lower):
        return "IPTV"
    if any(term in lower for term in ["tr069", "tr-069", "cwmp", "acs", "provisioning", "management"]):
        return "TR-069"
    if any(term in lower for term in ["voip", "voice", "sip", "rtp", "telefonie", "phone", "packetcable"]):
        return "VoIP"
    if any(term in lower for term in ["pppoe", "default route", "default", "internet", " wan", "dhcp"]):
        return "Internet"
    return "Unknown"


def _service_evidence_key(service: str) -> str:
    return {
        "Internet": "service_reference_evidence",
        "IPTV": "multicast_evidence",
        "TR-069": "service_reference_evidence",
        "VoIP": "service_reference_evidence",
        "Management": "service_reference_evidence",
    }.get(service, "service_reference_evidence")


def _merge_ppe_vlan_rows(data: dict) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    for row in data.get("ppe_devices", []):
        if row.get("category") not in {"VLAN", "PPPoE"}:
            continue
        name = row.get("interface_name")
        if name:
            rows[name] = row
    for row in data.get("device_only", []):
        if row.get("category") not in {"VLAN", "PPPoE"}:
            continue
        name = row.get("name")
        if name and name not in rows:
            rows[name] = {
                "interface_name": name,
                "ppe_index": row.get("ppe_port"),
                "port": row.get("ppe_port"),
                "device_type": row.get("type"),
                "base_device": row.get("base_device"),
                "category": row.get("category"),
            }
    return rows


def build_ppe_network_correlation(data: dict, text: str) -> dict:
    network_interfaces, networking_blocks = parse_network_interfaces_from_supportdata(text)
    service_vlans = parse_wan_service_vlans_from_networking(text)
    net_by_name = {row["name"]: row for row in network_interfaces}
    ppe_by_name = _merge_ppe_vlan_rows(data)
    service_ppe_correlation = correlate_wan_service_vlans_with_ppe(service_vlans, ppe_by_name)
    pppoe_parents = {re.sub(r"\.p\d+$", "", name): name for name, row in ppe_by_name.items() if row.get("category") == "PPPoE" or re.search(r"\.p\d+$", name)}
    vlan_names = set()
    for name, row in ppe_by_name.items():
        if row.get("category") == "VLAN" or _detect_vlan_from_name(name)[0] is not None:
            vlan_names.add(name)
    for name, row in net_by_name.items():
        if row.get("type") == "VLAN" or row.get("vlan_id") is not None:
            vlan_names.add(name)
    for service_vlan in service_vlans:
        vlan_id = service_vlan.get("vlan_id")
        if vlan_id is None:
            continue
        vlan_names.add(_expected_ppe_vlan_name(service_vlan))
    service_by_expected = {_expected_ppe_vlan_name(row): row for row in service_vlans if row.get("vlan_id") is not None}
    service_by_vlan_parent = {
        (row.get("vlan_id"), row.get("physical_parent_interface") or row.get("logical_parent_interface") or ""): row
        for row in service_vlans
        if row.get("vlan_id") is not None
    }
    correlations: List[dict] = []
    mappings: List[dict] = []
    for name in sorted(vlan_names):
        ppe = ppe_by_name.get(name)
        net = net_by_name.get(name)
        vlan_id, parent = _detect_vlan_from_name(name)
        evidence: List[str] = []
        service = "Unknown"
        service_hits: List[str] = []
        service_vlan = service_by_expected.get(name)
        if not service_vlan and vlan_id is not None:
            service_vlan = service_by_vlan_parent.get((vlan_id, parent or "")) or next(
                (row for (row_vlan, _row_parent), row in service_by_vlan_parent.items() if row_vlan == vlan_id),
                None,
            )
        if service_vlan:
            service_hits.append(service_vlan.get("detected_service", "Unknown"))
            evidence.extend(service_vlan.get("evidence", [])[:6])
            parent = parent or service_vlan.get("physical_parent_interface") or service_vlan.get("logical_parent_interface")
        if ppe:
            evidence.append(f"PPE: {name} als {ppe.get('category') or ppe.get('device_type')} registriert")
            parent = parent or ppe.get("base_device") or ppe.get("parent")
        if net:
            evidence.extend(net.get("raw_evidence", [])[:6])
            parent = parent or net.get("parent")
            service_hits.extend(net.get("services", []))
            if net.get("routes") and any(route.lower().startswith(("default", "0.0.0.0/0")) or " default" in route.lower() for route in net.get("routes", [])):
                service_hits.append("Internet")
            for raw in net.get("raw_evidence", []):
                detected = _detect_service_from_text(raw)
                if detected != "Unknown":
                    service_hits.append(detected)
        if name in pppoe_parents:
            service_hits.append("Internet")
            evidence.append(f"PPE: PPPoE-Interface {pppoe_parents[name]} hängt auf {name}")
        if service_hits:
            priority = ["Internet", "IPTV", "TR-069", "VoIP", "Management"]
            service = sorted(set(service_hits), key=lambda item: priority.index(item) if item in priority else 99)[0]
        if service == "Unknown" and (ppe or net):
            evidence.append("WAN-VLAN erkannt, aber keine eindeutige Dienstzuordnung möglich.")
        if service_vlan and service != "Unknown":
            confidence = service_vlan.get("confidence", "high")
        elif ppe and net and service != "Unknown" and (name in pppoe_parents or len(set(service_hits)) >= 1):
            confidence = "high"
        elif ppe and net:
            confidence = "medium" if service != "Unknown" else "low"
        elif service != "Unknown":
            confidence = "low"
        else:
            confidence = "unknown"
        correlation = {
            "interface_name": name,
            "ppe_ifidx": ppe.get("ppe_index") if ppe else None,
            "ppe_type": ppe.get("device_type") or ppe.get("category") if ppe else "",
            "ppe_port": ppe.get("port") if ppe else None,
            "vlan_id": vlan_id or (net.get("vlan_id") if net else None),
            "parent_interface": parent or (net.get("parent") if net else ""),
            "network_state": net.get("state", "") if net else "nicht gefunden",
            "network_mtu": net.get("mtu") if net else None,
            "network_addresses": (net.get("ip_addresses", []) + net.get("ipv6_addresses", [])) if net else [],
            "bridge_membership": (net.get("bridge") or net.get("master", "")) if net else "",
            "detected_service": service,
            "confidence": confidence,
            "evidence": evidence[:10],
            "ppe_registered": bool(ppe),
            "networking_found": bool(net or service_vlan),
            "wan_service_vlan": service_vlan or {},
            "expected_ppe_device": _expected_ppe_vlan_name(service_vlan) if service_vlan else name,
            "found_ppe_devices": next((row.get("found_ppe_devices", []) for row in service_ppe_correlation["service_vlan_ppe_rows"] if row.get("expected_ppe_device") == (_expected_ppe_vlan_name(service_vlan) if service_vlan else name)), [name] if ppe else []),
            "pppoe_ppe_device_found": name in pppoe_parents or any(found in pppoe_parents.values() for found in (next((row.get("found_ppe_devices", []) for row in service_ppe_correlation["service_vlan_ppe_rows"] if row.get("expected_ppe_device") == (_expected_ppe_vlan_name(service_vlan) if service_vlan else name)), []))),
        }
        correlations.append(correlation)
        mapping = {
            "interface": name,
            "vlan_id": correlation["vlan_id"],
            "service": service,
            "confidence": confidence,
            "ppe_status": "registriert" if ppe else "nicht registriert",
            "networking_status": "gefunden" if net else "nicht gefunden",
            "evidence": evidence[:10],
            "networking_section_references": [section for section in networking_blocks if any(name in line for line in networking_blocks[section].splitlines())][:5],
            "ip_configuration_evidence": [item for item in evidence if re.search(r"\b(ip|inet|addr|dhcp)\b", item, re.IGNORECASE)],
            "route_evidence": [item for item in evidence if re.search(r"\b(route|default|gateway|gw)\b", item, re.IGNORECASE)],
            "bridge_evidence": [item for item in evidence if re.search(r"\b(bridge|brctl|master|br-)\b", item, re.IGNORECASE)],
            "multicast_evidence": [item for item in evidence if re.search(r"\b(igmp|multicast|iptv|tv)\b", item, re.IGNORECASE)],
            "service_reference_evidence": [item for item in evidence if _detect_service_from_text(item) != "Unknown"],
        }
        mappings.append(mapping)
    productive_missing_ppe = [row for row in correlations if not row["ppe_registered"] and row["networking_found"] and (row["network_state"].upper() == "UP" or row["network_addresses"] or row["detected_service"] != "Unknown")]
    for row in productive_missing_ppe:
        row["evidence"].append("VLAN ist im Netzwerkstack produktiv sichtbar, aber nicht in der PPE registriert.")
    return {
        "networkInterfaces": network_interfaces,
        "wanServiceVlans": service_vlans,
        "serviceVlanPpeCorrelation": service_ppe_correlation.get("service_vlan_ppe_rows", []),
        "unmatchedPpeVlans": service_ppe_correlation.get("unmatched_ppe_vlans", []),
        "ppeNetworkCorrelation": correlations,
        "vlanServiceMapping": mappings,
        "diagnostics": [],
        "raw_blocks": networking_blocks,
    }

def parse_ppe_diagnosis(text: str) -> dict:
    raw_blocks = _extract_ppe_raw_blocks(text)
    modules = parse_ppe_kernel_modules(text)
    ppe_devices = parse_ppe_if_map(raw_blocks.get("ppe_if_map", ""))
    hwpa_interfaces = parse_hwpa_interfaces(raw_blocks.get("interfaces", ""))
    device_only = parse_ppe_device_only(raw_blocks.get("interfaces", ""))
    counters = parse_common_ppe_offload_counters(raw_blocks.get("brief", ""))
    summary = parse_ppe_summary_and_state(raw_blocks.get("brief", ""), raw_blocks.get("caps", ""))
    sessions = parse_ppe_sessions(raw_blocks.get("synced_sessions", ""))
    mtu_mru = parse_ppe_mtu_mru(text)
    flow_control = parse_ppe_flow_control(text)
    portshaper = parse_ppe_portshaper(text, ppe_devices)
    ppe_detected = bool(ppe_devices or counters or raw_blocks.get("ppe_if_map") or any(row["detected"] and "ppe" in row["module"] for row in modules))
    hwpa_detected = bool(raw_blocks.get("brief") or raw_blocks.get("interfaces") or sessions.get("total") or re.search(r"\bHWPA\b", text, re.IGNORECASE))
    combined_devices = ppe_devices + [
        {"category": row.get("category"), "interface_name": row.get("name"), "port": row.get("ppe_port")}
        for row in device_only
    ]
    counts = {
        "registered_devices": len(ppe_devices),
        "vlan_devices": sum(1 for row in combined_devices if row.get("category") == "VLAN"),
        "pppoe_devices": sum(1 for row in combined_devices if row.get("category") == "PPPoE"),
        "bridge_devices": sum(1 for row in combined_devices if row.get("category") == "BRIDGE"),
        "physical_ports": sum(1 for row in combined_devices if row.get("category") == "PHYSICAL"),
    }
    data = {
        "ppe_detected": ppe_detected,
        "hwpa_detected": hwpa_detected,
        "summary": summary,
        "counts": counts,
        "ppe_devices": ppe_devices,
        "device_tree": build_ppe_device_tree(ppe_devices),
        "hwpa_interfaces": hwpa_interfaces,
        "device_only": device_only,
        "device_chains": [f"{row['name']} hängt auf {row['base_device']}" for row in device_only if row.get("base_device")],
        "counters": counters,
        "sessions": sessions,
        "mtu_mru": mtu_mru,
        "flow_control": flow_control,
        "portshaper": portshaper,
        "modules": modules,
        "raw_blocks": {key: value for key, value in raw_blocks.items() if value},
    }
    data["network_correlation"] = build_ppe_network_correlation(data, text)
    data["networkInterfaces"] = data["network_correlation"]["networkInterfaces"]
    data["wanServiceVlans"] = data["network_correlation"].get("wanServiceVlans", [])
    data["serviceVlanPpeCorrelation"] = data["network_correlation"].get("serviceVlanPpeCorrelation", [])
    data["unmatchedPpeVlans"] = data["network_correlation"].get("unmatchedPpeVlans", [])
    data["ppeNetworkCorrelation"] = data["network_correlation"]["ppeNetworkCorrelation"]
    data["vlanServiceMapping"] = data["network_correlation"]["vlanServiceMapping"]
    data["assessment"] = _analyze_ppe_diagnosis(data)
    data["developer_summary"] = _build_ppe_developer_summary(data)
    return data


def _ppe_dataframe(rows: List[dict], columns: Dict[str, str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(columns.values()))
    return pd.DataFrame([{label: row.get(key, "") for key, label in columns.items()} for row in rows])


def _render_missing_ppe_section(name: str) -> None:
    st.info(f"{name}: Nicht in den Supportdaten gefunden.")



def _format_ppe_evidence(values: List[str]) -> str:
    return "\n".join(values[:6]) if values else "k.A."


def _render_ppe_network_correlation(data: dict) -> None:
    st.markdown("### Service-VLANs aus Networking")
    service_rows = data.get("wanServiceVlans", [])
    if service_rows:
        ppe_rows_by_service = {row.get("service"): row for row in data.get("serviceVlanPpeCorrelation", [])}
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Dienst": row.get("detected_service", ""),
                        "VLAN": row.get("vlan_id", ""),
                        "Prio": row.get("vlan_prio", ""),
                        "Encapsulation": row.get("encap", ""),
                        "Interface": row.get("logical_parent_interface", ""),
                        "Parent/WAN-Port": row.get("physical_parent_interface", ""),
                        "MAC": row.get("mac", ""),
                        "IPv4 Status": row.get("ipv4_status", ""),
                        "IPv4 Adresse": row.get("ipv4_address", ""),
                        "MTU": row.get("ipv4_mtu", ""),
                        "PPE registriert": "Ja" if ppe_rows_by_service.get(row.get("service"), {}).get("ppe_registered") else "Nein",
                        "PPPoE-PPE-Device": "Ja" if ppe_rows_by_service.get(row.get("service"), {}).get("pppoe_ppe_device_found") else "Nein",
                        "Confidence": row.get("confidence", ""),
                        "Evidence": _format_ppe_evidence(row.get("evidence", [])),
                    }
                    for row in service_rows
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Keine Service-VLANs aus der Networking-Sektion erkannt.")

    st.markdown("### PPE-Abgleich")
    service_ppe_rows = data.get("serviceVlanPpeCorrelation", [])
    unmatched_ppe = data.get("unmatchedPpeVlans", [])
    if service_ppe_rows or unmatched_ppe:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Dienst": row.get("detected_service", ""),
                        "VLAN": row.get("vlan_id", ""),
                        "Networking": "Ja" if row.get("networking_found") else "Nein",
                        "PPE": "Ja" if row.get("ppe_registered") else "Nein",
                        "Erwartetes PPE Device": row.get("expected_ppe_device", ""),
                        "Gefundenes PPE Device": ", ".join(row.get("found_ppe_devices", [])) or "-",
                        "PPPoE-PPE-Device": "Ja" if row.get("pppoe_ppe_device_found") else "Nein",
                        "Bewertung": row.get("assessment", ""),
                    }
                    for row in service_ppe_rows
                ]
                + [
                    {
                        "Dienst": "Unknown",
                        "VLAN": row.get("vlan_id", ""),
                        "Networking": "Nein",
                        "PPE": "Ja",
                        "Erwartetes PPE Device": "-",
                        "Gefundenes PPE Device": row.get("interface_name", ""),
                        "PPPoE-PPE-Device": "Nein",
                        "Bewertung": row.get("assessment", ""),
                    }
                    for row in unmatched_ppe
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Kein PPE-Abgleich für Service-VLANs verfügbar.")

    st.markdown("### PPE ↔ Networking Korrelation")
    rows = data.get("ppeNetworkCorrelation", [])
    if not rows:
        _render_missing_ppe_section("PPE ↔ Networking Korrelation")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Interface": row.get("interface_name", ""),
                    "VLAN-ID": row.get("vlan_id", ""),
                    "PPE registriert": "Ja" if row.get("ppe_registered") else "Nein",
                    "Networking gefunden": "Ja" if row.get("networking_found") else "Nein",
                    "State": row.get("network_state", ""),
                    "MTU": row.get("network_mtu", ""),
                    "IP-Adressen": ", ".join(row.get("network_addresses", [])),
                    "Parent/Base": row.get("parent_interface", ""),
                    "Bridge/Master": row.get("bridge_membership", ""),
                    "Erkannter Dienst": row.get("detected_service", "Unknown"),
                    "Confidence": row.get("confidence", "unknown"),
                    "Evidence": _format_ppe_evidence(row.get("evidence", [])),
                }
                for row in rows
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### VLAN-Zuordnung nach Dienst")
    mappings = data.get("vlanServiceMapping", [])
    for service in ["Internet", "IPTV", "TR-069", "VoIP", "Management", "Unknown"]:
        service_rows = [row for row in mappings if row.get("service") == service]
        with st.expander(f"{service} ({len(service_rows)})", expanded=bool(service_rows and service != "Unknown")):
            if not service_rows:
                st.info(f"Keine VLANs für {service} erkannt.")
                continue
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Interface": row.get("interface", ""),
                            "VLAN-ID": row.get("vlan_id", ""),
                            "PPE-Status": row.get("ppe_status", ""),
                            "Networking-Status": row.get("networking_status", ""),
                            "Confidence": row.get("confidence", ""),
                            "Evidence": _format_ppe_evidence(row.get("evidence", [])),
                            "Networking-Sektionen": ", ".join(row.get("networking_section_references", [])),
                        }
                        for row in service_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

def render_ppe_diagnosis(data: dict) -> None:
    st.subheader("PPE Diagnose / Packet Processing Engine")
    assessment = data["assessment"]
    severity_to_icon = {"ok": "✅", "info": "ℹ️", "warning": "⚠️", "critical": "🛑"}
    st.markdown(f"### {severity_to_icon.get(assessment['severity'], 'ℹ️')} Gesamtbewertung: {assessment['overall']}")

    state = data["summary"].get("accelerator_state", {})
    metrics = [
        ("PPE erkannt", "Ja" if data["ppe_detected"] else "Nein"),
        ("HWPA erkannt", "Ja" if data["hwpa_detected"] else "Nein"),
        ("IPv4", state.get("ipv4") or "k.A."),
        ("IPv6", state.get("ipv6") or "k.A."),
        ("Ratelimiter", state.get("ratelimiter") or "k.A."),
        ("HW Sessions", f"{data['summary'].get('used_hws') if data['summary'].get('used_hws') is not None else data['sessions'].get('total', 0)} / {data['summary'].get('max_hws') or 'k.A.'}"),
        ("PPE Devices", data["counts"]["registered_devices"]),
        ("VLAN / PPPoE", f"{data['counts']['vlan_devices']} / {data['counts']['pppoe_devices']}"),
        ("Bridge / Ports", f"{data['counts']['bridge_devices']} / {data['counts']['physical_ports']}"),
    ]
    for row_start in range(0, len(metrics), 3):
        cols = st.columns(3)
        for col, (label, value) in zip(cols, metrics[row_start : row_start + 3]):
            col.metric(label, value)

    if assessment["findings"]:
        for finding in assessment["findings"]:
            message = finding["message"]
            if finding["severity"] == "critical":
                st.error(message)
            elif finding["severity"] == "warning":
                st.warning(message)
            else:
                st.info(message)

    _render_ppe_network_correlation(data)

    st.markdown("### Filter")
    f1, f2, f3, f4 = st.columns(4)
    only_errors = f1.checkbox("Nur Fehler anzeigen", key="ppe_filter_errors")
    only_registered = f2.checkbox("Nur registrierte PPE Devices", key="ppe_filter_registered")
    only_wan = f3.checkbox("Nur WAN-relevant", key="ppe_filter_wan")
    only_vlan_pppoe = f4.checkbox("Nur VLAN/PPPoE", key="ppe_filter_vlan_pppoe")

    st.markdown("### Registrierte PPE Devices")
    devices = data["ppe_devices"]
    if only_wan:
        devices = [row for row in devices if "wan" in str(row.get("interface_name", "")).lower() or "wan" in str(row.get("role", "")).lower()]
    if only_vlan_pppoe:
        devices = [row for row in devices if row.get("category") in {"VLAN", "PPPoE"}]
    if devices:
        st.dataframe(
            _ppe_dataframe(
                devices,
                {
                    "ppe_index": "PPE Index",
                    "device_type": "Device Type",
                    "interface_name": "Interface Name",
                    "port": "Port",
                    "parent": "Parent",
                    "base_device": "Base Device",
                    "l3_interface": "L3 Interface",
                    "vsi": "VSI",
                    "category": "Badge",
                    "role": "Rolle/Einschätzung",
                },
            ),
            use_container_width=True,
        )
        if data["device_tree"]:
            st.markdown("**Abgeleitete Device-Baumansicht**")
            st.code("\n".join(data["device_tree"]), language="text")
    else:
        _render_missing_ppe_section("ppe_if_map")

    st.markdown("### HWPA Interface Tabelle")
    hwpa_rows = data["hwpa_interfaces"]
    if only_errors:
        hwpa_rows = [row for row in hwpa_rows if row.get("severity") in {"warning", "critical"}]
    if only_registered:
        hwpa_rows = [row for row in hwpa_rows if (row.get("ppe_ifidx") or -1) >= 0]
    if only_wan:
        hwpa_rows = [row for row in hwpa_rows if "wan" in row.get("netdev", "").lower()]
    if only_vlan_pppoe:
        hwpa_rows = [row for row in hwpa_rows if _classify_ppe_interface(row.get("netdev", "")) in {"VLAN", "PPPoE"}]
    if hwpa_rows:
        st.dataframe(
            _ppe_dataframe(hwpa_rows, {"netdev": "Netdev", "avm_pid": "avm_pid", "ppe_ifidx": "ppe_ifidx", "ppe_port": "ppe_port", "rfs": "RFS", "hwpa_type": "HWPA Type", "status": "Status"}),
            use_container_width=True,
        )
    else:
        _render_missing_ppe_section("HWPA Interface Liste")

    st.markdown("### PPE Device Only")
    if data["device_only"]:
        st.dataframe(
            _ppe_dataframe(data["device_only"], {"name": "Name", "ppe_port": "PPE Index / Port", "type": "Typ", "mtu": "MTU", "base_device": "Base Device", "refs": "Refcount", "mac": "MAC-Adresse"}),
            use_container_width=True,
        )
        if data["device_chains"]:
            st.markdown("**Erkannte Ketten**")
            st.code("\n".join(data["device_chains"]), language="text")
    else:
        _render_missing_ppe_section("PPE device only")

    st.markdown("### Offload Counter / Fehlerbewertung")
    counters = data["counters"]
    if only_errors:
        counters = [row for row in counters if row.get("severity") in {"info", "warning", "critical"}]
    if counters:
        st.dataframe(_ppe_dataframe(counters, {"counter": "Counter", "value": "Wert", "severity": "Bewertung"}), use_container_width=True)
    else:
        _render_missing_ppe_section("Common PPE offload counter")

    st.markdown("### Aktive HWPA/PPE Sessions")
    sessions = data["sessions"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gesamt", sessions.get("total", 0))
    c2.metric("Ratelimiter", sessions.get("by_type", {}).get("ratelimiter", 0))
    c3.metric("IPv4 / IPv6", f"{sessions.get('by_type', {}).get('ipv4', 0)} / {sessions.get('by_type', {}).get('ipv6', 0)}")
    c4.metric("Sonstige", sessions.get("by_type", {}).get("sonstige", 0))
    anonymize = st.checkbox("Session-IP-Adressen anonymisieren", value=True, key="ppe_anonymize_sessions")
    session_rows = []
    for session in sessions.get("sessions", []):
        row = {key: session.get(key, "") for key in ["hws", "accelerator", "source_ip", "destination_ip", "source_port", "destination_port", "protocol"]}
        if anonymize:
            for key in ["source_ip", "destination_ip"]:
                if row.get(key):
                    row[key] = _anonymize_ip(row[key])
        session_rows.append(row)
    if session_rows:
        st.dataframe(pd.DataFrame(session_rows), use_container_width=True)
    else:
        _render_missing_ppe_section("HWPA Sessions")

    detail_tabs = st.tabs(["MTU/MRU", "Flow Control", "Portshaper", "Kernelmodule"])
    with detail_tabs[0]:
        if data["mtu_mru"]:
            st.dataframe(_ppe_dataframe(data["mtu_mru"], {"port": "Port", "mtu_hex": "MTU hex", "mtu_decimal": "MTU dezimal", "mru_hex": "MRU hex", "mru_decimal": "MRU dezimal", "assessment": "Bewertung"}), use_container_width=True)
        else:
            _render_missing_ppe_section("PPE MTU / MRU")
    with detail_tabs[1]:
        if data["flow_control"]:
            st.dataframe(_ppe_dataframe(data["flow_control"], {"port": "Port", "status": "Status", "assessment": "Bewertung"}), use_container_width=True)
        else:
            _render_missing_ppe_section("PPE Flow Control")
    with detail_tabs[2]:
        if data["portshaper"]:
            st.dataframe(_ppe_dataframe(data["portshaper"], {"port": "Port", "interface": "Interface", "active": "Shaper aktiv", "cir_hex": "CIR hex", "cir_decimal": "CIR dezimal", "cbs_hex": "CBS hex", "cbs_decimal": "CBS dezimal", "frame_mode": "Frame Mode", "assessment": "Einschätzung"}), use_container_width=True)
        else:
            _render_missing_ppe_section("PPE Portshaper")
    with detail_tabs[3]:
        st.dataframe(_ppe_dataframe(data["modules"], {"module": "Modulname", "size": "Größe", "used_by": "Used by", "detected": "Status erkannt"}), use_container_width=True)

    st.markdown("### Diagnose-Text / Entwicklerzusammenfassung")
    summary = data["developer_summary"]
    st.code(summary, language="text")
    components.html(
        f"""
        <button onclick="navigator.clipboard.writeText({json.dumps(summary)}); this.textContent='Kopiert'; setTimeout(() => this.textContent='Entwicklerzusammenfassung kopieren', 1500);">
            Entwicklerzusammenfassung kopieren
        </button>
        """,
        height=45,
    )

    with st.expander("Aufklappbare Rohdatenblöcke"):
        combined_raw_blocks = dict(data.get("raw_blocks", {}))
        for name, raw in data.get("network_correlation", {}).get("raw_blocks", {}).items():
            combined_raw_blocks.setdefault(name, raw)
        if combined_raw_blocks:
            for name, raw in combined_raw_blocks.items():
                st.markdown(f"**{name}**")
                st.code(raw[:20000], language="text")
        else:
            st.info("Keine PPE-/Networking-Rohdatenblöcke gefunden.")


def _anonymize_ip(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value
    if ip.version == 4:
        parts = value.split(".")
        return ".".join(parts[:2] + ["x", "x"])
    groups = value.split(":")
    return ":".join(groups[:3] + ["…"])

def analyze_connection_performance(
    runtime_entries: List[RatelimiterRuntimeEntry],
    config_entries: List[RatelimiterConfigEntry],
    hardware_analysis: dict,
    load_average: Optional[List[str]],
    text: str,
) -> dict:
    summary = hardware_analysis.get("summary", {})
    assessment = list(hardware_analysis.get("assessment", []))
    findings: List[ConnectionPerformanceFinding] = []
    score = 0

    total_blocked = sum(entry.blocked for entry in runtime_entries)
    total_hits = sum(entry.hits for entry in runtime_entries)
    total_packets = int(summary.get("total_packets", 0) or 0)
    total_bytes = int(summary.get("total_bytes", 0) or 0)
    sessions = int(summary.get("total_sessions", 0) or 0)
    unique_sources = summary.get("unique_sources", [])
    unique_source_count = len(unique_sources)
    limited_ports = summary.get("limited_ports", [])
    catchall_count = sum(1 for session in hardware_analysis.get("sessions", []) if session.get("catchall"))
    management_ports = sorted({port for port in limited_ports if port in {443, 499}})

    load_values: List[float] = []
    if load_average:
        for value in load_average:
            try:
                load_values.append(float(value))
            except (TypeError, ValueError):
                load_values.append(0.0)
    load_1, load_5, load_15 = (load_values + [0.0, 0.0, 0.0])[:3]

    drop_indicators = parse_drop_indicators(text)
    offload_indicators = parse_offload_indicators(text)

    findings.append(
        ConnectionPerformanceFinding(
            category="ratelimiter",
            severity="info",
            title="Rate-Limiter-Bewertung",
            details=(
                f"{sessions} Hardware-Sessions, {total_packets} Pakete, {total_blocked} geblockte Pakete. "
                f"{unique_source_count} eindeutige Source-IPs, {len(limited_ports)} limitierte Zielports."
            ),
        )
    )

    if total_blocked >= 1000:
        score += 30
        findings.append(ConnectionPerformanceFinding("drops", "critical", "Viele geblockte Pakete", "Die Block-Counts sind hoch und können auf relevanten Schutz-/Lastdruck hinweisen."))
    elif total_blocked >= 100:
        score += 15
        findings.append(ConnectionPerformanceFinding("drops", "warning", "Erhöhte Block-Aktivität", "Geblockte Pakete treten gehäuft auf und sollten beobachtet werden."))
    elif total_blocked > 0:
        score += 5

    if unique_source_count >= 8 and len(limited_ports) >= 5:
        score += 20
        findings.append(ConnectionPerformanceFinding("traffic", "warning", "Breites Quell-/Port-Muster", "Viele externe Quellen und mehrere Zielports deuten eher auf Scan-/Hintergrundtraffic."))
    elif unique_source_count >= 4 and len(limited_ports) >= 3:
        score += 10
        findings.append(ConnectionPerformanceFinding("traffic", "warning", "Auffälliges Trafficmuster", "Mehrere Quellen und Zielports deuten auf erhöhte Schutzaktivität."))

    if total_packets >= 200_000:
        score += 20
    elif total_packets >= 50_000:
        score += 10

    if load_1 >= 3.5 or load_5 >= 2.5:
        score += 30
        findings.append(ConnectionPerformanceFinding("cpu", "critical", "Hohe Systemlast", "Die Load-Average ist deutlich erhöht und kann die Verarbeitung beeinträchtigen."))
    elif load_1 >= 1.5 or load_5 >= 1.0:
        score += 15
        findings.append(ConnectionPerformanceFinding("cpu", "warning", "Erhöhte Systemlast", "Die Lastwerte sind erhöht und sollten im Kontext beobachtet werden."))
    else:
        findings.append(ConnectionPerformanceFinding("cpu", "info", "Systemlast unauffällig", "Die vorliegenden Load-Werte sprechen nicht für eine Überlastung der FRITZ!Box."))

    drop_score = sum(drop_indicators.values())
    severe_drop_hits = drop_indicators.get("frag_freemem", 0) + drop_indicators.get("reject_not_possible", 0)
    if severe_drop_hits > 0:
        score += 20
        findings.append(ConnectionPerformanceFinding("drops", "critical", "Kritische Drop-Indikatoren", "Speicher-/Reject-Hinweise deuten auf relevanten Verarbeitungsdruck hin."))
    elif drop_score >= 100:
        score += 20
        findings.append(ConnectionPerformanceFinding("drops", "warning", "Viele Drop-/Rate-Limit-Ereignisse", "Mehrere Dropcounter sind deutlich erhöht."))
    elif drop_score >= 20:
        score += 10
        findings.append(ConnectionPerformanceFinding("drops", "warning", "Moderate Drop-Indikatoren", "Drop-/Rate-Limit-Counter sind vorhanden, aber nicht extrem."))
    elif drop_score > 0:
        score += 5

    offload_score = sum(offload_indicators.values())
    if offload_score >= 5:
        score += 15
        findings.append(ConnectionPerformanceFinding("offload", "warning", "Offload-/Session-Hinweise", "Es gibt Indikatoren für Fallbacks, Flushes oder Session-Druck."))
    elif offload_score > 0:
        score += 5

    if (load_1 >= 1.5 or load_5 >= 1.0) and (drop_score >= 20 or total_blocked >= 100):
        score += 15
        findings.append(ConnectionPerformanceFinding("traffic", "warning", "Kombinierter Last-/Drop-Effekt", "Erhöhte Last und Schutz-/Drop-Aktivität treten gleichzeitig auf."))

    if management_ports and sessions > 0 and score <= 20:
        findings.append(ConnectionPerformanceFinding("ratelimiter", "info", "Management-Port-Schutz aktiv", "Port 443/499 ist limitiert – das ist häufig normaler Schutzmechanismus."))

    if not runtime_entries and not config_entries and sessions == 0:
        findings.append(ConnectionPerformanceFinding("ratelimiter", "info", "Keine Rate-Limiter-Daten", "Es liegen keine verwertbaren Rate-Limiter-Informationen vor."))

    score = max(0, min(score, 100))
    if score >= 70:
        status = "red"
        summary_text = "Auffällige Schutz-/Drop- oder Lastindikatoren deuten auf eine mögliche Beeinträchtigung der Anschluss-Performance hin."
    elif score >= 35:
        status = "yellow"
        summary_text = "Es gibt einzelne Auffälligkeiten. Ein klarer Performance-Impact ist nicht belegt, sollte aber beobachtet werden."
    else:
        status = "green"
        summary_text = "Keine belastbaren Hinweise auf ein Performanceproblem am Anschluss."

    if management_ports and score < 35:
        summary_text += " Der aktive Rate-Limiter wirkt hier überwiegend wie ein normaler Schutzmechanismus."

    assessment.extend(
        [
            "Die Einschätzung kombiniert Rate-Limiter, Last, Drop- und Offload-Indikatoren.",
            "Die Bewertung ist heuristisch und kein harter Fehlernachweis.",
        ]
    )

    return {
        "status": status,
        "score": score,
        "summary": summary_text,
        "findings": [finding.__dict__ for finding in findings],
        "metrics": {
            "ratelimiter_sessions": sessions,
            "ratelimiter_packets": total_packets,
            "ratelimiter_bytes": total_bytes,
            "ratelimiter_unique_sources": unique_source_count,
            "limited_ports": limited_ports,
            "blocked_packets": total_blocked,
            "load_1": load_1,
            "load_5": load_5,
            "load_15": load_15,
            "cpu_idle": None,
            "drop_indicators": drop_indicators,
            "offload_indicators": offload_indicators,
            "runtime_hits": total_hits,
            "catchall_rules": catchall_count,
        },
        "assessment": assessment,
    }


def render_ratelimiter(
    runtime_entries: List[RatelimiterRuntimeEntry],
    config_entries: List[RatelimiterConfigEntry],
    hardware_analysis: dict,
    connection_performance_analysis: dict,
) -> None:
    st.subheader("Anschluss-Performance")
    sessions = hardware_analysis.get("sessions", [])
    summary = hardware_analysis.get("summary", {})
    assessment = hardware_analysis.get("assessment", [])
    performance_findings = connection_performance_analysis.get("findings", [])
    performance_metrics = connection_performance_analysis.get("metrics", {})
    performance_status = connection_performance_analysis.get("status", "green")
    performance_score = connection_performance_analysis.get("score", 0)
    performance_summary = connection_performance_analysis.get("summary", "")

    if not runtime_entries and not config_entries and not sessions:
        st.info("Keine Ratelimiter-Daten in der Support-Datei gefunden.")
        return

    total_hits = sum(entry.hits for entry in runtime_entries)
    total_blocked = sum(entry.blocked for entry in runtime_entries)
    active_rules = sum(1 for entry in runtime_entries if entry.hits > 0)
    configured_rules = len(config_entries)

    status_label = {"green": "🟢 Unauffällig", "yellow": "🟡 Beobachten", "red": "🔴 Auffällig"}.get(performance_status, "🟢 Unauffällig")
    c_status, c_score = st.columns([2, 1])
    c_status.metric("Status", status_label)
    c_score.metric("Performance-Score", f"{performance_score}/100")
    if performance_summary:
        st.markdown(f"**Kurzfazit:** {performance_summary}")

    analysis_blocks = {
        "ratelimiter": "Rate-Limiter-Bewertung",
        "cpu": "Systemlast / CPU / Load",
        "drops": "Drops / Paketverluste / Rate-Limits",
        "offload": "Offload- / Session-Indikatoren",
        "traffic": "Anschluss- / Traffic-Indikatoren",
    }
    for category, title in analysis_blocks.items():
        block_findings = [item for item in performance_findings if item.get("category") == category]
        if not block_findings:
            continue
        st.markdown(f"**{title}**")
        for item in block_findings:
            prefix = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(item.get("severity"), "🔵")
            st.markdown(f"- {prefix} **{item.get('title')}**: {item.get('details')}")

    st.markdown("**Technische Einschätzung**")
    for item in connection_performance_analysis.get("assessment", []):
        st.markdown(f"- {item}")

    st.subheader("Technische Details (Ratelimiter-Rohdaten)")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Laufzeit-Regeln", len(runtime_entries))
    col2.metric("Konfigurierte Regeln", configured_rules)
    col3.metric("Treffer gesamt", total_hits)
    col4.metric("Geblockt gesamt", total_blocked)

    if runtime_entries:
        st.markdown(
            f"**Kurzfazit:** {active_rules} von {len(runtime_entries)} Laufzeit-Regeln hatten Traffic. "
            f"Geblockte Pakete: **{total_blocked}**."
        )
        runtime_df = pd.DataFrame(
            [
                {
                    "Bereich": entry.scope,
                    "Regel": entry.rule,
                    "Limit (Pakete)": entry.packets,
                    "Intervall (s)": entry.interval_seconds,
                    "Treffer": entry.hits,
                    "Geblockt": entry.blocked,
                }
                for entry in runtime_entries
            ]
        ).sort_values(by=["Geblockt", "Treffer"], ascending=False)
        st.dataframe(runtime_df, use_container_width=True, hide_index=True)

    if config_entries:
        st.subheader("Konfiguration")
        config_df = pd.DataFrame(
            [
                {
                    "Name": entry.name,
                    "Aktiv": "Ja" if entry.enabled else "Nein",
                    "Interface": entry.iface,
                    "Regel": entry.rule,
                    "Pakete": entry.packets,
                    "Intervall": entry.interval,
                    "Early": entry.early,
                }
                for entry in config_entries
            ]
        )
        st.dataframe(config_df, use_container_width=True, hide_index=True)

    if sessions:
        st.subheader("Hardware Rate-Limiter Sessions")
        session_df = pd.DataFrame(
            [
                {
                    "Source IP": session["source_ip"],
                    "Destination IP": session["destination_ip"],
                    "Source Port": session["source_port"],
                    "Destination Port": session["destination_port"],
                    "Matched Packets": session["matched_packets"],
                    "Matched Bytes": session["matched_bytes"],
                    "Rule Type": session["rule_type"] or "k.A.",
                    "Catchall": "Ja" if session["catchall"] else "Nein",
                }
                for session in sessions
            ]
        )
        st.dataframe(session_df, use_container_width=True, hide_index=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Hardware Sessions", summary.get("total_sessions", 0))
        c2.metric("Pakete gesamt", summary.get("total_packets", 0))
        c3.metric("Bytes gesamt", summary.get("total_bytes", 0))

        st.markdown(
            "**Unique Source IPs:** "
            + (", ".join(summary.get("unique_sources", [])) or "Keine")
        )
        ports = summary.get("limited_ports", [])
        st.markdown(
            "**Limitierte Zielports:** "
            + (", ".join(str(port) for port in ports) if ports else "Keine")
        )

    if performance_metrics.get("drop_indicators") or performance_metrics.get("offload_indicators"):
        st.markdown("**Zusätzliche Indikatoren**")
        if performance_metrics.get("drop_indicators"):
            st.json({"drop_indicators": performance_metrics.get("drop_indicators")})
        if performance_metrics.get("offload_indicators"):
            st.json({"offload_indicators": performance_metrics.get("offload_indicators")})

    if assessment:
        st.markdown("**Ratelimiter-spezifische Einschätzung**")
        for item in assessment:
            st.markdown(f"- {item}")


@st.cache_data(show_spinner=False)
def parse_support_data(text: str) -> dict:
    access_technology = detect_access_technology(text)
    dect_rssi_index_to_dbm = extract_dect_rssi_index_to_dbm(text)
    ratelimiter_runtime = parse_ratelimiter_runtime(text)
    ratelimiter_config = parse_ratelimiter_config(text)
    ratelimiter_sessions = parse_hardware_ratelimiter_sessions(text)
    ratelimiter_analysis = analyze_hardware_ratelimiter_sessions(ratelimiter_sessions)
    return {
        "access_technology": access_technology,
        "device_mac": extract_device_mac(text),
        "dsl_data": parse_dsl_snr(text),
        "dsl_metrics": parse_dsl_metrics(text),
        "docsis_data": parse_docsis_channels(text),
        "fiber_data": parse_fiber_overview(text),
        "internet_connection": parse_internet_connection(text),
        "port_forwardings": parse_port_forwardings(text),
        "ar7_network_settings": parse_ar7_network_settings(text),
        "ar7_overview": parse_ar7_overview(text),
        "networks": parse_wlan_env_scan(text),
        "stations": parse_wlan_stations(text),
        "radio_loads": parse_wlan_radio_load(text),
        "noisefloor_entries": parse_wlan_noisefloor(text),
        "ports": parse_lan_ports(text),
        "voip_accounts": parse_voip_accounts(text),
        "neighbour_clients": parse_neighbour_clients(text),
        "events": parse_events(text),
        "ratelimiter_runtime": ratelimiter_runtime,
        "ratelimiter_config": ratelimiter_config,
        "ratelimiter_analysis": ratelimiter_analysis,
        "connection_performance_analysis": analyze_connection_performance(
            ratelimiter_runtime,
            ratelimiter_config,
            ratelimiter_analysis,
            parse_fritz_load_average(text),
            text,
        ),
        "mesh_topology": parse_mesh_topology(text),
        "dect_devices": parse_dect_device_info(text, dect_rssi_index_to_dbm),
        "dect_basis_info": parse_dect_basis_info(text),
        "network_utilization_sections": parse_avm_counter_rrd_sections(text),
        "ppe_diagnosis": parse_ppe_diagnosis(text),
    }


def build_dashboard(text: str) -> None:
    fritz_model = parse_fritz_model(text) or "Unbekannt"
    firmware_version = parse_fritz_firmware_version(text) or "Unbekannt"
    uptime = parse_fritz_uptime_days_minutes(text) or "Unbekannt"
    load_average = parse_fritz_load_average(text)
    parsed = parse_support_data(text)
    access_technology = parsed["access_technology"]

    info_metrics = [
        ("Modell", fritz_model),
        ("Firmwareversion", firmware_version),
        ("Uptime (Tage/Min)", uptime),
        ("Zugang", access_technology),
    ]
    load_section = ""
    if load_average:
        load_labels = ["1 Min", "5 Min", "15 Min"]
        load_cards = "\n".join(
            textwrap.dedent(
                f"""\
                <div class="info-frame-load-card">
                    <div class="info-frame-label">{escape_html(label)}</div>
                    <div class="info-frame-value">{escape_html(value)}</div>
                </div>
                """
            ).strip()
            for label, value in zip(load_labels, load_average)
        )
        load_section = textwrap.dedent(
            f"""\
            <div class="info-frame-load">
                <div class="info-frame-load-title">Load Average</div>
                <div class="info-frame-load-grid">
                    {load_cards}
                </div>
            </div>
            """
        ).strip()
    info_cards = "\n".join(
        textwrap.dedent(
            f"""\
            <div class="info-frame-card">
                <div class="info-frame-label">{escape_html(label)}</div>
                <div class="info-frame-value">{escape_html(value)}</div>
            </div>
            """
        ).strip()
        for label, value in info_metrics
    )
    st.markdown(
        textwrap.dedent(
            f"""\
            <div class="info-frame">
                <div class="info-frame-title">FRITZ!Box Informationen</div>
                <div class="info-frame-grid">
                    {info_cards}
                </div>
                {load_section}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    device_mac = parsed["device_mac"]
    dsl_data = parsed["dsl_data"]
    dsl_metrics = parsed["dsl_metrics"]
    docsis_data = parsed["docsis_data"]
    fiber_data = parsed["fiber_data"]
    internet_connection = parsed["internet_connection"]
    port_forwardings = parsed["port_forwardings"]
    ar7_overview = parsed["ar7_overview"]
    networks = parsed["networks"]
    stations = parsed["stations"]
    radio_loads = parsed["radio_loads"]
    noisefloor_entries = parsed["noisefloor_entries"]
    ports = parsed["ports"]
    voip_accounts = parsed["voip_accounts"]
    neighbour_clients = parsed["neighbour_clients"]
    events = parsed["events"]
    ratelimiter_runtime = parsed["ratelimiter_runtime"]
    ratelimiter_config = parsed["ratelimiter_config"]
    ratelimiter_analysis = parsed["ratelimiter_analysis"]
    connection_performance_analysis = parsed["connection_performance_analysis"]
    mesh_topology = parsed["mesh_topology"]
    dect_devices = parsed["dect_devices"]
    dect_basis_info = parsed["dect_basis_info"]
    network_utilization_sections = parsed["network_utilization_sections"]
    ppe_diagnosis = parsed["ppe_diagnosis"]

    mac_label = "MACa Adresse"
    mac_value = device_mac or "Keine MAC-Adresse gefunden"
    mac_value_safe = escape_html(mac_value)
    st.markdown(
        textwrap.dedent(
            f"""\
            <div class="mac-address-card" aria-label="{mac_label}">
                <div class="mac-address-title">{mac_label}</div>
                <div class="mac-address-row">
                    <div class="mac-address-value" id="mac-address-value">{mac_value_safe}</div>
                    <button
                        class="mac-address-copy"
                        type="button"
                        aria-label="MAC-Adresse kopieren"
                        data-copy="{mac_value_safe}"
                        onclick="navigator.clipboard.writeText(this.dataset.copy); this.classList.add('copied'); this.textContent='Kopiert'; setTimeout(() => {{ this.classList.remove('copied'); this.textContent='Kopieren'; }}, 1500);"
                    >
                        Kopieren
                    </button>
                </div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )

    tab_names = [access_technology, "Internet", "LAN", "WLAN", "Netzauslastung", "PPE Diagnose", "Anschluss-Performance", "Mesh", "Telefonie", "DECT", "AR7", "Events"]
    tabs = st.tabs(tab_names)
    tab_dsl, tab_internet, tab_lan, tab_wlan, tab_netzlast, tab_ppe, tab_ratelimiter, tab_mesh, tab_phone, tab_dect, tab_ar7, tab_events = tabs[:12]
    with tab_dsl:
        if access_technology == "Cable":
            render_cable_dashboard(docsis_data)
        elif access_technology == "Fiber":
            render_fiber_dashboard(fiber_data)
        else:
            render_dsl_charts(dsl_data)
            render_dsl_metrics(dsl_metrics)

    with tab_internet:
        render_internet_connection(internet_connection)
        render_port_forwardings(port_forwardings)

    with tab_lan:
        render_lan_ports(ports)
        render_lan_clients(neighbour_clients)

    with tab_wlan:
        render_wlan_scan(networks)
        render_wlan_noisefloor(noisefloor_entries)
        render_wlan_clients(stations)
        render_wlan_radio_load(radio_loads)

    with tab_netzlast:
        render_network_utilization(network_utilization_sections)

    with tab_ppe:
        render_ppe_diagnosis(ppe_diagnosis)

    with tab_ratelimiter:
        render_ratelimiter(ratelimiter_runtime, ratelimiter_config, ratelimiter_analysis, connection_performance_analysis)

    with tab_mesh:
        render_mesh_topology(mesh_topology)

    with tab_phone:
        render_telephony(voip_accounts)

    with tab_dect:
        render_dect_basis_info(dect_basis_info)
        render_dect_devices(dect_devices)

    with tab_ar7:
        render_ar7_overview(ar7_overview)

    with tab_events:
        render_events(events)



def _is_running_with_streamlit() -> bool:
    return get_script_run_ctx(suppress_warning=True) is not None


def main() -> None:
    st.set_page_config(page_title="Support-Daten Viewer", layout="wide")
    st.markdown(
        textwrap.dedent(
            """\
            <style>
            .mac-address-card {
                position: fixed;
                top: 2.5rem;
                right: 1.25rem;
                z-index: 2000;
                padding: 0.6rem 0.9rem;
                border-radius: 0.6rem;
                background: var(--secondary-background-color);
                color: var(--text-color);
                border: 1px solid rgba(120, 120, 120, 0.25);
                box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
                max-width: 240px;
                min-width: 180px;
            }
            .mac-address-title {
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                opacity: 0.7;
                margin-bottom: 0.25rem;
            }
            .mac-address-value {
                font-weight: 600;
                font-size: 0.95rem;
                word-break: break-all;
            }
            .mac-address-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 0.5rem;
            }
            .mac-address-copy {
                border: 1px solid rgba(120, 120, 120, 0.4);
                background: transparent;
                color: inherit;
                padding: 0.25rem 0.5rem;
                border-radius: 0.4rem;
                font-size: 0.75rem;
                cursor: pointer;
                transition: background 0.2s ease, color 0.2s ease;
                white-space: nowrap;
            }
            .mac-address-copy:hover {
                background: rgba(120, 120, 120, 0.15);
            }
            .mac-address-copy.copied {
                background: rgba(76, 175, 80, 0.2);
                color: #2e7d32;
            }
            .info-frame {
                background: rgba(59, 130, 246, 0.08);
                border: 1px solid rgba(59, 130, 246, 0.35);
                border-left: 6px solid rgba(59, 130, 246, 0.85);
                border-radius: 0.85rem;
                padding: 0.75rem 1rem 1rem;
                margin-bottom: 1.25rem;
                box-shadow: 0 6px 18px rgba(59, 130, 246, 0.12);
            }
            .info-frame-title {
                font-size: 1.1rem;
                font-weight: 600;
                margin-bottom: 0.6rem;
                color: var(--text-color);
            }
            .info-frame-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 0.75rem;
            }
            .info-frame-load {
                margin-top: 0.85rem;
                padding-top: 0.75rem;
                border-top: 1px dashed rgba(59, 130, 246, 0.35);
            }
            .info-frame-load-title {
                font-size: 0.85rem;
                font-weight: 600;
                margin-bottom: 0.45rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                opacity: 0.75;
            }
            .info-frame-load-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
                gap: 0.6rem;
            }
            .info-frame-load-card {
                background: rgba(255, 255, 255, 0.65);
                border-radius: 0.6rem;
                padding: 0.5rem 0.6rem;
                border: 1px solid rgba(59, 130, 246, 0.2);
                display: flex;
                flex-direction: column;
                gap: 0.2rem;
                min-height: 54px;
            }
            .info-frame-card {
                background: rgba(255, 255, 255, 0.7);
                border-radius: 0.7rem;
                padding: 0.6rem 0.7rem;
                border: 1px solid rgba(59, 130, 246, 0.18);
                min-height: 62px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                gap: 0.25rem;
            }
            .info-frame-label {
                font-size: 0.75rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                opacity: 0.7;
            }
            .info-frame-value {
                font-size: 1rem;
                font-weight: 600;
                word-break: break-word;
            }
            .stTabs [data-baseweb="tab-list"] {
                gap: 0.6rem;
                border-bottom: 2px solid rgba(120, 120, 120, 0.25);
            }
            .stTabs [data-baseweb="tab"] {
                padding: 0.5rem 1rem;
                border-radius: 999px;
                border: 1px solid rgba(120, 120, 120, 0.3);
                background: rgba(120, 120, 120, 0.08);
                color: var(--text-color);
                font-weight: 600;
                transition: all 0.2s ease;
            }
            .stTabs [data-baseweb="tab-panel"] {
                border: none;
                border-radius: 0;
                padding: 0.55rem 0 0.35rem;
                background: transparent;
                box-shadow: none;
            }
            .stTabs [data-baseweb="tab-panel"] [data-testid="stVerticalBlock"] {
                gap: 0.65rem;
            }
            .stTabs [data-baseweb="tab-panel"] h3 {
                color: #1565c0;
            }
            .stTabs [data-baseweb="tab-panel"] [data-testid="stMetric"] {
                border: 1px solid rgba(59, 130, 246, 0.18);
                border-radius: 0.7rem;
                background: rgba(255, 255, 255, 0.72);
                padding: 0.65rem 0.75rem;
            }
            .stTabs [data-baseweb="tab-panel"] [data-testid="stDataFrame"],
            .stTabs [data-baseweb="tab-panel"] [data-testid="stPlotlyChart"],
            .stTabs [data-baseweb="tab-panel"] [data-testid="stExpander"] {
                border: 1px solid rgba(59, 130, 246, 0.16);
                border-radius: 0.75rem;
                background: rgba(255, 255, 255, 0.55);
                padding: 0.3rem;
            }
            .stTabs [aria-selected="true"] {
                background: linear-gradient(135deg, rgba(59, 130, 246, 0.22), rgba(14, 165, 233, 0.2));
                border-color: rgba(59, 130, 246, 0.55);
                color: #1f2937;
                box-shadow: 0 6px 14px rgba(59, 130, 246, 0.2);
            }
            .stTabs [data-baseweb="tab"]:hover {
                border-color: rgba(59, 130, 246, 0.45);
                background: rgba(59, 130, 246, 0.12);
            }
            @media (prefers-color-scheme: dark) {
                .info-frame {
                    background: rgba(14, 116, 144, 0.3);
                    border-color: rgba(56, 189, 248, 0.55);
                    border-left-color: rgba(56, 189, 248, 0.95);
                    box-shadow: 0 6px 18px rgba(14, 116, 144, 0.35);
                }
                .info-frame-card {
                    background: rgba(15, 23, 42, 0.7);
                    border-color: rgba(56, 189, 248, 0.35);
                }
                .info-frame-load {
                    border-top-color: rgba(56, 189, 248, 0.5);
                }
                .info-frame-load-card {
                    background: rgba(15, 23, 42, 0.7);
                    border-color: rgba(56, 189, 248, 0.4);
                }
                .stTabs [data-baseweb="tab"] {
                    border-color: rgba(148, 163, 184, 0.6);
                    background: rgba(148, 163, 184, 0.16);
                    color: #e2e8f0;
                }
                .stTabs [data-baseweb="tab-panel"] {
                    background: transparent;
                    border-color: transparent;
                    box-shadow: none;
                }
                .stTabs [data-baseweb="tab-panel"] h3 {
                    color: #7dd3fc;
                }
                .stTabs [data-baseweb="tab-panel"] [data-testid="stMetric"] {
                    background: rgba(15, 23, 42, 0.76);
                    border-color: rgba(56, 189, 248, 0.3);
                }
                .stTabs [data-baseweb="tab-panel"] [data-testid="stDataFrame"],
                .stTabs [data-baseweb="tab-panel"] [data-testid="stPlotlyChart"],
                .stTabs [data-baseweb="tab-panel"] [data-testid="stExpander"] {
                    background: rgba(15, 23, 42, 0.64);
                    border-color: rgba(56, 189, 248, 0.28);
                }
                .network-settings-panel {
                    background: rgba(15, 23, 42, 0.72);
                    border-color: rgba(56, 189, 248, 0.35);
                }
                .network-card {
                    background: rgba(15, 23, 42, 0.76);
                }
                .stTabs [aria-selected="true"] {
                    background: linear-gradient(135deg, rgba(59, 130, 246, 0.45), rgba(14, 165, 233, 0.4));
                    border-color: rgba(96, 165, 250, 0.8);
                    color: #f8fafc;
                }
                .stTabs [data-baseweb="tab"]:hover {
                    border-color: rgba(96, 165, 250, 0.75);
                    background: rgba(59, 130, 246, 0.3);
                }
            }

            .network-settings-grid {
                display: grid;
                grid-template-columns: 1.35fr 1fr;
                gap: 1rem;
            }
            .network-settings-panel {
                border: 1px solid rgba(59, 130, 246, 0.2);
                border-radius: 0.9rem;
                padding: 0.9rem;
                background: rgba(59, 130, 246, 0.06);
            }
            .network-settings-panel h3 {
                margin: 0 0 0.7rem;
                color: #1565c0;
            }
            .network-card {
                border: 2px solid;
                border-radius: 0.7rem;
                margin-bottom: 0.8rem;
                overflow: hidden;
                background: rgba(255, 255, 255, 0.8);
            }
            .network-card-header {
                font-weight: 700;
                padding: 0.5rem 0.7rem;
                color: #fff;
            }
            .network-card ul,
            .network-info ul {
                margin: 0;
                padding: 0.7rem 1.1rem 0.8rem;
            }
            .network-card li,
            .network-info li {
                margin-bottom: 0.35rem;
            }
            .network-card.lan { border-color: #1976d2; }
            .network-card.lan .network-card-header { background: #1976d2; }
            .network-card.guest { border-color: #ef6c00; }
            .network-card.guest .network-card-header { background: #ef6c00; }
            .network-card.service { border-color: #78909c; }
            .network-card.service .network-card-header { background: #78909c; }

            [data-testid="stDeployButton"],
            [data-testid="stToolbar"],
            [data-testid="stHeader"] {
                display: none;
            }
            @media (max-width: 1000px) {
                .network-settings-grid {
                    grid-template-columns: 1fr;
                }
            }
            @media (max-width: 768px) {
                .mac-address-card {
                    right: 0.75rem;
                    left: 0.75rem;
                    max-width: none;
                }
            }
            </style>
            """
        ),
        unsafe_allow_html=True,
    )
    st.title("Support-Daten Viewer")

    uploaded_file = st.file_uploader("Support-Data TXT", type=["txt"])
    if uploaded_file is None:
        st.info("Bitte eine Support-Data TXT hochladen.")
        return

    try:
        text = decode_support_data_upload(uploaded_file.name, uploaded_file.read())
    except ValueError as exc:
        st.error(str(exc))
        return
    build_dashboard(text)


if __name__ == "__main__":
    if _is_running_with_streamlit():
        main()
    else:
        print(
            textwrap.dedent(
                """\
                Dieses Projekt ist eine Streamlit-App.
                Bitte starte sie mit:

                    streamlit run app.py
                """
            )
        )
        sys.exit(0)
