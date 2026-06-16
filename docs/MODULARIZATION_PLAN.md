# Modularisierungsplan für `app.py`

Stand: 2026-06-16. Diese Datei ist eine reine Analyse- und Plan-Datei; sie beschreibt den aktuellen Modularisierungsstand und die verbleibenden risikoarmen Schritte zur Trennung von Parserlogik, Datenmodellen und Streamlit-Rendering ohne funktionale Änderungen.

## 0. Aktueller Modularisierungsstand am 2026-06-16

Die Modularisierung ist inzwischen gestartet. `app.py` ist weiterhin die zentrale Streamlit-Anwendung und Kompatibilitätsfassade, importiert aber bereits Modelle, allgemeine Helper und mehrere Parser aus dem Paket `support_viewer`. Diese Dokumentation beschreibt den Ist-Zustand; sie ist keine Vorgabe für App-Codeänderungen in diesem PR.

### 0.1 Inzwischen existierende Module

- `support_viewer/models.py`: enthält die bisher zentralisierten Dataclasses für WLAN, LAN/Mesh, Telefonie, Events, Ratelimiter/PPE-Findings, Internet, Portfreigaben, DECT, AR7 und AVM-Counter.
- `support_viewer/utils.py`: enthält frameworkfreie Text-, Zahlen- und Bereichshelper wie Section-Extraktion, Wert-Extraktion, optionale Float-/Integer-Parser, HTML-Escaping und Frequenzbereichs-Parsing.
- `support_viewer/parsers/events.py`: enthält `parse_events()`.
- `support_viewer/parsers/telephony.py`: enthält `parse_voip_accounts()` samt lokalem Registrierungsstatus-Helper.
- `support_viewer/parsers/dect.py`: enthält DECT-RSSI-Mapping, DECT-RSSI-Wertparser, Modellnormalisierung, Geräteparser und Basisinfo-Parser.
- `support_viewer/parsers/port_forwarding.py`: enthält `parse_port_forwardings()`.
- `support_viewer/parsers/internet_connection.py`: enthält `parse_internet_connection()` sowie InternetConnection-spezifische private Helper.
- `support_viewer/parsers/ar7.py`: enthält `parse_ar7_overview()`, `parse_ar7_network_settings()` und AR7-spezifische Auswertungshelper wie `_dsl_encap_label()` und `_extract_hidden_menus()`.
- `support_viewer/parsers/ar7_helpers.py`: enthält wiederverwendete AR7-Extraktionshelper für `ar7cfg`, Quotes, Blockwerte und benannte Blöcke.

### 0.2 Parser und Analysefunktionen, die weiterhin in `app.py` liegen

Folgende Parserdomänen sind noch nicht ausgelagert und bleiben aktuell in `app.py`:

- **DSL:** `extract_training_state()`, `is_showtime_state()`, `parse_dsl_snr()`, `parse_dsl_metrics()` und die Nutzung allgemeiner DSL-Extraktionshelper.
- **Fiber:** `parse_fiber_overview()`.
- **DOCSIS/Cable:** `extract_docsis_state()`, `parse_docsis_value()`, `parse_table_rows()`, `is_plausible_channel()`, `parse_docsis_channels()`, `parse_cable_spectrum()`, `build_cable_usage_ranges()`.
- **WLAN/LAN/Mesh/Netzauslastung:** `format_radio_label()`, `parse_wlan_env_scan()`, `parse_wlan_stations()`, `parse_wlan_radio_load()`, `parse_wlan_noisefloor()`, `parse_lan_ports()`, `parse_neighbour_clients()`, `parse_mesh_topology()`, `build_mesh_positions()`, `is_mesh_client_connected()`, `parse_avm_counter_rrd_sections()`, `parse_avm_counter_values()`, `summarize_avm_counter_values()`.
- **Metadaten und Upload-nahe Parser:** `extract_device_mac()`, `parse_fritz_model()`, `parse_fritz_uptime_line()`, `parse_fritz_uptime_days_minutes()`, `parse_fritz_load_average()`, `parse_fritz_firmware_version()`.
- **Access-Erkennung:** `detect_access_technology()` bleibt als Cross-Domain-Erkennung in `app.py`.
- **PPE/HWPA/VLAN/Ratelimiter/Performance:** alle Parser-, Korrelations- und Analysefunktionen dieser Domäne, einschließlich `parse_ratelimiter_runtime()`, `parse_ratelimiter_config()`, `parse_hardware_ratelimiter_sessions()`, `analyze_hardware_ratelimiter_sessions()`, `parse_drop_indicators()`, `parse_offload_indicators()`, PPE-Parser, VLAN-/Networking-Korrelation, `parse_ppe_diagnosis()` und `analyze_connection_performance()`.
- **Orchestrierung:** `parse_support_data()` bleibt in `app.py` und ruft sowohl ausgelagerte als auch noch lokale Parser auf.

Renderingfunktionen liegen weiterhin vollständig in `app.py`; es existiert noch kein `support_viewer/rendering/`-Paket.

### 0.3 Bewusste `app.<name>`-Re-Exports als Kompatibilitätsschicht

`app.py` importiert ausgelagerte Namen weiterhin auf Modulebene. Dadurch bleiben bestehende Tests, Skripte oder Nutzer, die `import app` verwenden, kompatibel. Diese Re-Exports sind bewusst und sollten bis zu einer ausdrücklich geplanten Breaking-Change-Phase erhalten bleiben. Dazu gehören insbesondere:

- Dataclasses aus `support_viewer.models`, z. B. `WifiStation`, `WifiNetwork`, `LanPort`, `TelephonyAccount`, `EventEntry`, `InternetConnection`, `PortForwarding`, `DectDevice`, `DectBasisInfo`, `Ar7Overview`, `Ar7NetworkSettings`, `AvmCounterSection` und weitere Modelle.
- Utility-Funktionen aus `support_viewer.utils`, z. B. `escape_html()`, `extract_section()`, `extract_section_by_prefix()`, `extract_value()`, `extract_numeric_array()`, `extract_numeric_array_loose()`, `extract_int_value()`, `extract_float_value()`, `extract_kbits_rate()`, `extract_section_block()`, `extract_section_between()`, `parse_optional_float()`, `parse_channel_float()`, `parse_int()` und `_parse_frequency_range()`.
- Ausgelagerte Parser aus `support_viewer.parsers.*`, z. B. `parse_events()`, `parse_voip_accounts()`, `extract_dect_rssi_index_to_dbm()`, `parse_dect_rssi_value()`, `parse_dect_model()`, `parse_dect_device_info()`, `parse_dect_basis_info()`, `parse_port_forwardings()`, `parse_internet_connection()`, `parse_ar7_overview()` und `parse_ar7_network_settings()`.

### 0.4 Noch offene Bereiche für spätere Modularisierung

- **DSL:** weiterhin in `app.py`; sinnvoller nächster reiner Struktur-PR, falls keine Featurearbeit priorisiert wird.
- **Fiber:** weiterhin in `app.py`; klein und gut isolierbar.
- **DOCSIS/Cable:** weiterhin in `app.py`; wegen Tabellenlogik und Plausibilitätsfiltern etwas risikoreicher als Fiber.
- **WLAN/LAN/Mesh:** weiterhin in `app.py`; enthält Parser, Layout-/Positionslogik und Rendering-nahe Übergänge.
- **PPE/HWPA/VLAN:** weiterhin in `app.py`; größte und am stärksten gekoppelte Domäne, daher später und in mehreren PRs.
- **Rendering/UI:** weiterhin in `app.py`; sollte erst nach stabilen Parsermodulen verschoben werden.
- **`parse_support_data()`-Orchestrierung:** weiterhin in `app.py`; kann später nach `support_viewer/orchestrator.py`, sobald die Mehrheit der Parser stabil ausgelagert ist.

### 0.5 Zeitpunkt für neue Features

Neue Features können ab jetzt sinnvoll eingebaut werden. Sie sollten jedoch als getrennte Feature-PRs laufen und nicht mit reinen Struktur-/Move-PRs vermischt werden. Dadurch bleiben Regressionsrisiko und Review-Aufwand niedrig: Struktur-PRs verschieben Code ohne fachliche Änderung, Feature-PRs ändern Verhalten gezielt und testbar.

Empfehlung für den ersten Feature-PR: ein kleines, parsernahes Feature mit klarer synthetischer Testabdeckung wählen, z. B. eine zusätzliche Fiber- oder DSL-Kennzahl. Wenn vorher noch ein Struktur-PR gewünscht ist, ist Fiber weiterhin die risikoärmste nächste Auslagerung.

## 1. Aktuelle Struktur

`app.py` ist ein einzelnes Streamlit-Modul mit ca. 6.400 Zeilen. Es enthält derzeit fünf Arten von Verantwortung:

1. **Datenmodelle**: Dataclasses am Dateianfang.
2. **Parser und Analysefunktionen**: reine Textparser, Normalisierung, Korrelation und Qualitätsbewertung.
3. **Rendering/UI**: Streamlit-, Plotly-, Pandas-DataFrame- und HTML-Erzeugung.
4. **Orchestrierung**: `parse_support_data()` sammelt Parserergebnisse, `build_dashboard()` verdrahtet Daten mit Tabs und Renderern.
5. **App-Einstieg und Upload/Runtime**: Streamlit-Page-Config, Upload-Decoding, Dateinamen-/Größenprüfung und Laufzeitprüfung.

Die aktuelle Importlage zeigt die Kopplung: Parser brauchen überwiegend `re`, `json`, `ipaddress`, Dataclasses und Typing. Rendering benötigt zusätzlich `html`, `textwrap`, `pandas`, `plotly`, `streamlit` und `streamlit.components`. Upload/App-Einstieg benötigt `base64`, `zlib`, `sys` und Streamlit-Runtime.

## 2. Parser-Domänen und heutige Funktionsgruppen

### 2.1 WLAN

**Modelle:** `WifiStation`, `WifiNetwork`, `WifiRadioLoad`, `WifiNoiseFloorEntry`.

**Parser/Helper:**
- `format_radio_label()`
- `parse_wlan_env_scan()`
- `parse_wlan_stations()`
- `parse_wlan_radio_load()`
- `parse_wlan_noisefloor()`

**Rendering:**
- `render_wlan_scan()`
- `render_wlan_noisefloor()`
- `render_wlan_clients()`
- `render_wlan_radio_load()`

**Besonderheit:** `WifiRadioLoad` trägt aktuell ein `pd.DataFrame` direkt im Modell. Das macht den WLAN-Parser von Pandas abhängig. Risikoarm ist zunächst, diese Abhängigkeit unverändert mitzunehmen. Erst später sollte geprüft werden, ob das Modell rohe Reihen enthält und DataFrames nur im Rendering gebaut werden.

### 2.2 DSL

**Modelle:** keine spezifische Dataclass; DSL-Spektrum und Metriken werden als Dictionaries/Listen zurückgegeben.

**Parser/Helper:**
- `extract_training_state()`
- `is_showtime_state()`
- `parse_dsl_snr()`
- `extract_numeric_array_loose()`
- `extract_int_value()`
- `extract_float_value()`
- `extract_kbits_rate()`
- `extract_section_block()`
- `extract_section_between()`
- `parse_dsl_metrics()`
- `detect_access_technology()` nutzt DSL-, DOCSIS- und Fiber-Indikatoren.

**Rendering/Qualität:**
- `render_dsl_charts()`
- `format_sync_rate()`
- `format_db()`
- `format_count()`
- `format_meters()`
- `render_metric_rows()`
- `assess_line_quality()`
- `render_dsl_metrics()`

**Besonderheit:** `detect_access_technology()` ist fachlich eine Cross-Domain-Erkennung. Sie sollte nicht in `dsl.py` landen, sondern in einem Orchestrierungs- oder Access-Modul.

### 2.3 DOCSIS/Cable

**Modelle:** keine spezifische Dataclass; Rückgabe erfolgt über Dictionaries/Listen.

**Parser/Helper:**
- `extract_docsis_state()`
- `parse_docsis_value()`
- `parse_table_rows()`
- `parse_channel_float()`
- `parse_int()`
- `is_plausible_channel()`
- `parse_docsis_channels()`
- `parse_cable_spectrum()`
- `_parse_frequency_range()`
- `build_cable_usage_ranges()`

**Rendering/Qualität:**
- `format_mbit()`
- `format_db()`
- `format_count()`
- `assess_cable_quality()`
- `assess_cable_limits()`
- `render_cable_dashboard()`
- `connection_quality_label()` wird auch für DSL-nahe Darstellung genutzt und sollte daher erst später verschoben werden.

### 2.4 Fiber/PON/AON

**Modelle:** keine spezifische Dataclass; Rückgabe erfolgt als Dictionary.

**Parser:**
- `parse_fiber_overview()`

**Rendering:**
- `render_fiber_dashboard()`

**Besonderheit:** Fiber ist im aktuellen Testbestand relativ gut isoliert und eignet sich sehr gut als erste echte Parser-Auslagerung nach vorbereitenden Schritten.

### 2.5 PPE/HWPA/VLAN-Service-Korrelation

**Modelle:**
- `RatelimiterRuntimeEntry`
- `RatelimiterConfigEntry`
- `HardwareRatelimiterSession`
- `ConnectionPerformanceFinding`

**Parser/Analyse:**
- `parse_ratelimiter_runtime()`
- `parse_ratelimiter_config()`
- `parse_hardware_ratelimiter_sessions()`
- `analyze_hardware_ratelimiter_sessions()`
- `parse_drop_indicators()`
- `parse_offload_indicators()`
- `_extract_section_containing()`
- `_extract_ppe_raw_blocks()`
- `_parse_ppe_int()`
- `_classify_ppe_interface()`
- `_describe_ppe_role()`
- `_infer_base_from_name()`
- `parse_ppe_if_map()`
- `parse_hwpa_interfaces()`
- `_is_expected_ppe_netdev()`
- `parse_ppe_device_only()`
- `parse_common_ppe_offload_counters()`
- `parse_ppe_summary_and_state()`
- `parse_ppe_sessions()`
- `_hex_and_decimal()`
- `parse_ppe_mtu_mru()`
- `parse_ppe_flow_control()`
- `parse_ppe_portshaper()`
- `parse_ppe_kernel_modules()`
- `build_ppe_device_tree()`
- `_severity_rank()`
- `_severity_label()`
- `_analyze_ppe_diagnosis()`
- `_build_ppe_developer_summary()`
- `_extract_named_sections()`
- `_extract_networking_raw_blocks()`
- `_detect_vlan_from_name()`
- `_network_interface_type()`
- `_get_or_create_network_interface()`
- `_append_unique()`
- `_add_network_evidence()`
- `parse_network_interfaces_from_supportdata()`
- `_normalize_wan_service_name()`
- `_display_wan_service()`
- `_new_wan_service_vlan()`
- `_merge_wan_service_value()`
- `_add_wan_service_evidence()`
- `_upsert_wan_service_vlan()`
- `parse_wan_service_vlans_from_networking()`
- `_expected_ppe_vlan_name()`
- `correlate_wan_service_vlans_with_ppe()`
- `_detect_service_from_text()`
- `_service_evidence_key()`
- `_merge_ppe_vlan_rows()`
- `build_ppe_network_correlation()`
- `parse_ppe_diagnosis()`
- `analyze_connection_performance()`

**Rendering:**
- `_ppe_dataframe()`
- `_render_missing_ppe_section()`
- `_format_ppe_evidence()`
- `_render_ppe_network_correlation()`
- `render_ppe_diagnosis()`
- `render_ratelimiter()`

**Besonderheit:** Diese Domäne ist groß, intern stark gekoppelt und enthält sowohl Parser als auch Diagnose/Rendering. Sie sollte spät und in mehreren PRs ausgelagert werden.

### 2.6 Internet/AR7/Portforwarding

**Modelle:**
- `InternetConnection`
- `PortForwarding`
- `Ar7Interface`
- `Ar7BridgeInterface`
- `Ar7VccEntry`
- `Ar7VlanEntry`
- `Ar7DslIface`
- `Ar7Overview`
- `Ar7NetworkSettings`

**Parser/Helper:**
- `_parse_internet_connections()`
- `_normalize_dns()`
- `parse_internet_connection()`
- `parse_port_forwardings()`
- `_extract_ar7cfg_body()`
- `_strip_quotes()`
- `_format_toggle_state()`
- `_mode_label()`
- `_ipv4_label()`
- `_ipv6_label()`
- `_find_block_value()`
- `_extract_hidden_menus()`
- `_extract_named_blocks()`
- `_dsl_encap_label()`
- `parse_ar7_overview()`
- `parse_ar7_network_settings()`
- `_cidr_from_netmask()` is rendering-adjacent but AR7-specific.

**Rendering:**
- `render_internet_connection()`
- `render_port_forwardings()`
- `render_ar7_overview()`
- `render_network_settings()`

### 2.7 Telefonie/DECT/Events

**Modelle:**
- `TelephonyAccount`
- `EventEntry`
- `DectDevice`
- `DectBasisInfo`

**Parser/Helper:**
- `parse_events()`
- `parse_voip_accounts()`
- `extract_dect_rssi_index_to_dbm()`
- `parse_dect_rssi_value()`
- `parse_dect_model()`
- `parse_dect_device_info()`
- `parse_dect_basis_info()`
- `_format_binary_state()`
- `_format_no_emission_mode()`
- `_format_repeater_mode()`
- `assess_dect_rssi()`

**Rendering:**
- `render_telephony()`
- `render_dect_basis_info()`
- `render_dect_devices()`
- `render_events()`

### 2.8 Allgemeine Helper und Metadaten

**Upload/App:**
- `is_allowed_support_data_filename()`
- `decode_support_data_upload()`
- `_is_running_with_streamlit()`
- `main()`

**Allgemeine Parserhelpers:**
- `extract_section()`
- `extract_section_by_prefix()`
- `extract_numeric_array()`
- `extract_value()`
- `extract_device_mac()`
- `parse_fritz_model()`
- `parse_fritz_uptime_line()`
- `parse_fritz_uptime_days_minutes()`
- `parse_fritz_load_average()`
- `parse_fritz_firmware_version()`
- `parse_optional_float()`
- `_anonymize_ip()`

**Allgemeines Rendering/Formatierung:**
- `escape_html()`
- `format_bool()`
- `format_bytes()`
- teilweise `format_mbit()`, `format_db()`, `format_count()` wenn domainübergreifend genutzt.

**LAN/Mesh/Netzauslastung:**
- Modelle: `LanPort`, `NeighbourClient`, `MeshTopology`, `AvmCounterSection`, `AvmCounterValueEntry`.
- Parser: `parse_lan_ports()`, `parse_neighbour_clients()`, `parse_mesh_topology()`, `build_mesh_positions()`, `is_mesh_client_connected()`, `parse_avm_counter_rrd_sections()`, `parse_avm_counter_values()`, `summarize_avm_counter_values()`.
- Rendering: `render_lan_ports()`, `render_lan_clients()`, `render_mesh_topology()`, `render_network_utilization()`.

## 3. `parse_support_data()` als zentrale Orchestrierung

`parse_support_data()` sollte mittelfristig in einem Orchestrierungsmodul bleiben und nicht in eine einzelne Fachdomäne verschoben werden. Es führt aktuell folgende zentralen Schritte aus:

1. Zugangstechnologie erkennen: `detect_access_technology(text)`.
2. DECT-RSSI-Mapping vorbereiten: `extract_dect_rssi_index_to_dbm(text)`.
3. Ratelimiter-Runtime/Config/Sessions parsen.
4. Ratelimiter-Sessions analysieren.
5. Fachparser für DSL, DOCSIS, Fiber, Internet, AR7, WLAN, LAN, Telefonie, Events, Mesh, Netzauslastung und PPE aufrufen.
6. Anschluss-Performance aus Ratelimiterdaten, Load Average und Rohtext analysieren.
7. Ein flaches Dictionary für `build_dashboard()` zurückgeben.

Risikoarme Zwischenlösung: Die Signatur und Dictionary-Keys von `parse_support_data()` bleiben unverändert. Nach jeder Auslagerung importiert `app.py` die jeweilige Parserfunktion weiter und re-exportiert sie optional, damit bestehende Tests mit `import app` unverändert funktionieren.

## 4. Vorgeschlagene Zielstruktur

```text
support_viewer/
  __init__.py
  models.py
  utils.py
  orchestrator.py
  parsers/
    __init__.py
    common.py
    wlan.py
    dsl.py
    docsis.py
    fiber.py
    ppe.py
    internet.py
    telephony.py
    lan_mesh.py
    network_utilization.py
    access.py
  rendering/
    __init__.py
    common.py
    wlan.py
    dsl.py
    docsis.py
    fiber.py
    ppe.py
    internet.py
    telephony.py
    lan_mesh.py
    network_utilization.py
  app_shell.py
```

Empfohlene Verantwortungen:

- `support_viewer/models.py`: alle Dataclasses, zunächst unverändert. Pandas-Abhängigkeit durch `WifiRadioLoad.dataframe` temporär akzeptieren.
- `support_viewer/parsers/common.py`: reine Text- und Zahlenextraktion ohne Streamlit/Plotly.
- `support_viewer/parsers/access.py`: `detect_access_technology()` und Cross-Domain-Access-Erkennung.
- `support_viewer/orchestrator.py`: `parse_support_data()`.
- `support_viewer/rendering/common.py`: `escape_html()` und Formatierungsfunktionen, die wirklich UI-Ausgabe sind.
- `support_viewer/app_shell.py`: `build_dashboard()`, `main()`, Upload-Decoding und Streamlit-spezifischer Einstieg, falls am Ende gewünscht.
- `app.py`: bleibt zunächst Kompatibilitätsfassade und importiert öffentlich getestete Funktionen. Ganz am Ende kann es auf Streamlit-Einstieg plus Re-Exports reduziert werden.

## 5. Funktionen, die vorerst in `app.py` bleiben sollten

Diese Funktionen sollten bis spät im Prozess in `app.py` bleiben, weil sie stark verdrahten oder UI-Lifecycle betreffen:

- `build_dashboard()` wegen Tab-Struktur, Streamlit-Kontext und Zugriff auf fast alle Renderfunktionen.
- `main()` wegen Page-Config, CSS, Upload und Streamlit-Laufzeit.
- `_is_running_with_streamlit()` wegen direkter Streamlit-Runtime-Abhängigkeit.
- `parse_support_data()` bis mehrere Parsermodule stabil ausgelagert sind; danach nach `support_viewer/orchestrator.py` verschieben und in `app.py` re-exportieren.
- `detect_access_technology()` bis DSL, DOCSIS und Fiber klar getrennt sind; danach nach `parsers/access.py` verschieben.
- PPE/HWPA/VLAN-Korrelation und `analyze_connection_performance()` wegen hoher interner Kopplung und höherem Regressionsrisiko.

## 6. Risikoarme PR-Reihenfolge

### PR 1: Nur Paket-Skelett und Modelle auslagern

**Änderung:** `support_viewer/` anlegen, `models.py` mit Dataclasses erstellen, `app.py` importiert Dataclasses und re-exportiert Namen indirekt weiter.

**Risiko:** niedrig. Keine Parserlogik ändert sich. Hauptgefahr sind Importzyklen oder vergessene Typnamen.

**Tests:**
- `python -m pytest tests/test_supportdata_regressions.py tests/test_telephony_dect_events.py`
- `python -m pytest tests/test_internet_ar7_portforwarding.py`
- `python -m pytest`

### PR 2: Allgemeine Parserhelpers auslagern

**Änderung:** `support_viewer/parsers/common.py` mit `extract_section*`, `extract_numeric_array*`, `extract_value`, `parse_optional_float`, Metadatenparsern und Upload-unabhängigen Textparsern. `escape_html()` bleibt zunächst in `app.py` oder kommt in `rendering/common.py`, nicht in Parser-Common.

**Risiko:** niedrig bis mittel. Viele Domänen verwenden diese Helper; durch Re-Exports in `app.py` bleiben Tests stabil.

**Tests:**
- `python -m pytest tests/test_helpers.py tests/test_supportdata_regressions.py`
- `python -m pytest tests/test_dsl_parsers.py tests/test_fiber_parsers.py tests/test_internet_ar7_portforwarding.py`
- `python -m pytest`

### PR 3: Fiber-Parser auslagern

**Änderung:** `parse_fiber_overview()` nach `support_viewer/parsers/fiber.py`. Rendering bleibt in `app.py`.

**Risiko:** niedrig. Fiber hat eine kleine Parseroberfläche und spezifische Tests.

**Tests:**
- `python -m pytest tests/test_fiber_parsers.py`
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 4: DSL-Parser auslagern

**Änderung:** DSL-Spektrum, DSL-Metriken und DSL-spezifische Helper nach `support_viewer/parsers/dsl.py`. `detect_access_technology()` bleibt noch in `app.py` oder in `parsers/access.py` erst, wenn Fiber/DOCSIS verfügbar sind.

**Risiko:** mittel. DSL nutzt mehrere allgemeine Helper und hat Dictionary-Rückgaben ohne Modellschutz.

**Tests:**
- `python -m pytest tests/test_dsl_parsers.py`
- `python -m pytest tests/test_fiber_parsers.py`
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 5: DOCSIS/Cable-Parser auslagern

**Änderung:** DOCSIS-Parser, Cable-Spectrum und Frequenzbereichslogik nach `support_viewer/parsers/docsis.py`. Rendering/Qualitätsbewertung bleibt zunächst in `app.py`.

**Risiko:** mittel. Parser enthält Tabellenlogik und Plausibilitätsfilter; Tests in `test_helpers.py` decken einen Teil ab.

**Tests:**
- `python -m pytest tests/test_helpers.py`
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 6: Internet/AR7/Portforwarding-Parser auslagern

**Änderung:** InternetConnection, PortForwarding und AR7-Parserhelper nach `support_viewer/parsers/internet.py`; Modelle sind bereits zentral.

**Risiko:** mittel. AR7 parsing ist strukturreich und enthält viele private Helper.

**Tests:**
- `python -m pytest tests/test_internet_ar7_portforwarding.py`
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 7: Telephony/DECT/Events-Parser auslagern

**Änderung:** VoIP-, DECT- und Eventparser nach `support_viewer/parsers/telephony.py`.

**Risiko:** mittel. DECT-Parsing hat Mapping-Abhängigkeit und Formatierungshelper, ist aber gut testbar.

**Tests:**
- `python -m pytest tests/test_telephony_dect_events.py`
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 8: WLAN-Parser auslagern

**Änderung:** WLAN-Parser nach `support_viewer/parsers/wlan.py`. `WifiRadioLoad` bleibt vorerst mit `pd.DataFrame` unverändert.

**Risiko:** mittel. WLAN-Parser hängen an mehreren Supportdatenformaten; Pandas im Parser bleibt als bewusst akzeptierte Übergangskopplung.

**Tests:**
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 9: LAN, Mesh und Netzauslastung auslagern

**Änderung:** `parse_lan_ports()`, `parse_neighbour_clients()`, Mesh-Parser und AVM-Counter-Parser nach `support_viewer/parsers/lan_mesh.py` bzw. `network_utilization.py`.

**Risiko:** mittel. Mesh-Rendering nutzt Positionen; Parser und Layout-Helfer sollten zunächst gemeinsam bleiben oder sehr vorsichtig getrennt werden.

**Tests:**
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`

### PR 10: Access-Orchestrierung auslagern

**Änderung:** `detect_access_technology()` nach `support_viewer/parsers/access.py`; `parse_support_data()` optional nach `support_viewer/orchestrator.py` verschieben. `app.py` bleibt Re-Export-Fassade.

**Risiko:** mittel. Diese Änderung verbindet mehrere bereits ausgelagerte Parserdomänen.

**Tests:**
- `python -m pytest tests/test_fiber_parsers.py tests/test_dsl_parsers.py tests/test_helpers.py`
- `python -m pytest`

### PR 11: Rendering schrittweise auslagern

**Änderung:** Renderingfunktionen nach `support_viewer/rendering/*` verschieben, aber `build_dashboard()` bleibt zunächst in `app.py`. Reihenfolge: Fiber/DOCSIS/DSL, dann WLAN/LAN, dann Internet/AR7, dann Telephony/DECT/Events, zuletzt PPE.

**Risiko:** mittel bis hoch, weil Streamlit-Ausgaben schwerer automatisch zu prüfen sind. Möglichst nur reine Moves mit unveränderten Funktionskörpern durchführen.

**Tests:**
- `python -m pytest`
- optional manuell: `streamlit run app.py` mit synthetischen oder anonymisierten Mini-Daten, keine echten Supportdaten.

### PR 12: PPE/HWPA/VLAN und Ratelimiter zuletzt auslagern

**Änderung:** PPE-Parser/Diagnose nach `support_viewer/parsers/ppe.py`, PPE-Rendering nach `support_viewer/rendering/ppe.py`, Ratelimiter-Rendering getrennt halten.

**Risiko:** hoch relativ zu den anderen Schritten. Die Domäne ist groß, hat viele private Helper, Korrelationstabellen und Diagnoseausgaben.

**Tests:**
- `python -m pytest tests/test_supportdata_regressions.py`
- `python -m pytest`
- zusätzlich gezielte neue Tests in einer späteren separaten PR, aber nicht während reiner Move-PRs.

## 7. Welche Funktionen zuerst ausgelagert werden sollten

1. Dataclasses nach `support_viewer/models.py`.
2. Kleine, reine Texthelper nach `support_viewer/parsers/common.py`.
3. `parse_fiber_overview()` als kleinste isolierte Domäne.
4. DSL-Parser, weil Tests klar existieren und keine UI-Abhängigkeit nötig ist.
5. DOCSIS-Parser, danach Internet/AR7 und Telephony/DECT.

## 8. Welche Funktionen zuletzt ausgelagert werden sollten

1. `build_dashboard()` und `main()`.
2. `parse_support_data()` und `detect_access_technology()` erst nach stabilen Parsermodulen.
3. PPE/HWPA/VLAN-Service-Korrelation und `analyze_connection_performance()`.
4. Renderingfunktionen, die viele `st.*`, Plotly-Figuren, HTML und DataFrames mischen.
5. `WifiRadioLoad.dataframe`-Entkopplung von Pandas erst nach reinen Move-PRs, weil dies eine echte Verhaltens-/Strukturänderung wäre.

## 9. Import- und Abhängigkeitsziel

Parsermodule sollten langfristig keine Streamlit-, Plotly- oder `components`-Imports haben. Zielabhängigkeiten:

- `parsers/*`: `re`, `json`, `ipaddress`, `dataclasses`/Modelle, `typing`; temporär `pandas` nur für WLAN-Radio-Load.
- `rendering/*`: `html`, `textwrap`, `pandas`, `plotly`, `streamlit`, `streamlit.components` nach Bedarf.
- `models.py`: `dataclasses`, `typing`; temporär `pandas` wegen `WifiRadioLoad`.
- `utils.py` oder `parsers/common.py`: reine, frameworkfreie Helpers.
- `app.py`: Streamlit-App-Einstieg, Upload/Decoding, Dashboard-Orchestrierung und Kompatibilitäts-Re-Exports.

## 10. Arbeitsregeln für jede Move-PR

- Keine Verhaltensänderung und keine Umbenennung öffentlicher Funktionen.
- `app.py` exportiert alte Namen weiter, solange Tests `import app` verwenden.
- Erst kopieren/importieren, Tests grün bekommen, danach alten Code entfernen.
- Private Helper einer Domäne zusammen mit ihrem Parser verschieben, nicht einzeln über mehrere Module verteilen.
- Keine echten Supportdaten nutzen; Tests nur mit bestehenden synthetischen Fixtures oder neuem synthetischem Minimaltext.
- Nach jeder PR vollständigen Testlauf ausführen.
