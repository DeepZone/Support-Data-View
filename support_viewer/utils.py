import html


def escape_html(value: object) -> str:
    """Escape support-data derived values before rendering custom HTML."""
    return html.escape(str(value), quote=True)
