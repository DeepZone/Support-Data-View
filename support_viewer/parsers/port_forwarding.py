import re
from typing import List, Optional

from support_viewer.models import PortForwarding
from support_viewer.utils import extract_section_by_prefix


def parse_port_forwardings(text: str) -> List[PortForwarding]:
    section = extract_section_by_prefix(text, "##### BEGIN SECTION port_forwards IPv4 forwardings")
    if not section:
        return []

    entries: List[PortForwarding] = []
    current_entry: Optional[PortForwarding] = None
    forwarding_pattern = re.compile(
        r'^(?P<service>\S+)\s+'
        r'(?P<protocol>TCP|UDP|IP)\s+'
        r'(?P<target_ip>\S+)\s+'
        r'(?P<target_port>\S+)\s+'
        r'(?P<public_ip>\S+)\s+'
        r'(?P<public_port>\S+)\s+'
        r'"(?P<description>[^"]*)"\s*$',
    )

    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("##### BEGIN SECTION") or line.startswith("--- Active IPv4 Portforwardings ---"):
            continue
        if line.startswith("allow-only-from"):
            if current_entry:
                current_entry.allow_only_from = line.replace("allow-only-from", "", 1).strip()
            continue

        match = forwarding_pattern.match(line)
        if not match:
            continue

        current_entry = PortForwarding(
            service=match.group("service"),
            protocol=match.group("protocol"),
            target_ip=match.group("target_ip"),
            target_port=match.group("target_port"),
            public_ip=match.group("public_ip"),
            public_port=match.group("public_port"),
            description=match.group("description") or None,
        )
        entries.append(current_entry)
    return entries
