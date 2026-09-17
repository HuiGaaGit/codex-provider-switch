"""Generate the Windows icon from Tabler's MIT-licensed switch-horizontal glyph."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ASSET_DIR = Path(__file__).resolve().parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
DESIGN_SIZE = 256


def point(value: float, size: int) -> int:
    return round(value * size / DESIGN_SIZE)


def _mix(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int, int]:
    return tuple(round(left + (right - left) * ratio) for left, right in zip(start, end)) + (255,)


def _rounded_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (point(8, size), point(8, size), point(248, size), point(248, size)),
        radius=point(57, size),
        fill=255,
    )
    return mask


def _round_polyline(
    draw: ImageDraw.ImageDraw,
    coordinates: list[tuple[float, float]],
    *,
    size: int,
    fill: str | tuple[int, int, int, int],
    width: float,
) -> None:
    points = [(point(x, size), point(y, size)) for x, y in coordinates]
    stroke = max(1, point(width, size))
    radius = stroke // 2
    draw.line(points, fill=fill, width=stroke, joint="curve")
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _draw_switch(layer: Image.Image, size: int, *, glow: bool = False) -> None:
    draw = ImageDraw.Draw(layer)
    width = 20 if not glow else 29
    alpha = 255 if not glow else 115
    top = (91, 200, 255, alpha)
    bottom = (91, 240, 190, alpha)

    # Exact Tabler path geometry mapped from its original 24px grid.
    transform = lambda x, y: (20 + x * 9, 20 + y * 9)
    _round_polyline(draw, [transform(16, 3), transform(20, 7), transform(16, 11)], size=size, fill=top, width=width)
    _round_polyline(draw, [transform(10, 7), transform(20, 7)], size=size, fill=top, width=width)
    _round_polyline(draw, [transform(8, 13), transform(4, 17), transform(8, 21)], size=size, fill=bottom, width=width)
    _round_polyline(draw, [transform(4, 17), transform(13, 17)], size=size, fill=bottom, width=width)


def make_icon(size: int) -> Image.Image:
    render_size = size * 4
    transparent = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
    mask = _rounded_mask(render_size)

    gradient = Image.new("RGBA", (render_size, render_size))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(render_size):
        ratio = y / max(1, render_size - 1)
        gradient_draw.line((0, y, render_size, y), fill=_mix((22, 39, 52), (34, 57, 68), ratio))
    image = Image.composite(gradient, transparent, mask)

    ambient = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ambient_draw = ImageDraw.Draw(ambient)
    ambient_draw.ellipse(
        (point(34, render_size), point(-28, render_size), point(224, render_size), point(139, render_size)),
        fill=(91, 200, 255, 35),
    )
    ambient = ambient.filter(ImageFilter.GaussianBlur(max(1, point(25, render_size))))
    image = Image.alpha_composite(image, Image.composite(ambient, transparent, mask))

    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    _draw_switch(glow, render_size, glow=True)
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, point(13, render_size))))
    image = Image.alpha_composite(image, glow)
    glyph = Image.new("RGBA", image.size, (0, 0, 0, 0))
    _draw_switch(glyph, render_size)
    image = Image.alpha_composite(image, glyph)

    finish = ImageDraw.Draw(image)
    finish.rounded_rectangle(
        (point(8, render_size), point(8, render_size), point(248, render_size), point(248, render_size)),
        radius=point(57, render_size),
        outline=(199, 235, 244, 145),
        width=max(1, point(3, render_size)),
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    frames = [make_icon(size) for size in SIZES]
    frames[-1].save(ASSET_DIR / "codex_api_provider_switch_icon.png")
    frames[-1].save(
        ASSET_DIR / "codex_api_provider_switch_icon.ico",
        append_images=frames[:-1],
        sizes=[(size, size) for size in SIZES],
    )


if __name__ == "__main__":
    main()
