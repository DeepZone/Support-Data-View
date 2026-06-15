# Security

Support data files can contain sensitive operational details such as MAC addresses, public IP addresses, provider configuration, SIP state, port forwards, Wi-Fi metadata and event logs.

## Handling support data

- Do not commit real support data files to the repository.
- Use synthetic or heavily anonymized samples for tests and bug reports.
- Remove customer identifiers, serial numbers, public IP addresses, SIP numbers and credentials before sharing data.
- Keep local `.streamlit/secrets.toml` files out of version control.

## Reporting issues

If you find a security-relevant parsing or display issue, please report it privately to the repository owner instead of opening a public issue with real support data attached.

## Runtime scope

The app is intended to parse uploaded local text files and render diagnostics. It should not need write access to uploaded support data, external network access for parsing, or persistent storage of uploaded files.
