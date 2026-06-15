import html
import re
from typing import List, Optional, Tuple


def escape_html(value: object) -> str:
    """Escape support-data derived values before rendering custom HTML."""
    return html.escape(str(value), quote=True)


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


def extract_value(block: str, key: str) -> Optional[str]:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+)$", block, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'")


def extract_numeric_array(text: str, label: str) -> List[int]:
    match = re.search(rf"{re.escape(label)}:\s*([0-9,\-]+)", text)
    if not match:
        return []
    values = [int(value) for value in match.group(1).split(",") if value.strip()]
    return values


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


def parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if not value or value == "-":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_channel_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    normalized = str(value).strip().replace(",", ".")
    if not normalized:
        return None
    try:
        return float(normalized)
    except (TypeError, ValueError):
        return None


def parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_frequency_range(value: Optional[str]) -> Optional[Tuple[float, float]]:
    if value is None:
        return None
    match = re.match(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*", str(value))
    if not match:
        return None
    start = parse_channel_float(match.group(1))
    end = parse_channel_float(match.group(2))
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    return (start, end)
