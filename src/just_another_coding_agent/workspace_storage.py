from __future__ import annotations

import hashlib
import re
from pathlib import Path

from just_another_coding_agent.tools._workspace import normalize_workspace_root


def workspace_key(workspace_root: Path | str) -> str:
    normalized_workspace_root = str(normalize_workspace_root(workspace_root))
    slug = re.sub(r"[^a-z0-9]+", "-", Path(normalized_workspace_root).name.lower())
    normalized_slug = slug.strip("-") or "workspace"
    digest = hashlib.sha256(normalized_workspace_root.encode("utf-8")).hexdigest()[:16]
    return f"{normalized_slug}-{digest}"


__all__ = ["workspace_key"]
