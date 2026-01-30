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
    dsl_metrics = parse_dsl_metrics(text)
    networks = parse_wlan_env_scan(text)
    stations = parse_wlan_stations(text)
    ports = parse_lan_ports(text)

    render_dsl_charts(dsl_data)
    render_dsl_metrics(dsl_metrics)
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
