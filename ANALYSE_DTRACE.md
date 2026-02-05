# Auswertung dtrace-Mitschnitt (Q.931/DECT)

## A) Executive Summary
- **Anzahl Call-Legs:** **0** (im bereitgestellten Mitschnitt sind **keine Q.931-Frames** enthalten, daher keine Leg-Trennung nach Controller/CallRef/Richtung möglich).
- Der Mitschnitt enthält stattdessen allgemeine System-/WAN-/DSL-/WLAN-/Voice-Logeinträge (z. B. Boot, PPPoE, DSL-LOS, SIP-Registrierung).
- **Startzeit des Mitschnitts:** 2026-01-14 08:15:01.120.
- **Endzeit des Mitschnitts:** 2026-01-14T08:17:20.
- **Dauer des Mitschnittfensters:** ca. 2 min 18.880 s.
- **~25s-Muster pro Call-Leg:** **nicht prüfbar**, da keine Call-Legs vorhanden.
- **Lokales Routing-Problem „No route to destination“:** **nicht nachweisbar**, da keine Q.931 Cause-Elemente vorhanden.
- **Finaler Cause pro Leg:** nicht verfügbar (keine DISCONNECT/RELEASE-Ereignisse im Q.931-Sinn vorhanden).

## B) Detail pro Call-Leg
Da keine Q.931-Signalisierung mit „Protocol discriminator Q.931: 08“, Call Reference, Controller und Richtung im Mitschnitt vorhanden ist, können keine Call-Legs rekonstruiert werden.

### Beobachtete (nicht-Q.931) Timeline im Mitschnitt
- 2026-01-14 08:15:01.120 – `[INFO] [boot] DTrace started`
- 2026-01-14 08:15:03.009 – `[DEBUG] [wan] Interface pppoe0 initialized`
- 2026-01-14 08:15:10.442 – `[WARN] [dsl] SNR margin dropped below threshold: 5.2 dB`
- 2026-01-14 08:15:11.872 – `[ERROR] [dsl] Retrain triggered due to LOS event`
- 2026-01-14 08:16:30.012 – `[INFO] [dsl] Link back in showtime, sync restored`
- 14.01.2026 08:16:45 – `WARN wlan: DFS event detected, channel switch pending`
- 2026-01-14T08:17:20 – `[CRIT] [voice] SIP registration failed repeatedly`
- `unstructured line without parser format`

### Finaler DISCONNECT/RELEASE Cause mit exakter Logzeile
- **Nicht belegbar im Mitschnitt**, da keine Q.931 DISCONNECT/RELEASE-Zeilen enthalten sind.

## C) Findings & Nächste Checks (konkret, ohne Spekulation)
1. **Richtigen dtrace-Abschnitt exportieren** (oder nachliefern), der Q.931 tatsächlich enthält:
   - „Protocol discriminator Q.931: 08“
   - SETUP / SETUP ACKNOWLEDGE / CALL PROCEEDING / ALERTING / INFORMATION / DISCONNECT / RELEASE / RELEASE COMPLETE
   - Call reference, Richtung und Controller pro Nachricht.
2. **Falls DECT-Korrelation gewünscht ist**, zusätzlich die DECT-Events mit Zeitstempel bereitstellen (z. B. MAC_PT, DLC, NWL, LCE_REQUEST_PAGE, CC_SETUP, CC_ALERTING).
3. **Zeitformat vereinheitlichen** (aktuell gemischt: `YYYY-MM-DD hh:mm:ss.mmm`, `DD.MM.YYYY hh:mm:ss`, `YYYY-MM-DDThh:mm:ss`) und unstrukturierte Zeilen vermeiden.
4. Mit einem vollständigen Q.931-Trace kann dann exakt geliefert werden:
   - Leg-Trennung nach (Controller + CallRef + Richtung),
   - Dauer bis DISCONNECT/RELEASE COMPLETE,
   - Cause-basierte Abbruchanalyse,
   - Prüfung des ~25s-Musters je Leg.
