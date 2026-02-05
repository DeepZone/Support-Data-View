import base64
import html
import re
import sys
import textwrap
import zlib
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx


@dataclass
class WifiStation:
    mac: str
    if_name: str
    connect_state: int
    rate_rx: int
    rate_tx: int
    rate_rx_max: int
    rate_tx_max: int
    rssi: int
    quality: int


@dataclass
class WifiNetwork:
    ssid: str
    rssi: int
    radioband: Optional[int]
    frequency: Optional[int]


@dataclass
class LanPort:
    port: str
    status: str
    speed: Optional[str]


@dataclass
class WifiRadioLoad:
    radio_id: int
    offset: int
    interval: int
    count: int
    dataframe: pd.DataFrame
    error: Optional[str] = None


@dataclass
class WifiNoiseFloorEntry:
    radio_id: int
    frequency_mhz: int
    channel: int
    noise_floor: int
    load: int
    band: str


RADIO_BAND_LABELS = {
    101: "2,4 GHz",
    102: "5 GHz",
    111: "5 GHz",
    121: "6 GHz",
}


@dataclass
class TelephonyAccount:
    index: int
    number: str
    provider: str
    transport: str
    port: Optional[int]
    sip_interface: str
    registered: bool
    reachability: Optional[int]
    cipher: Optional[str] = None
    rx_bytes: Optional[int] = None
    rx_pkts: Optional[int] = None
    tx_bytes: Optional[int] = None
    tx_pkts: Optional[int] = None
    lost_pkts: Optional[int] = None
    outgoing_attempted: Optional[int] = None
    outgoing_answered: Optional[int] = None
    outgoing_connected: Optional[int] = None
    outgoing_failed: Optional[int] = None
    incoming_received: Optional[int] = None
    incoming_answered: Optional[int] = None
    incoming_connected: Optional[int] = None
    incoming_failed: Optional[int] = None
    dropped_calls: Optional[int] = None
    total_call_time: Optional[str] = None
    loopback_connected: Optional[int] = None
    loopback_failed: Optional[int] = None


@dataclass
class NeighbourClient:
    mac: str
    interface: str
    connection_type: Optional[str]
    ip_address: Optional[str]
    name: Optional[str]
    lan_port: Optional[str]
    speed: Optional[str]
    is_online: bool


@dataclass
class EventEntry:
    date: str
    time: str
    message: str


@dataclass
class InternetConnection:
    name: str
    access_type: str
    vlan: Optional[str]
    ipv4_address: Optional[str]
    ipv4_dns: List[str]
    ipv4_masq: Optional[str]
    ipv6_address: Optional[str]
    ipv6_dns: List[str]
    ipv6_masq: Optional[str]


@dataclass
class DectDevice:
    name: str
    hgid: Optional[int]
    model: Optional[str]
    ipui: Optional[str]
    curr_codec: Optional[str]
    ber: Optional[float]
    rssi_values: List[float]
    hg_ber: Optional[float]
    hg_rssi_values: List[float]
    no_emission: Optional[int]
    fw_version: Optional[str]


def format_radio_label(radio_id: int) -> str:
    band = RADIO_BAND_LABELS.get(radio_id)
    if band:
        return f"Radio {radio_id} ({band})"
    return f"Radio {radio_id}"

def extract_section(text: str, start_marker: str, end_marker: str) -> str:
    pattern = re.compile(
        rf"{re.escape(start_marker)}(.*?){re.escape(end_marker)}",
        re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


def extract_section_by_prefix(text: str, start_marker: str) -> str:
    start_index = text.find(start_marker)
    if start_index == -1:
        return ""
    end_index = text.find("##### END SECTION", start_index)
    if end_index == -1:
        return text[start_index:]
    return text[start_index:end_index]


def extract_numeric_array(text: str, label: str) -> List[int]:
    match = re.search(rf"{re.escape(label)}:\s*([0-9,\-]+)", text)
    if not match:
        return []
    values = [int(value) for value in match.group(1).split(",") if value.strip()]
    return values


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


def parse_events(text: str) -> List[EventEntry]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION Events Events")
    if not section:
        return []

    entries = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Events") or set(stripped) == {"-"}:
            continue
        match = re.match(r"(\d{2}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(.*)", stripped)
        if not match:
            continue
        entries.append(EventEntry(date=match.group(1), time=match.group(2), message=match.group(3)))
    return entries


def parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if not value or value == "-":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_dect_device_info(text: str) -> List[DectDevice]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION DECTDeviceInfo")
    if not section:
        return []

    lines = [line.strip() for line in section.splitlines()]
    data_lines: List[str] = []
    for line in lines:
        if not line or line.startswith("#####"):
            continue
        if line.startswith("Name,") or line.startswith("HGValid,"):
            continue
        if line.startswith("ULE Devices"):
            break
        data_lines.append(line)

    devices: List[DectDevice] = []
    for i in range(0, len(data_lines), 2):
        first = data_lines[i]
        second = data_lines[i + 1] if i + 1 < len(data_lines) else ""
        values1 = [item.strip() for item in first.split(",")]
        values2 = [item.strip() for item in second.split(",")]
        if len(values1) < 29:
            continue

        rssi_values = [v for v in values1[28:38] if v and v != "-"]
        hg_rssi_values = [v for v in values2[13:23] if v and v != "-"] if len(values2) >= 23 else []

        devices.append(
            DectDevice(
                name=values1[0],
                hgid=parse_int(values1[2]),
                model=values1[3] or None,
                ipui=values1[5] or None,
                curr_codec=values1[7] or None,
                ber=parse_float(values1[27]),
                rssi_values=[float(v) for v in rssi_values],
                hg_ber=parse_float(values2[12]) if len(values2) >= 13 else None,
                hg_rssi_values=[float(v) for v in hg_rssi_values],
                no_emission=parse_int(values2[23]) if len(values2) >= 24 else None,
                fw_version=values2[24] if len(values2) >= 25 and values2[24] else None,
            )
        )
    return devices


def assess_dect_rssi(rssi: Optional[float]) -> str:
    if rssi is None:
        return "k.A."
    if rssi >= -60:
        return "Sehr gut"
    if rssi >= -70:
        return "Gut"
    if rssi >= -80:
        return "Mittel"
    if rssi >= -90:
        return "Kritisch"
    return "Schlecht"


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


def extract_numeric_array_loose(text: str, label: str) -> List[int]:
    match = re.search(rf"{re.escape(label)}\s*:\s*([0-9,\-]+)", text)
    if not match:
        return []
    values = [int(value) for value in match.group(1).split(",") if value.strip()]
    return values


def extract_int_value(text: str, label: str) -> Optional[int]:
    match = re.search(rf"{re.escape(label)}\s*:\s*([-\d]+)", text)
    if not match:
        return None
    return int(match.group(1))


def extract_float_value(text: str, label: str) -> Optional[float]:
    match = re.search(rf"{re.escape(label)}\s*:\s*([-\d]+(?:\.\d+)?)", text)
    if not match:
        return None
    return float(match.group(1))


def extract_kbits_rate(text: str, label: str) -> Optional[int]:
    match = re.search(rf"{re.escape(label)}\s*:\s*(\d+)\s*kBits/s", text)
    if not match:
        return None
    return int(match.group(1))


def extract_section_block(text: str, header: str) -> str:
    match = re.search(rf"{re.escape(header)}\n[-]+\n(.*?)(\n[A-Za-z].*?:|\Z)", text, re.DOTALL)
    if not match:
        return ""
    return match.group(1)


def extract_section_between(text: str, start_marker: str, end_marker: str) -> str:
    pattern = re.compile(
        rf"{re.escape(start_marker)}(.*?){re.escape(end_marker)}",
        re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


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


def parse_voip_accounts(text: str) -> List[TelephonyAccount]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION voip Voice over IP")
    if not section:
        return []

    def parse_registration_status(status: str) -> bool:
        normalized = status.strip().lower()
        if not normalized:
            return False
        if "not registered" in normalized or "unregistered" in normalized:
            return False
        if "register failed" in normalized or "registration failed" in normalized:
            return False
        return "registered" in normalized or "registration ok" in normalized

    accounts: dict[int, TelephonyAccount] = {}
    header_pattern = re.compile(
        r"ua(?P<idx>\d+)\s+\((?P<number>[^@]+)@(?P<domain>[^,]+),\s*"
        r"(?P<transport>[^,]+),\s*port=(?P<port>\d+),\s*sipiface=(?P<sipiface>[^\)]+)\):\s*"
        r"(?P<status>.*?)(?:\s*--\s*reachability\s*(?P<reachability>\d+)\s*%\s*(?:\([^)]+\))?)?\s*$",
        re.IGNORECASE,
    )

    for line in section.splitlines():
        header_match = header_pattern.search(line)
        if header_match:
            idx = int(header_match.group("idx"))
            accounts[idx] = TelephonyAccount(
                index=idx,
                number=header_match.group("number"),
                provider=header_match.group("domain"),
                transport=header_match.group("transport"),
                port=int(header_match.group("port")),
                sip_interface=header_match.group("sipiface"),
                registered=parse_registration_status(header_match.group("status")),
                reachability=int(header_match.group("reachability"))
                if header_match.group("reachability")
                else None,
            )
            continue

        stat_match = re.match(r"\s*(\d+):\s*(.*)", line)
        if not stat_match:
            continue
        idx = int(stat_match.group(1))
        payload = stat_match.group(2).strip()
        account = accounts.get(idx)
        if not account:
            continue

        cipher_match = re.match(r"Cipher:\s*(.*)", payload)
        if cipher_match:
            account.cipher = cipher_match.group(1).strip()
            continue

        traffic_match = re.match(
            r"RX:\s*(\d+)\s*bytes,\s*(\d+)\s*pkts,\s*TX:\s*(\d+)\s*bytes,\s*(\d+)\s*pkts,\s*"
            r"Lost packets:\s*(\d+)",
            payload,
        )
        if traffic_match:
            account.rx_bytes = int(traffic_match.group(1))
            account.rx_pkts = int(traffic_match.group(2))
            account.tx_bytes = int(traffic_match.group(3))
            account.tx_pkts = int(traffic_match.group(4))
            account.lost_pkts = int(traffic_match.group(5))
            continue

        outgoing_match = re.match(
            r"Outgoing Calls:\s*(\d+)\s*attempted,\s*(\d+)\s*answered,\s*(\d+)\s*connected,\s*(\d+)\s*failed",
            payload,
        )
        if outgoing_match:
            account.outgoing_attempted = int(outgoing_match.group(1))
            account.outgoing_answered = int(outgoing_match.group(2))
            account.outgoing_connected = int(outgoing_match.group(3))
            account.outgoing_failed = int(outgoing_match.group(4))
            continue

        incoming_match = re.match(
            r"Incoming Calls:\s*(\d+)\s*received,\s*(\d+)\s*answered,\s*(\d+)\s*connected,\s*(\d+)\s*failed",
            payload,
        )
        if incoming_match:
            account.incoming_received = int(incoming_match.group(1))
            account.incoming_answered = int(incoming_match.group(2))
            account.incoming_connected = int(incoming_match.group(3))
            account.incoming_failed = int(incoming_match.group(4))
            continue

        overall_match = re.match(
            r"Overall Calls:\s*(\d+)\s*dropped,\s*Total Call Time\s*=\s*([0-9:]+)",
            payload,
        )
        if overall_match:
            account.dropped_calls = int(overall_match.group(1))
            account.total_call_time = overall_match.group(2)
            continue

        loopback_match = re.match(r"Direct Loopback:\s*(\d+)\s*connected,\s*(\d+)\s*failed", payload)
        if loopback_match:
            account.loopback_connected = int(loopback_match.group(1))
            account.loopback_failed = int(loopback_match.group(2))

    return list(accounts.values())


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


def parse_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        frequency = parse_float(row["Frequency"])
        power = parse_float(row["Power"])
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
        frequency = parse_float(row["Frequency"])
        power = parse_float(row["Power"])
        mse = parse_float(row["MSE"])
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
        freq_start = parse_float(freq_match.group(1))
        freq_end = parse_float(freq_match.group(2))
        power = parse_float(row["Power"])
        mer = parse_float(row["MER"])
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
    }


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
        start = parse_float(match.group(1))
        end = parse_float(match.group(2))
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

    for radio in radio_loads:
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
            st.plotly_chart(fig, use_container_width=True)


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


def render_events(events: List[EventEntry]) -> None:
    st.subheader("Events")
    if not events:
        st.info("Keine Events gefunden.")
        return
    df = pd.DataFrame([event.__dict__ for event in events])
    st.dataframe(df, use_container_width=True)


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
    st.caption("Einschätzung basiert auf durchschnittlichem RSSI: ≥-60 sehr gut, ≥-70 gut, ≥-80 mittel, ≥-90 kritisch, < -90 schlecht.")


@st.cache_data(show_spinner=False)
def parse_support_data(text: str) -> dict:
    access_technology = detect_access_technology(text)
    return {
        "access_technology": access_technology,
        "device_mac": extract_device_mac(text),
        "dsl_data": parse_dsl_snr(text),
        "dsl_metrics": parse_dsl_metrics(text),
        "docsis_data": parse_docsis_channels(text),
        "fiber_data": parse_fiber_overview(text),
        "internet_connection": parse_internet_connection(text),
        "networks": parse_wlan_env_scan(text),
        "stations": parse_wlan_stations(text),
        "radio_loads": parse_wlan_radio_load(text),
        "noisefloor_entries": parse_wlan_noisefloor(text),
        "ports": parse_lan_ports(text),
        "voip_accounts": parse_voip_accounts(text),
        "neighbour_clients": parse_neighbour_clients(text),
        "events": parse_events(text),
        "dect_devices": parse_dect_device_info(text),
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
                    <div class="info-frame-label">{html.escape(label)}</div>
                    <div class="info-frame-value">{html.escape(value)}</div>
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
                <div class="info-frame-label">{html.escape(label)}</div>
                <div class="info-frame-value">{html.escape(value)}</div>
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
    networks = parsed["networks"]
    stations = parsed["stations"]
    radio_loads = parsed["radio_loads"]
    noisefloor_entries = parsed["noisefloor_entries"]
    ports = parsed["ports"]
    voip_accounts = parsed["voip_accounts"]
    neighbour_clients = parsed["neighbour_clients"]
    events = parsed["events"]
    dect_devices = parsed["dect_devices"]

    mac_label = "MACa Adresse"
    mac_value = device_mac or "Keine MAC-Adresse gefunden"
    mac_value_safe = html.escape(mac_value)
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

    tab_dsl, tab_internet, tab_lan, tab_wlan, tab_phone, tab_dect, tab_events = st.tabs(
        [access_technology, "Internet", "LAN", "WLAN", "Telefonie", "DECT", "Events"]
    )
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

    with tab_lan:
        render_lan_ports(ports)
        render_lan_clients(neighbour_clients)

    with tab_wlan:
        render_wlan_scan(networks)
        render_wlan_noisefloor(noisefloor_entries)
        render_wlan_clients(stations)
        render_wlan_radio_load(radio_loads)

    with tab_phone:
        render_telephony(voip_accounts)

    with tab_dect:
        render_dect_devices(dect_devices)

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
            [data-testid="stDeployButton"],
            [data-testid="stToolbar"],
            [data-testid="stHeader"] {
                display: none;
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

    text = uploaded_file.read().decode("utf-8", errors="ignore")
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
