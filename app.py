import re
from dataclasses import dataclass
import sys
import textwrap
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


def extract_value(block: str, key: str) -> Optional[str]:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+)$", block, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'")


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


def detect_access_technology(text: str) -> str:
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


def render_dsl_metrics(metrics: dict) -> None:
    st.subheader("DSL Leitungswerte")
    if not metrics:
        st.info("Keine detaillierten DSL-Leitungswerte gefunden.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Leitungslänge", format_meters(metrics.get("loop_length_m")))
    col1.metric("Sync Downstream", format_mbit(metrics.get("ds_rate_kbits")))
    col1.metric("Sync Upstream", format_mbit(metrics.get("us_rate_kbits")))

    col2.metric("SNR Downstream", format_db(metrics.get("ds_margin_db")))
    col2.metric("SNR Upstream", format_db(metrics.get("us_margin_db")))
    col2.metric("Leitungsdämpfung DS", format_db(metrics.get("ds_attenuation_db")))

    col3.metric("Leitungsdämpfung US", format_db(metrics.get("us_attenuation_db")))
    col3.metric("FEC (DS/US)", f"{format_count(metrics.get('ds_total_fec'))} / {format_count(metrics.get('us_total_fec'))}")
    col3.metric("CRC (DS/US)", f"{format_count(metrics.get('ds_total_crc'))} / {format_count(metrics.get('us_total_crc'))}")

    st.metric("ES (DS/US)", f"{format_count(metrics.get('ds_es'))} / {format_count(metrics.get('us_es'))}")
    st.metric("Resyncs (24h)", format_count(metrics.get("resyncs_24h")))
    st.metric("Host triggered Retrains (24h)", format_count(metrics.get("retrains_24h")))

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


def render_wlan_scan(networks: List[WifiNetwork]) -> None:
    st.subheader("WLAN Umgebung (Scan)")
    if not networks:
        st.info("Keine WLAN-Scan-Daten gefunden.")
        return
    df = pd.DataFrame([network.__dict__ for network in networks])
    df = df.sort_values("rssi", ascending=False)
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


def render_lan_ports(ports: List[LanPort]) -> None:
    st.subheader("LAN/WAN Ports")
    if not ports:
        st.info("Keine LAN/WAN-Portinformationen gefunden.")
        return
    df = pd.DataFrame([port.__dict__ for port in ports])
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


def render_events(events: List[EventEntry]) -> None:
    st.subheader("Events")
    if not events:
        st.info("Keine Events gefunden.")
        return
    df = pd.DataFrame([event.__dict__ for event in events])
    st.dataframe(df, use_container_width=True)


def build_dashboard(text: str) -> None:
    st.header("Support-Data-Visualisierung")
    st.caption("Fokus auf DSL, WLAN, Telefonie und LAN-Status.")

    access_technology = detect_access_technology(text)
    dsl_data = parse_dsl_snr(text)
    dsl_metrics = parse_dsl_metrics(text)
    networks = parse_wlan_env_scan(text)
    stations = parse_wlan_stations(text)
    ports = parse_lan_ports(text)
    voip_accounts = parse_voip_accounts(text)
    neighbour_clients = parse_neighbour_clients(text)
    events = parse_events(text)

    tab_dsl, tab_lan, tab_wlan, tab_phone, tab_events = st.tabs(
        [access_technology, "LAN", "WLAN", "Telefonie", "Events"]
    )
    with tab_dsl:
        render_dsl_charts(dsl_data)
        render_dsl_metrics(dsl_metrics)

    with tab_lan:
        render_lan_ports(ports)
        render_lan_clients(neighbour_clients)

    with tab_wlan:
        render_wlan_scan(networks)
        render_wlan_clients(stations)

    with tab_phone:
        render_telephony(voip_accounts)

    with tab_events:
        render_events(events)


def _is_running_with_streamlit() -> bool:
    return get_script_run_ctx(suppress_warning=True) is not None


def main() -> None:
    st.set_page_config(page_title="Support-Data-View", layout="wide")
    st.title("Support-Data-View")
    st.markdown(
        "Lade eine Support-Data TXT hoch (z. B. von einer FRITZ!Box), "
        "um DSL- und WLAN-Informationen grafisch auszuwerten."
    )

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
