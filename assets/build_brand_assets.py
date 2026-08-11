"""Build production-ready FoxQuiz mascot and social assets.

The high-resolution, transparent mascot masters live in
``assets/brand_sources``. The social preview uses the canonical marketing image
at
``assets/brand_sources/marketing/foxquiz_mascots_performing_quiz.png``. Running this script creates
deterministic web-sized exports under ``app/static/assets`` without altering
the source artwork.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = PROJECT_DIR / "assets" / "brand_sources"
DEFAULT_SOCIAL_SOURCE = (
    PROJECT_DIR
    / "assets"
    / "brand_sources"
    / "marketing"
    / "foxquiz_mascots_performing_quiz.png"
)
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "app" / "static" / "assets"
MASCOTS = ("felix", "olivia", "dino")
VARIANTS = ("face", "full")
EXPORT_SIZES = (64, 128, 256, 512)
NAVY = (15, 23, 42, 255)
ORANGE = (255, 123, 0, 255)


def _trim_and_fit(image: Image.Image, size: int, padding: float = 0.04) -> Image.Image:
    """Fit visible artwork onto a transparent square canvas."""
    rgba = image.convert("RGBA")
    alpha_box = rgba.getchannel("A").getbbox()
    if alpha_box is None:
        raise ValueError("Source image has no visible pixels")

    artwork = rgba.crop(alpha_box)
    available = max(1, round(size * (1 - 2 * padding)))
    artwork.thumbnail((available, available), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    position = ((size - artwork.width) // 2, (size - artwork.height) // 2)
    canvas.alpha_composite(artwork, position)
    return canvas


def _save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _build_mascot_exports(source_dir: Path, output_dir: Path) -> Image.Image:
    mascot_output = output_dir / "mascots"
    felix_face_source: Image.Image | None = None

    for mascot in MASCOTS:
        for variant in VARIANTS:
            source_path = source_dir / "mascots" / f"{mascot}-{variant}.png"
            with Image.open(source_path) as source:
                source_rgba = source.convert("RGBA")
                if mascot == "felix" and variant == "face":
                    felix_face_source = source_rgba.copy()
                for size in EXPORT_SIZES:
                    export = _trim_and_fit(source_rgba, size)
                    _save_png(export, mascot_output / f"{mascot}-{variant}-{size}.png")

    if felix_face_source is None:
        raise RuntimeError("Felix face source was not processed")
    return felix_face_source


def _build_app_icon(felix_face: Image.Image, size: int) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    radius = max(2, round(size * 0.22))
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=NAVY)
    face = _trim_and_fit(felix_face, size, padding=0.07)
    icon.alpha_composite(face)
    return icon


def _build_icons(felix_face: Image.Image, output_dir: Path) -> None:
    favicon_16 = _build_app_icon(felix_face, 16)
    favicon_32 = _build_app_icon(felix_face, 32)
    favicon_48 = _build_app_icon(felix_face, 48)
    _save_png(favicon_16, output_dir / "favicon-16x16.png")
    _save_png(favicon_32, output_dir / "favicon-32x32.png")
    _save_png(_build_app_icon(felix_face, 180), output_dir / "apple-touch-icon.png")
    _save_png(_build_app_icon(felix_face, 192), output_dir / "icon-192.png")
    _save_png(_build_app_icon(felix_face, 512), output_dir / "icon-512.png")
    favicon_48.save(
        output_dir / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )


def _build_social_preview(source_path: Path, output_dir: Path) -> None:
    with Image.open(source_path) as source:
        preview = ImageOps.fit(
            source.convert("RGBA"),
            (1280, 640),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    card = (54, 64, 430, 190)
    draw.rounded_rectangle(
        card,
        radius=22,
        fill=(15, 23, 42, 226),
        outline=(255, 255, 255, 44),
        width=2,
    )
    draw.rounded_rectangle((80, 82, 158, 89), radius=3, fill=ORANGE)
    draw.text((80, 105), "FoxQuiz", font=_font(58), fill=ORANGE)

    final = Image.alpha_composite(preview, overlay)
    _save_png(final, output_dir / "foxquiz_social_preview.png")
    final.convert("RGB").save(
        output_dir / "foxquiz_social_preview.jpg",
        "JPEG",
        quality=90,
        optimize=True,
        progressive=True,
    )


def build(source_dir: Path, output_dir: Path, social_source: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    felix_face = _build_mascot_exports(source_dir, output_dir)
    _build_icons(felix_face, output_dir)
    _build_social_preview(social_source, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--social-source", type=Path, default=DEFAULT_SOCIAL_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    build(
        args.source_dir.resolve(),
        args.output_dir.resolve(),
        args.social_source.resolve(),
    )


if __name__ == "__main__":
    main()
