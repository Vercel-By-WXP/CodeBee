"""Small helpers for rendering prompts with untrusted interpolated values."""
from __future__ import annotations

import re


def render_values(template: str, values: dict) -> str:
    """Replace known template tokens once; leave token-like data untouched."""
    return re.sub(r"__[A-Z][A-Z0-9_]*__",
                  lambda match: str(values.get(match.group(0), match.group(0))),
                  template)
