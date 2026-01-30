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


def parse_dsl_snr(text: str) -> dict:
    section = extract_section_by_prefix(text, "DSL Spectrum")
    return {
        "SNR Array DS": extract_numeric_array(section, "SNR Array DS"),
        "SNR Array US": extract_numeric_array(section, "SNR Array US"),
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
    st.subheader("DSL Spektrum (SNR)")
    if not dsl_data["SNR Array DS"] and not dsl_data["SNR Array US"]:
        st.info("Keine DSL-SNR-Spektrumsdaten gefunden.")
        return

    if dsl_data["SNR Array DS"]:
        fig_ds = px.line(
            y=dsl_data["SNR Array DS"],
            labels={"index": "Ton", "y": "SNR (DS)"},
            title="SNR Array DS",
        )
        st.plotly_chart(fig_ds, use_container_width=True)

    if dsl_data["SNR Array US"]:
        fig_us = px.line(
            y=dsl_data["SNR Array US"],
            labels={"index": "Ton", "y": "SNR (US)"},
            title="SNR Array US",
        )
        st.plotly_chart(fig_us, use_container_width=True)


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
    st.dataframe(df, use_container_width=True)

    chart_df = df.copy()
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


def build_dashboard(text: str) -> None:
    st.header("Support-Data-Visualisierung")
    st.caption("Fokus auf DSL-Spektrum, WLAN-Umgebung, WLAN-Clients und LAN-Status.")

    dsl_data = parse_dsl_snr(text)
    networks = parse_wlan_env_scan(text)
    stations = parse_wlan_stations(text)
    ports = parse_lan_ports(text)

    render_dsl_charts(dsl_data)
    render_wlan_scan(networks)
    render_wlan_clients(stations)
    render_lan_ports(ports)

    st.subheader("LAN Clients")
    st.info(
        "In der hochgeladenen Datei wurde keine explizite LAN-Clientliste gefunden. "
        "Wenn eine Hosts-Liste oder ein Mesh-Export vorhanden ist, wird dieser Bereich automatisch gefüllt."
    )


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
