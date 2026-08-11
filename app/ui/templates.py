"""Shared ``Jinja2Templates`` instance.

One instance, imported by :mod:`app.ui.router` (and by any API route that
ever needs to render an HTML fragment, e.g. an HTMX partial down the line).
Keeping it here instead of instantiating inline in the router avoids
accidentally creating two template environments that drift out of sync.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _format_duration(seconds: float | int | None) -> str:
    """``172.0`` -> ``"2:52"``; ``3725`` -> ``"1:02:05"``; falsy -> ``"–"``.
    Used by the album detail page's tracklist and total-runtime line."""
    if not seconds or seconds < 0:
        return "–"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


templates.env.filters["duration"] = _format_duration
