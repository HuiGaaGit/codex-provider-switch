"""生成适合 Windows 16–256 像素层的简化供应商开关图标。"""

from pathlib import Path

from PIL import Image, ImageDraw


ASSET_DIR = Path(__file__).resolve().parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def point(value: float, size: int) -> int:
    return round(value * size / 256)


def rounded(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], radius: float, fill, outline=None, width=1, size=256) -> None:
    coords = tuple(point(value, size) for value in box)
    draw.rounded_rectangle(coords, radius=point(radius, size), fill=fill, outline=outline, width=max(1, point(width, size)))


def make_icon(size: int) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 外框只保留大面积深色和开关符号，避免 16px 时出现模糊的箭头、阴影和纹理。
    rounded(draw, (12, 12, 244, 244), 52, "#102a57", "#4d79b6", width=5, size=canvas_size)
    rounded(draw, (33, 85, 223, 171), 43, "#081b3d", "#8eb5ed", width=5, size=canvas_size)
    rounded(draw, (38, 90, 128, 166), 38, "#1767f4", size=canvas_size)
    rounded(draw, (128, 90, 218, 166), 38, "#12cfca", size=canvas_size)
    # 中线和两个纯色圆点在最小层仍能清晰表达“切换”。
    draw.rectangle((point(125, canvas_size), point(96, canvas_size), point(131, canvas_size), point(160, canvas_size)), fill="#0b2754")
    draw.ellipse((point(48, canvas_size), point(100, canvas_size), point(114, canvas_size), point(166, canvas_size)), fill="#f3fbff", outline="#0e4eca", width=max(1, point(4, canvas_size)))
    draw.ellipse((point(142, canvas_size), point(100, canvas_size), point(208, canvas_size), point(166, canvas_size)), fill="#f3fbff", outline="#0c9e9b", width=max(1, point(4, canvas_size)))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    frames = [make_icon(size) for size in SIZES]
    frames[-1].save(ASSET_DIR / "codex_api_provider_switch_icon.png")
    frames[-1].save(ASSET_DIR / "codex_api_provider_switch_icon.ico", sizes=[(size, size) for size in SIZES])


if __name__ == "__main__":
    main()
