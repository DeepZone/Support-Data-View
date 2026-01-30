# Support-Data-View

Dieses Projekt stellt eine kleine Streamlit-Anwendung bereit, die Support-Data-TXT-Dateien
(z. B. FRITZ!Box Support Data) einliest und die wichtigsten DSL- und WLAN-Informationen
visualisiert.

## Features

- DSL-SNR-Spektrum (Downstream/Upstream)
- WLAN-Umgebung (Scan-Ergebnisse)
- WLAN-Clientliste inkl. Verbindungseinschätzung
- LAN/WAN-Portstatus
- Upload-Funktion für Support-Data-TXT

## Starten

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Dann im Browser die URL öffnen, die Streamlit ausgibt (meist `http://localhost:8501`).
