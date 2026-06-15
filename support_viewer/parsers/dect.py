import re
from typing import Dict, List, Optional

from support_viewer.models import DectBasisInfo, DectDevice
from support_viewer.utils import extract_section_by_prefix, parse_int, parse_optional_float


DEFAULT_DECT_RSSI_INDEX_TO_DBM = {
    1: -92.7,
    2: -94.9,
    3: -92.7,
    4: -79.7,
    5: -45.1,
    6: -75.4,
    7: -92.7,
    8: -90.5,
    9: -73.2,
    10: -38.6,
}


DECT_MODEL_NAMES = {
    "0": "Fremdgerät",
    "1": "FRITZ!Fon MT-D",
    "2": "Speedphone (MT-D OEM Gerät für die Telekom)",
    "3": "FRITZ!Fon MT-F",
    "3.1": "FRITZ!Fon MT-F",
    "4": "FRITZ!Fon C3",
    "5": "FRITZ!Fon M2",
    "5.1": "FRITZ!Fon M2",
    "8": "FRITZ!Fon C4",
    "8.1": "FRITZ!Fon C4",
    "8.2": "FRITZ!Fon C5",
    "8.3": "FRITZ!Fon C6",
    "8.4": "FRITZ!Fon X6",
    "12.1": "FRITZ!Fon M3",
    "213": "FRITZ!Fon MT-C (eigentlich Swisscom-Gerät)",
}


def extract_dect_rssi_index_to_dbm(text: str) -> Dict[int, float]:
    match = re.search(r"DECT_RSSI_INDEX_TO_DBM\s*[:=]\s*([^\n\r]+)", text)
    if not match:
        return DEFAULT_DECT_RSSI_INDEX_TO_DBM

    raw_values = re.findall(r"-\d+(?:\.\d+)?", match.group(1))
    if len(raw_values) != 10:
        return DEFAULT_DECT_RSSI_INDEX_TO_DBM

    parsed_values = [float(value) for value in raw_values]
    if not all(value < 0 for value in parsed_values):
        return DEFAULT_DECT_RSSI_INDEX_TO_DBM

    return {index: value for index, value in enumerate(parsed_values, start=1)}


def parse_dect_rssi_value(value: str, dect_rssi_index_to_dbm: Dict[int, float]) -> Optional[float]:
    parsed_value = parse_optional_float(value)
    if parsed_value is None:
        return None
    mapped_value = dect_rssi_index_to_dbm.get(int(parsed_value)) if parsed_value.is_integer() else None
    return mapped_value if mapped_value is not None else parsed_value


def parse_dect_model(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    model_key = value.strip()
    if not model_key:
        return None
    model_name = DECT_MODEL_NAMES.get(model_key)
    return f"{model_name} ({model_key})" if model_name else model_key


def parse_dect_device_info(text: str, dect_rssi_index_to_dbm: Dict[int, float]) -> List[DectDevice]:
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

        rssi_values = [parse_dect_rssi_value(v, dect_rssi_index_to_dbm) for v in values1[28:38] if v and v != "-"]
        hg_rssi_values = [parse_dect_rssi_value(v, dect_rssi_index_to_dbm) for v in values2[13:23] if v and v != "-"] if len(values2) >= 23 else []

        devices.append(
            DectDevice(
                name=values1[0],
                hgid=parse_int(values1[2]),
                model=parse_dect_model(values1[3]),
                ipui=values1[5] or None,
                curr_codec=values1[7] or None,
                ber=parse_optional_float(values1[27]),
                rssi_values=[v for v in rssi_values if v is not None],
                hg_ber=parse_optional_float(values2[12]) if len(values2) >= 13 else None,
                hg_rssi_values=[v for v in hg_rssi_values if v is not None],
                no_emission=parse_int(values2[23]) if len(values2) >= 24 else None,
                fw_version=values2[24] if len(values2) >= 25 and values2[24] else None,
            )
        )
    return devices


def parse_dect_basis_info(text: str) -> Optional[DectBasisInfo]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION DECTBasisInfo")
    if not section:
        return None

    basis_line = next((line.strip() for line in section.splitlines() if line.strip().startswith("Basis ")), "")
    if not basis_line:
        return None

    values = {}
    for part in basis_line.replace("Basis ", "", 1).split(","):
        key, sep, value = part.strip().partition("=")
        if sep:
            values[key.strip()] = value.strip()

    rfpi_line = next((line.strip() for line in section.splitlines() if line.strip().startswith("RFPI=")), "")
    rfpi = rfpi_line.split("=", 1)[1].strip() if "=" in rfpi_line else None

    return DectBasisInfo(
        dect_enabled=parse_int(values.get("DECT_ENABLED")),
        dect_repeater_enabled=parse_int(values.get("DECT_REPEATER_ENABLED")),
        eco_mode=parse_int(values.get("ECOMode")),
        no_emission=parse_int(values.get("NoEmission")),
        no_emission_state=parse_int(values.get("NoEmissionState")),
        repeater_mode=parse_int(values.get("RepeaterMode")),
        overlapped_sending=parse_int(values.get("OverlappedSending")),
        ext_security=parse_int(values.get("ExtSecurity")),
        catiq20support=parse_int(values.get("CATIQ20SUPPORT")),
        pin_protect=parse_int(values.get("PINProtect")),
        avmuleaes=parse_int(values.get("AVMULEAES")),
        rfpi=rfpi,
    )
