"""Shared image-tag syntax and session-relative path validation."""

from pathlib import Path, PureWindowsPath
import re


IMAGE_TAG_RE = re.compile(r'<image\s+path="([^"]+)"\s*/>')


def resolve_image_path(working_dir: Path, relative_path: str) -> Path:
    """Resolve an existing image file inside the session working directory."""
    path = Path(relative_path)
    if path.is_absolute() or PureWindowsPath(relative_path).drive or "\\" in relative_path:
        raise ValueError("Image paths must be relative and use forward slashes.")
    root = working_dir.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Image paths must stay inside the session working directory.")
    if not resolved.is_file():
        raise FileNotFoundError("Image file does not exist.")
    return resolved
