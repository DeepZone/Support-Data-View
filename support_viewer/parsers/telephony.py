import re
from typing import List

from support_viewer.models import TelephonyAccount
from support_viewer.utils import extract_section_by_prefix


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

