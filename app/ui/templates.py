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
