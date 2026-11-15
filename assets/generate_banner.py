#!/usr/bin/env python3
"""Generate the LissSave welcome banner (assets/welcome_banner.png).

Brand palette:
- Dark Void (main background):  #151419
- Gluon Grey (cards/surfaces):  #1B1B1E
- Slate Grey (borders/strokes): #262626
- Liquid Lava (primary accent): #F56E0F
- Snow (primary text):          #FBFBFB
- Dusty Grey (secondary text):  #878787
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

# Brand colors (HEX & RGB)
HEX_DARK_VOID = "#151419"
HEX_GLUON_GREY = "#1B1B1E"
HEX_SLATE_GREY = "#262626"
HEX_LIQUID_LAVA = "#F56E0F"
HEX_SNOW = "#FBFBFB"
HEX_DUSTY_GREY = "#878787"

RGB_DARK_VOID = (21, 20, 25)
RGB_GLUON_GREY = (27, 27, 30)
RGB_SLATE_GREY = (38, 38, 38)
RGB_LIQUID_LAVA = (245, 110, 15)
RGB_SNOW = (251, 251, 251)
RGB_DUSTY_GREY = (135, 135, 135)

ASSETS_DIR = Path(__file__).resolve().parent
LOGO_PATH = ASSETS_DIR / "logo.png"
OUTPUT_PATH = ASSETS_DIR / "welcome_banner.png"

BANNER_WIDTH = 1280
BANNER_HEIGHT = 640


def _load_pillow_font(size: int, bold: bool = False):
    """Load a clean sans-serif TrueType font via Pillow with safe fallbacks."""
    from PIL import ImageFont

    candidates = (
        [
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def generate_with_pillow(logo_path: Path, output_path: Path) -> None:
    """Generate welcome_banner.png using Pillow (PIL)."""
    from PIL import Image, ImageDraw, ImageFilter

    canvas = Image.new("RGBA", (BANNER_WIDTH, BANNER_HEIGHT), (*RGB_DARK_VOID, 255))

    # Subtle radial orange ambient glow behind the card
    glow = Image.new("RGBA", (BANNER_WIDTH, BANNER_HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((70, 90, 530, 550), fill=(*RGB_LIQUID_LAVA, 42))
    glow_draw.ellipse((820, -60, 1300, 420), fill=(*RGB_LIQUID_LAVA, 24))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=85))
    canvas = Image.alpha_composite(canvas, glow)

    draw = ImageDraw.Draw(canvas)

    # Outer card in Gluon Grey (#1B1B1E) with Slate Grey (#262626) stroke
    card_box = (56, 56, BANNER_WIDTH - 56, BANNER_HEIGHT - 56)
    draw.rounded_rectangle(
        card_box,
        radius=32,
        fill=RGB_GLUON_GREY,
        outline=RGB_SLATE_GREY,
        width=2,
    )

    # Accent bar on the left edge of the card in Liquid Lava (#F56E0F)
    draw.rounded_rectangle(
        (56, 136, 64, BANNER_HEIGHT - 136),
        radius=4,
        fill=RGB_LIQUID_LAVA,
    )

    # Logo container frame on the left
    logo_frame_box = (116, 140, 476, 500)
    draw.rounded_rectangle(
        logo_frame_box,
        radius=28,
        fill=RGB_DARK_VOID,
        outline=RGB_SLATE_GREY,
        width=2,
    )

    # Composite mascot logo seamlessly onto Dark Void (#151419)
    if logo_path.exists():
        logo_img = Image.open(logo_path).convert("RGBA")
        bg_sample = logo_img.getpixel((0, 0))
        pixels = logo_img.load()
        if pixels is not None:
            for py in range(logo_img.height):
                for px in range(logo_img.width):
                    r, g, b, a = pixels[px, py]
                    if (
                        abs(r - bg_sample[0]) <= 12
                        and abs(g - bg_sample[1]) <= 12
                        and abs(b - bg_sample[2]) <= 12
                    ):
                        pixels[px, py] = (*RGB_DARK_VOID, a)
        target_w = 270
        aspect = logo_img.height / max(1, logo_img.width)
        target_h = int(target_w * aspect)
        resample = getattr(Image, "Resampling", Image).LANCZOS
        logo_img = logo_img.resize((target_w, target_h), resample=resample)
        lx = 116 + (360 - target_w) // 2
        ly = 140 + (360 - target_h) // 2
        canvas.alpha_composite(logo_img, dest=(lx, ly))

    # Top pill tag: FAST MEDIA DOWNLOADER
    pill_box = (530, 142, 855, 184)
    draw.rounded_rectangle(
        pill_box,
        radius=21,
        fill=RGB_DARK_VOID,
        outline=RGB_LIQUID_LAVA,
        width=2,
    )
    font_tag = _load_pillow_font(17, bold=True)
    draw.text((552, 153), "FAST MEDIA DOWNLOADER", fill=RGB_LIQUID_LAVA, font=font_tag)

    # Main Title: LissSave
    font_title = _load_pillow_font(84, bold=True)
    draw.text((530, 202), "Liss", fill=RGB_SNOW, font=font_title)
    liss_bbox = draw.textbbox((530, 202), "Liss", font=font_title)
    draw.text((liss_bbox[2] + 2, 202), "Save", fill=RGB_LIQUID_LAVA, font=font_title)

    # Subtitle
    font_sub = _load_pillow_font(26, bold=False)
    draw.text(
        (530, 314),
        "Video & Audio from Social Platforms in Original Quality",
        fill=RGB_DUSTY_GREY,
        font=font_sub,
    )

    # Divider line in Slate Grey (#262626)
    draw.line((530, 374, 1156, 374), fill=RGB_SLATE_GREY, width=2)

    # Feature / Platform badges
    badges = [
        ("TIKTOK NO WATERMARK", True),
        ("INSTAGRAM REELS", False),
        ("YOUTUBE 1080P+", False),
        ("UP TO 2 GB", True),
        ("MP4 / MP3", False),
    ]
    font_badge = _load_pillow_font(18, bold=True)
    bx, by = 530, 404
    for text, is_accent in badges:
        tb = draw.textbbox((0, 0), text, font=font_badge)
        tw = tb[2] - tb[0]
        bw = tw + 36
        bh = 44
        if bx + bw > 1170:
            bx = 530
            by += 58
        draw.rounded_rectangle(
            (bx, by, bx + bw, by + bh),
            radius=12,
            fill=RGB_DARK_VOID,
            outline=RGB_LIQUID_LAVA if is_accent else RGB_SLATE_GREY,
            width=2,
        )
        draw.text(
            (bx + 18, by + 11),
            text,
            fill=RGB_LIQUID_LAVA if is_accent else RGB_SNOW,
            font=font_badge,
        )
        bx += bw + 14

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)


# ---------------------------------------------------------------------------
# Pure-Python fallback (used when Pillow is not yet installed on host system)
# ---------------------------------------------------------------------------
_FONT_5X7: dict[str, list[str]] = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "&": ["01100", "10010", "10100", "01000", "10101", "10010", "01101"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def _decode_png_rgba(path: Path) -> tuple[int, int, bytearray]:
    """Minimal pure-Python 8-bit RGB/RGBA PNG decoder."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG header")
    pos = 8
    width = height = color_type = 0
    idat_chunks: list[bytes] = []
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos : pos + 4])[0]
        ctype = raw[pos + 4 : pos + 8]
        chunk_data = raw[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk_data)
            if bit_depth != 8 or color_type not in (2, 6):
                raise ValueError(f"Unsupported PNG format: depth={bit_depth}, color={color_type}")
        elif ctype == b"IDAT":
            idat_chunks.append(chunk_data)
        elif ctype == b"IEND":
            break

    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    decompressed = zlib.decompress(b"".join(idat_chunks))
    out = bytearray(width * height * 4)
    prev_row = bytearray(stride)
    cur_row = bytearray(stride)

    src_idx = 0
    for y in range(height):
        ftype = decompressed[src_idx]
        src_idx += 1
        row_bytes = decompressed[src_idx : src_idx + stride]
        src_idx += stride
        for i in range(stride):
            x_byte = row_bytes[i]
            a = cur_row[i - bpp] if i >= bpp else 0
            b = prev_row[i]
            c = prev_row[i - bpp] if i >= bpp else 0
            if ftype == 0:
                val = x_byte
            elif ftype == 1:
                val = (x_byte + a) & 0xFF
            elif ftype == 2:
                val = (x_byte + b) & 0xFF
            elif ftype == 3:
                val = (x_byte + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                val = (x_byte + pr) & 0xFF
            else:
                val = x_byte
            cur_row[i] = val
        prev_row[:] = cur_row
        dst_base = y * width * 4
        if bpp == 4:
            out[dst_base : dst_base + width * 4] = cur_row
        else:
            for x in range(width):
                out[dst_base + x * 4 : dst_base + x * 4 + 4] = bytes(
                    (cur_row[x * 3], cur_row[x * 3 + 1], cur_row[x * 3 + 2], 255)
                )
    return width, height, out


def _encode_png_rgb(width: int, height: int, rgb_buf: bytearray) -> bytes:
    """Encode an RGB bytearray into a valid PNG byte stream."""

    def make_chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_rows = bytearray()
    stride = width * 3
    for y in range(height):
        raw_rows.append(0)
        raw_rows.extend(rgb_buf[y * stride : (y + 1) * stride])
    compressed = zlib.compress(bytes(raw_rows), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + make_chunk(b"IHDR", ihdr)
        + make_chunk(b"IDAT", compressed)
        + make_chunk(b"IEND", b"")
    )


def generate_fallback(logo_path: Path, output_path: Path) -> None:
    """Pure-Python banner generator when Pillow is unavailable on the host."""
    w, h = BANNER_WIDTH, BANNER_HEIGHT
    buf = bytearray(w * h * 3)

    # Fill background (#151419) with subtle Liquid Lava radial glow
    for y in range(h):
        row_off = y * w * 3
        dy1 = y - 320
        for x in range(w):
            dx1 = x - 300
            dist_sq = dx1 * dx1 + dy1 * dy1
            glow = max(0.0, 1.0 - dist_sq / (340.0 * 340.0)) * 0.14
            r = int(RGB_DARK_VOID[0] * (1.0 - glow) + RGB_LIQUID_LAVA[0] * glow)
            g = int(RGB_DARK_VOID[1] * (1.0 - glow) + RGB_LIQUID_LAVA[1] * glow)
            b = int(RGB_DARK_VOID[2] * (1.0 - glow) + RGB_LIQUID_LAVA[2] * glow)
            idx = row_off + x * 3
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b

    def draw_rounded_rect(
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        radius: int,
        fill_rgb: tuple[int, int, int],
        border_rgb: tuple[int, int, int] | None = None,
        border_w: int = 2,
    ) -> None:
        for y in range(max(0, y0), min(h, y1)):
            for x in range(max(0, x0), min(w, x1)):
                cx = min(max(x, x0 + radius), x1 - radius - 1)
                cy = min(max(y, y0 + radius), y1 - radius - 1)
                dist = math.hypot(x - cx, y - cy)
                if dist <= radius + 0.5:
                    idx = (y * w + x) * 3
                    is_border = border_rgb is not None and (
                        dist >= radius - border_w
                        or x < x0 + border_w
                        or x >= x1 - border_w
                        or y < y0 + border_w
                        or y >= y1 - border_w
                    )
                    c = border_rgb if is_border and border_rgb else fill_rgb
                    buf[idx], buf[idx + 1], buf[idx + 2] = c

    # Main Gluon Grey card (#1B1B1E) with Slate Grey (#262626) outline
    draw_rounded_rect(56, 56, w - 56, h - 56, 32, RGB_GLUON_GREY, RGB_SLATE_GREY, 2)
    # Liquid Lava accent bar (#F56E0F)
    draw_rounded_rect(56, 136, 64, h - 136, 4, RGB_LIQUID_LAVA)
    # Logo container box
    draw_rounded_rect(116, 140, 476, 500, 28, RGB_DARK_VOID, RGB_SLATE_GREY, 2)

    # Bilinear composite of assets/logo.png with seamless background color match
    if logo_path.exists():
        lw, lh, lrgba = _decode_png_rgba(logo_path)
        bg_r, bg_g, bg_b = lrgba[0], lrgba[1], lrgba[2]
        for i in range(0, len(lrgba), 4):
            if (
                abs(lrgba[i] - bg_r) <= 12
                and abs(lrgba[i + 1] - bg_g) <= 12
                and abs(lrgba[i + 2] - bg_b) <= 12
            ):
                lrgba[i], lrgba[i + 1], lrgba[i + 2] = RGB_DARK_VOID
        tw = 270
        th = int(tw * (lh / max(1, lw)))
        ox = 116 + (360 - tw) // 2
        oy = 140 + (360 - th) // 2
        for ty in range(th):
            sy = (ty / max(1, th - 1)) * (lh - 1)
            y0_i = int(sy)
            y1_i = min(lh - 1, y0_i + 1)
            wy = sy - y0_i
            for tx in range(tw):
                sx = (tx / max(1, tw - 1)) * (lw - 1)
                x0_i = int(sx)
                x1_i = min(lw - 1, x0_i + 1)
                wx = sx - x0_i
                p00 = (y0_i * lw + x0_i) * 4
                p01 = (y0_i * lw + x1_i) * 4
                p10 = (y1_i * lw + x0_i) * 4
                p11 = (y1_i * lw + x1_i) * 4
                r = int(
                    lrgba[p00] * (1 - wx) * (1 - wy)
                    + lrgba[p01] * wx * (1 - wy)
                    + lrgba[p10] * (1 - wx) * wy
                    + lrgba[p11] * wx * wy
                )
                g = int(
                    lrgba[p00 + 1] * (1 - wx) * (1 - wy)
                    + lrgba[p01 + 1] * wx * (1 - wy)
                    + lrgba[p10 + 1] * (1 - wx) * wy
                    + lrgba[p11 + 1] * wx * wy
                )
                b = int(
                    lrgba[p00 + 2] * (1 - wx) * (1 - wy)
                    + lrgba[p01 + 2] * wx * (1 - wy)
                    + lrgba[p10 + 2] * (1 - wx) * wy
                    + lrgba[p11 + 2] * wx * wy
                )
                a = (
                    lrgba[p00 + 3] * (1 - wx) * (1 - wy)
                    + lrgba[p01 + 3] * wx * (1 - wy)
                    + lrgba[p10 + 3] * (1 - wx) * wy
                    + lrgba[p11 + 3] * wx * wy
                ) / 255.0
                dx, dy = ox + tx, oy + ty
                if 0 <= dx < w and 0 <= dy < h and a > 0.01:
                    idx = (dy * w + dx) * 3
                    buf[idx] = int(buf[idx] * (1 - a) + r * a)
                    buf[idx + 1] = int(buf[idx + 1] * (1 - a) + g * a)
                    buf[idx + 2] = int(buf[idx + 2] * (1 - a) + b * a)

    def draw_text(text: str, x: int, y: int, scale: int, color: tuple[int, int, int]) -> int:
        cur_x = x
        for ch in text.upper():
            glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        for sy in range(scale):
                            for sx in range(scale):
                                px = cur_x + gx * scale + sx
                                py = y + gy * scale + sy
                                if 0 <= px < w and 0 <= py < h:
                                    idx = (py * w + px) * 3
                                    buf[idx], buf[idx + 1], buf[idx + 2] = color
            cur_x += 6 * scale
        return cur_x

    # Top pill tag
    draw_rounded_rect(530, 142, 875, 184, 20, RGB_DARK_VOID, RGB_LIQUID_LAVA, 2)
    draw_text("FAST MEDIA DOWNLOADER", 550, 156, 2, RGB_LIQUID_LAVA)

    # Main Title: LISSSAVE
    end_x = draw_text("LISS", 530, 212, 11, RGB_SNOW)
    draw_text("SAVE", end_x + 4, 212, 11, RGB_LIQUID_LAVA)

    # Subtitle
    draw_text("VIDEO & AUDIO IN ORIGINAL QUALITY", 530, 322, 3, RGB_DUSTY_GREY)

    # Divider line
    draw_rounded_rect(530, 372, 1156, 374, 1, RGB_SLATE_GREY)

    # Feature badges
    badges = [
        ("TIKTOK HD", True),
        ("INSTAGRAM", False),
        ("YOUTUBE 1080P+", False),
        ("UP TO 2 GB", True),
        ("MP4 / MP3", False),
    ]
    bx, by = 530, 404
    for label, is_accent in badges:
        bw = len(label) * 12 + 36
        bh = 44
        if bx + bw > 1170:
            bx = 530
            by += 58
        draw_rounded_rect(
            bx,
            by,
            bx + bw,
            by + bh,
            12,
            RGB_DARK_VOID,
            RGB_LIQUID_LAVA if is_accent else RGB_SLATE_GREY,
            2,
        )
        draw_text(label, bx + 18, by + 15, 2, RGB_LIQUID_LAVA if is_accent else RGB_SNOW)
        bx += bw + 14

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_encode_png_rgb(w, h, buf))


def main() -> None:
    """Entry point to generate welcome_banner.png."""
    try:
        generate_with_pillow(LOGO_PATH, OUTPUT_PATH)
        print(f"[Pillow] Generated welcome banner at {OUTPUT_PATH}")
    except ImportError:
        generate_fallback(LOGO_PATH, OUTPUT_PATH)
        print(f"[Fallback] Generated welcome banner at {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
