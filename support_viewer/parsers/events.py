import re
from typing import List

from support_viewer.models import EventEntry
from support_viewer.utils import extract_section_by_prefix


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
