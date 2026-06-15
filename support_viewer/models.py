from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


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
class RatelimiterRuntimeEntry:
    scope: str
    rule: str
    packets: int
    interval_seconds: int
    hits: int
    blocked: int


@dataclass
class RatelimiterConfigEntry:
    name: str
    iface: str
    rule: str
    packets: int
    interval: str
    early: int
    enabled: bool


@dataclass
class HardwareRatelimiterSession:
    source_ip: str
    destination_ip: str
    source_port: Optional[int]
    destination_port: Optional[int]
    matched_packets: int
    matched_bytes: int
    rule_type: Optional[str]
    catchall: bool


@dataclass
class ConnectionPerformanceFinding:
    category: str
    severity: str
    title: str
    details: str


@dataclass
class MeshTopology:
    nodes: List[dict]
    links: List[dict]
    error: Optional[str] = None


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
class PortForwarding:
    service: str
    protocol: str
    target_ip: str
    target_port: str
    public_ip: str
    public_port: str
    description: Optional[str]
    allow_only_from: Optional[str] = None


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


@dataclass
class DectBasisInfo:
    dect_enabled: Optional[int]
    dect_repeater_enabled: Optional[int]
    eco_mode: Optional[int]
    no_emission: Optional[int]
    no_emission_state: Optional[int]
    repeater_mode: Optional[int]
    overlapped_sending: Optional[int]
    ext_security: Optional[int]
    catiq20support: Optional[int]
    pin_protect: Optional[int]
    avmuleaes: Optional[int]
    rfpi: Optional[str]


@dataclass
class Ar7Interface:
    name: str
    ipaddr: Optional[str]
    netmask: Optional[str]
    dhcp_start: Optional[str]
    dhcp_end: Optional[str]


@dataclass
class Ar7BridgeInterface:
    name: Optional[str]
    ipaddr: Optional[str]
    netmask: Optional[str]
    dhcp_start: Optional[str]
    dhcp_end: Optional[str]


@dataclass
class Ar7VccEntry:
    vpi: Optional[str]
    vci: Optional[str]
    dsl_encap: Optional[str]


@dataclass
class Ar7VlanEntry:
    vlanid: Optional[str]
    vlanprio: Optional[str]
    tos: Optional[str]


@dataclass
class Ar7DslIface:
    name: Optional[str]
    enabled: Optional[str]
    dsl_encap: Optional[str]
    dsl_interface_name: Optional[str]
    stackmode: Optional[str]
    weight: Optional[str]
    vlan_encap: Optional[str]
    vlan_id: Optional[str]
    vlan_prio: Optional[str]


@dataclass
class Ar7Overview:
    mode: Optional[str]
    active_provider: Optional[str]
    bridge_interfaces: List[Ar7BridgeInterface]
    vccs: List[Ar7VccEntry]
    vlans: List[Ar7VlanEntry]
    dsl_ifaces: List[Ar7DslIface]


@dataclass
class Ar7NetworkSettings:
    mode: Optional[str]
    ipv4_mode: Optional[str]
    ipv6_mode: Optional[str]
    mtu: Optional[str]
    wan_vlan: Optional[str]
    tr069: Optional[str]
    snmp_wan: Optional[str]
    dyn_dns: Optional[str]
    email_reports: Optional[str]
    expert_mode: Optional[str]
    hidden_menus: List[str]
    dns_servers: List[str]
    interfaces: Dict[str, Ar7Interface]


@dataclass
class AvmCounterSection:
    title: str
    content: str


@dataclass
class AvmCounterValueEntry:
    category: str
    metric: str
    direction: str
    value: int
    value_type: str
    age_seconds: int
