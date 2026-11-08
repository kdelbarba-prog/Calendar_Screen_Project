"""Local background-image preparation for the display's top zone."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
BACKGROUND_SIZE = (1080, 620)
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def prepare_backgrounds(
    source_folder: Path,
    output_folder: Path,
    *,
    enabled: bool,
) -> list[str]:
    """Resize supported local images for the top display zone and return relative URLs."""
    if not enabled or not source_folder.is_dir():
        return []
    output_folder.mkdir(parents=True, exist_ok=True)
    background_urls: list[str] = []
    for source in sorted(source_folder.iterdir(), key=lambda path: path.name.casefold()):
        if source.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES or not source.is_file():
            continue
        try:
            with Image.open(source) as image:
                prepared = ImageOps.fit(
                    image.convert("RGB"),
                    BACKGROUND_SIZE,
                    method=Image.Resampling.LANCZOS,
                )
                digest = hashlib.sha256(source.name.encode("utf-8")).hexdigest()[:10]
                target_name = f"{source.stem[:48]}-{digest}.jpg"
                prepared.save(
                    output_folder / target_name,
                    "JPEG",
                    quality=88,
                    optimize=True,
                )
                background_urls.append(f"backgrounds/{target_name}")
        except (OSError, UnidentifiedImageError) as exc:
            logger.warning("Skipping unreadable background image '%s' (%s).", source.name, type(exc).__name__)
    return background_urls

