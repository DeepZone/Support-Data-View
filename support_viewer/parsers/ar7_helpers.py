import re
from typing import List, Optional

from support_viewer.utils import extract_section_by_prefix, extract_value


def extract_ar7cfg_body(text: str) -> str:
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


def strip_ar7_value_quotes(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip().rstrip(';').strip().strip('"').strip("'")


def find_ar7_block_value(block: str, key: str) -> Optional[str]:
    return strip_ar7_value_quotes(extract_value(block, key))


def extract_ar7_named_blocks(text: str, block_name: str) -> List[str]:
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
