#!/usr/bin/env python3
"""Generate WebP/AVIF variants for PNG images under static/img.
Skips favicon.png. Only regenerates when source is newer or output missing.
"""
from pathlib import Path
from PIL import Image
import pillow_avif_plugin  # noqa: F401

root = Path(__file__).resolve().parent.parent / "static" / "img"
if not root.exists():
    raise SystemExit(f"Image root not found: {root}")

pngs = list(root.rglob("*.png"))
for p in pngs:
    if p.name.lower() == "favicon.png":
        continue
    out_webp = p.with_suffix(".webp")
    out_avif = p.with_suffix(".avif")

    try:
        src_mtime = p.stat().st_mtime
    except FileNotFoundError:
        continue

    img = Image.open(p)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    if (not out_webp.exists()) or (out_webp.stat().st_mtime < src_mtime):
        img.save(out_webp, format="WEBP", quality=85, method=6)

    if (not out_avif.exists()) or (out_avif.stat().st_mtime < src_mtime):
        img.save(out_avif, format="AVIF", quality=50)

print("Image optimization complete.")
