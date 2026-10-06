"""Minecraft MOTD 图片卡片渲染器。"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont

from .chart import smooth_points
from .minecraft_text import TextRun, motd_runs

CARD_WIDTH = 1200
CHINA_TIME = timezone(timedelta(hours=8))


@lru_cache(maxsize=64)
def _load_font(
    size: int, bold: bool = False, font_path: str = ""
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先使用服务器上的中文字体，兼容 Windows 本地预览。"""
    names = (
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ]
    )
    for name in ([font_path] if font_path else []) + names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _rounded_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int] | None = None,
    radius: int = 22,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)


def _draw_pixel_background(image: Image.Image, seed_text: str) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    top = (12, 23, 18)
    bottom = (22, 49, 34)
    width, height = image.size
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=(*color, 255))

    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    palette = [(17, 39, 26, 255), (20, 46, 29, 255), (15, 34, 24, 255)]
    for index in range(34):
        x = (digest[index % len(digest)] * 47 + index * 113) % CARD_WIDTH
        y = (digest[(index + 9) % len(digest)] * 31 + index * 71) % height
        size = 24 + digest[(index + 17) % len(digest)] % 64
        draw.rectangle((x, y, x + size, y + size), fill=palette[index % len(palette)])


def _decode_server_icon(icon_value: str) -> Image.Image | None:
    if not icon_value:
        return None
    if len(icon_value) > 1_000_000:
        return None
    try:
        encoded = icon_value.split(",", 1)[1] if "," in icon_value else icon_value
        source = Image.open(io.BytesIO(base64.b64decode(encoded)))
        if source.width > 512 or source.height > 512:
            return None
        icon = source.convert("RGBA")
        return icon.resize((128, 128), Image.Resampling.NEAREST)
    except Exception:
        return None


def _draw_creeper_icon(image: Image.Image, box: tuple[int, int, int, int], seed_text: str) -> None:
    """服务器没有图标时，绘制像素风苦力怕头像。"""
    x1, y1, x2, y2 = box
    size = x2 - x1
    cell = max(size // 8, 1)
    draw = ImageDraw.Draw(image, "RGBA")
    digest = hashlib.md5(seed_text.encode("utf-8")).digest()
    greens = [(72, 160, 72, 255), (83, 177, 79, 255), (55, 139, 65, 255), (101, 188, 88, 255)]
    for row in range(8):
        for col in range(8):
            color = greens[digest[(row * 8 + col) % len(digest)] % len(greens)]
            draw.rectangle(
                (x1 + col * cell, y1 + row * cell, x1 + (col + 1) * cell, y1 + (row + 1) * cell),
                fill=color,
            )
    dark = (15, 31, 22, 255)
    for col, row, width, height in (
        (1, 2, 2, 2),
        (5, 2, 2, 2),
        (3, 4, 2, 2),
        (2, 5, 4, 2),
        (2, 7, 1, 1),
        (5, 7, 1, 1),
    ):
        draw.rectangle(
            (
                x1 + col * cell,
                y1 + row * cell,
                x1 + (col + width) * cell,
                y1 + (row + height) * cell,
            ),
            fill=dark,
        )


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text)
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "..."
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return text + suffix


def _layout_motd(draw, runs, max_width, font_path):
    lines, current, width = [], [], 0.0
    for run in runs:
        font = _load_font(25, run.style.bold, font_path)
        for char in run.text.replace("\t", "    "):
            if char == "\n":
                lines.append(current)
                current, width = [], 0.0
                continue
            advance = draw.textlength(char, font=font)
            if current and width + advance > max_width - 8:
                lines.append(current)
                current, width = [], 0.0
            if current and current[-1].style == run.style:
                current[-1] = TextRun(current[-1].text + char, run.style)
            else:
                current.append(TextRun(char, run.style))
            width += advance
    if current or not lines:
        lines.append(current)
    return lines


def _draw_styled_line(image, position, runs, font_path):
    x, y = position
    draw = ImageDraw.Draw(image, "RGBA")
    for run in runs:
        font = _load_font(25, run.style.bold, font_path)
        width = draw.textlength(run.text, font=font)
        color = ImageColor.getrgb(run.style.color)
        stroke = 1 if sum(color) < 180 else 0
        options = {
            "font": font,
            "fill": color,
            "anchor": "lt",
            "stroke_width": stroke,
            "stroke_fill": (115, 145, 124),
        }
        if run.style.italic:
            glyph = Image.new("RGBA", (math.ceil(width) + 16, 36))
            ImageDraw.Draw(glyph).text((4, 2), run.text, **options)
            glyph = glyph.transform(
                glyph.size, Image.Transform.AFFINE, (1, 0.2, -7, 0, 1, 0), Image.Resampling.BICUBIC
            )
            image.alpha_composite(glyph, (int(x) - 4, int(y) - 2))
        else:
            draw.text((x, y), run.text, **options)
        if run.style.underlined:
            draw.line((x, y + 28, x + width, y + 28), fill=color, width=1)
        if run.style.strikethrough:
            draw.line((x, y + 13, x + width, y + 13), fill=color, width=1)
        x += width


def player_rows(names: list[str], font_path: str = "") -> list[dict]:
    """Wrap entire names into badges, retaining every character and every supplied player."""
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = _load_font(21, font_path=font_path)
    rows, chips, used, row_height = [], [], 0, 0
    for name in names:
        lines, line = [], ""
        for char in name:
            if line and measure.textlength(line + char, font=font) > 276:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
        width = math.ceil(max(measure.textlength(line, font=font) for line in lines)) + 32
        height = 20 + len(lines) * 28
        if chips and used + width > 944:
            rows.append({"chips": chips, "height": row_height})
            chips, used, row_height = [], 0, 0
        chips.append({"name": name, "lines": lines, "width": width, "height": height})
        used += width + 10
        row_height = max(row_height, height)
    if chips:
        rows.append({"chips": chips, "height": row_height})
    return rows


def player_pages(names: list[str], font_path: str = "") -> list[list[str]]:
    pages, page, height = [], [], 0
    for row in player_rows(names, font_path):
        if page and height + row["height"] + 10 > 500:
            pages.append(page)
            page, height = [], 0
        page.extend(chip["name"] for chip in row["chips"])
        height += row["height"] + 10
    if page or not pages:
        pages.append(page)
    return pages


def render_motd_cards(
    data: dict,
    bot_name="xiaohuicat",
    history=None,
    now=None,
    sample_interval=300,
    history_enabled=True,
    font_path="",
) -> list[bytes]:
    """Paginate large public rosters instead of making names unreadable or silently dropping them."""
    names = [str(name) for name in (data.get("player_list") or []) if str(name).strip()]
    pages = player_pages(names, font_path)
    result = []
    for index, page in enumerate(pages, 1):
        status = dict(
            data,
            player_list=page,
            _player_total=len(names),
            _player_page=index,
            _player_pages=len(pages),
        )
        result.append(
            render_motd_card(
                status, bot_name, history, now, sample_interval, history_enabled, font_path
            )
        )
    return result


def render_motd_card(
    data: dict,
    bot_name: str = "xiaohuicat",
    history: list[dict] | None = None,
    now: int | None = None,
    sample_interval: int = 300,
    history_enabled: bool = True,
    font_path: str = "",
) -> bytes:
    """把 MOTD 查询结果渲染为 PNG 字节。"""
    address = str(data.get("address") or "未知服务器")
    online = bool(data.get("online"))
    accent = (91, 214, 111, 255) if online else (241, 96, 89, 255)
    accent_soft = (27, 67, 37, 255) if online else (76, 31, 29, 255)

    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    fallback = "暂无服务器标语" if online else "无法连接到该服务器"
    runs = motd_runs(data.get("motd_raw") or data.get("motd") or fallback)
    motd_lines = _layout_motd(measure, runs, 940, font_path)
    motd_bottom = 452 + max(116, 56 + 34 * len(motd_lines))
    names = [str(name) for name in (data.get("player_list") or []) if str(name).strip()]
    rows = player_rows(names, font_path)
    players_top = motd_bottom + 20
    players_height = max(108, 64 + sum(row["height"] + 10 for row in rows))
    history_top = players_top + players_height + 20
    card_height = history_top + 490

    image = Image.new("RGBA", (CARD_WIDTH, card_height), (12, 23, 18, 255))
    _draw_pixel_background(image, address)
    draw = ImageDraw.Draw(image, "RGBA")

    # 主面板和左侧像素饰条
    _rounded_panel(draw, (44, 42, 1156, card_height - 40), (8, 16, 13, 222), (77, 123, 90, 110), 30)
    draw.rounded_rectangle((44, 42, 58, card_height - 40), radius=7, fill=accent)
    _rounded_panel(draw, (84, 100, 264, 280), (20, 38, 27, 245), (83, 147, 94, 155), 22)

    icon = _decode_server_icon(str(data.get("icon") or ""))
    if icon is not None:
        image.alpha_composite(icon, (110, 126))
    else:
        _draw_creeper_icon(image, (110, 126, 238, 254), address)

    title_font = _load_font(25, bold=True, font_path=font_path)
    address_font = _load_font(42, bold=True, font_path=font_path)
    status_font = _load_font(28, bold=True, font_path=font_path)
    label_font = _load_font(22, font_path=font_path)
    value_font = _load_font(29, bold=True, font_path=font_path)
    footer_font = _load_font(19, font_path=font_path)

    draw.text((84, 66), "MINECRAFT  SERVER  STATUS", font=title_font, fill=(161, 196, 171, 255))
    shown_address = _ellipsize(draw, address, address_font, 790)
    draw.text((298, 112), shown_address, font=address_font, fill=(239, 247, 241, 255))

    status_text = (
        "查询失败" if data.get("query_failed") else ("服务器在线" if online else "服务器离线")
    )
    status_width = int(draw.textlength(status_text, font=status_font)) + 58
    draw.rounded_rectangle(
        (298, 176, 298 + status_width, 226), radius=25, fill=accent_soft, outline=accent, width=2
    )
    draw.ellipse((316, 193, 330, 207), fill=accent)
    draw.text((342, 183), status_text, font=status_font, fill=accent)
    details = " · ".join(str(data.get(k) or "") for k in ("hostname", "software") if data.get(k))
    plugins = data.get("plugins")
    if isinstance(plugins, dict) and plugins.get("names"):
        details += f" · 插件 {len(plugins['names'])} 个"
    if details:
        draw.text(
            (298, 247),
            _ellipsize(draw, details.strip(" ·"), label_font, 780),
            font=label_font,
            fill=(145, 171, 152, 255),
        )

    # 信息卡片
    latency = data.get("latency_ms")
    latency_text = f"{latency} ms" if latency is not None else "未提供"
    panels = [
        (84, 314, 392, 426, "游戏版本", str(data.get("version") or "未知")),
        (
            414,
            314,
            722,
            426,
            "在线玩家",
            f"{data.get('players_online', 0)} / {data.get('players_max', 0)}"
            if online
            else "- / -",
        ),
        (744, 314, 1072, 426, "MC PING", latency_text),
    ]
    for x1, y1, x2, y2, label, value in panels:
        _rounded_panel(draw, (x1, y1, x2, y2), (19, 35, 27, 230), (65, 104, 76, 100), 18)
        draw.text((x1 + 22, y1 + 16), label, font=label_font, fill=(145, 171, 152, 255))
        draw.text(
            (x1 + 22, y1 + 52),
            _ellipsize(draw, value, value_font, x2 - x1 - 44),
            font=value_font,
            fill=(237, 244, 239, 255),
        )

    # 玩家数进度条
    players_online = int(data.get("players_online") or 0)
    players_max = int(data.get("players_max") or 0)
    ratio = min(players_online / players_max, 1.0) if players_max > 0 and online else 0
    draw.rounded_rectangle((436, 404, 700, 414), radius=5, fill=(40, 61, 47, 255))
    if ratio > 0:
        draw.rounded_rectangle((436, 404, 436 + int(264 * ratio), 414), radius=5, fill=accent)

    # Full-width, dynamically sized colored MOTD; retain its original line breaks.
    _rounded_panel(draw, (84, 452, 1072, motd_bottom), (17, 31, 24, 230), (65, 104, 76, 90), 20)
    draw.text((106, 468), "MOTD", font=label_font, fill=(145, 171, 152, 255))
    for index, line in enumerate(motd_lines):
        _draw_styled_line(image, (106, 501 + index * 34), line, font_path)

    # Full roster gets its own section and wraps at badge boundaries.
    _rounded_panel(
        draw,
        (84, players_top, 1072, players_top + players_height),
        (17, 31, 24, 230),
        (65, 104, 76, 90),
        20,
    )
    if names:
        public_count = data.get("_player_total", len(names))
        player_label = f"在线玩家 · 已公开 {public_count} / {players_online}"
        if data.get("player_list_cached"):
            player_label += " · API 缓存样本"
        elif data.get("player_list_source") == "query":
            player_label += " · Query 名单"
        player_text = ""
    elif online and players_online > 0:
        player_label = "在线玩家"
        player_text = "服务器未公开玩家名单"
    elif online:
        player_label = "在线玩家"
        player_text = "暂无玩家在线"
    else:
        player_label = "在线玩家"
        player_text = "-"
    draw.text((106, players_top + 16), player_label, font=label_font, fill=(145, 171, 152, 255))
    if data.get("_player_pages", 1) > 1:
        page_label = f"{data['_player_page']} / {data['_player_pages']} 页"
        draw.text(
            (1048 - draw.textlength(page_label, font=label_font), players_top + 16),
            page_label,
            font=label_font,
            fill=(116, 145, 125),
        )
    name_font = _load_font(21, font_path=font_path)
    row_y = players_top + 55
    for row in rows:
        x = 106
        for chip in row["chips"]:
            _rounded_panel(
                draw,
                (x, row_y, x + chip["width"], row_y + chip["height"]),
                (29, 49, 37, 255),
                (53, 82, 62, 180),
                12,
            )
            for index, line in enumerate(chip["lines"]):
                draw.text(
                    (x + 16, row_y + 10 + index * 28),
                    line,
                    font=name_font,
                    fill=(220, 238, 225),
                    anchor="lt",
                )
            x += chip["width"] + 10
        row_y += row["height"] + 10
    if player_text:
        draw.text((106, row_y + 2), player_text, font=label_font, fill=(166, 189, 173))

    now = int(time.time()) if now is None else now
    _draw_history(
        image, history or [], now, sample_interval, history_enabled, font_path, history_top
    )
    source = "公开 API（可能有缓存）" if data.get("source") == "api" else "Minecraft 直连"
    if data.get("query_failed"):
        source = "本次查询失败"
    update_time = datetime.fromtimestamp(now, CHINA_TIME).strftime("%m-%d %H:%M")
    footer = f"{bot_name} · itxiaohui  ·  {source}  ·  {update_time} (UTC+8)"
    draw.text(
        (84, card_height - 83),
        _ellipsize(draw, footer, footer_font, 1010),
        font=footer_font,
        fill=(116, 145, 125, 255),
    )

    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def history_segments(history: list[dict], now: int, interval: int) -> list[list[dict]]:
    """Split at failures, offline responses, and missing samples instead of inventing a connecting line."""
    segments, current = [], []
    for point in sorted(history, key=lambda p: p["timestamp"]):
        if not now - 86400 <= point["timestamp"] <= now:
            continue
        valid = point.get("online") == 1 and point.get("players") is not None
        if not valid or (
            current and point["timestamp"] - current[-1]["timestamp"] > interval * 2.5
        ):
            if current:
                segments.append(current)
                current = []
        if valid:
            current.append(point)
    if current:
        segments.append(current)
    return segments


def _draw_history(image, history, now, interval, enabled, font_path, panel_top):
    draw = ImageDraw.Draw(image, "RGBA")
    _rounded_panel(
        draw, (84, panel_top, 1072, panel_top + 384), (17, 31, 24, 230), (65, 104, 76, 90), 22
    )
    title = _load_font(25, True, font_path)
    font = _load_font(19, font_path=font_path)
    small = _load_font(17, font_path=font_path)
    draw.text(
        (106, panel_top + 16), "最近 24 小时 · 在线玩家波动", font=title, fill=(226, 236, 229)
    )
    draw.text((928, panel_top + 22), "24H / UTC+8", font=small, fill=(116, 145, 125))
    history = sorted(
        (p for p in history if now - 86400 <= p["timestamp"] <= now), key=lambda p: p["timestamp"]
    )
    counts = [
        p["players"] for p in history if p.get("online") == 1 and p.get("players") is not None
    ]
    if counts:
        metrics = [
            f"最低  {min(counts)}",
            f"最高  {max(counts)}",
            f"均值  {sum(counts) / len(counts):.1f}",
        ]
        x = 106
        for text in metrics:
            width = int(draw.textlength(text, font=font)) + 30
            _rounded_panel(
                draw, (x, panel_top + 56, x + width, panel_top + 90), (27, 48, 35, 255), radius=13
            )
            draw.text((x + 15, panel_top + 62), text, font=font, fill=(157, 221, 176), anchor="lt")
            x += width + 12
    else:
        draw.text((106, panel_top + 62), "暂无成功的人数采样", font=font, fill=(145, 171, 152))

    # Supersampling keeps curved strokes and their round ends smooth on QQ images.
    scale, width, height = 3, 988, 212
    layer = Image.new("RGBA", (width * scale, height * scale))
    chart = ImageDraw.Draw(layer, "RGBA")
    left, right, top, bottom = 78, 950, 10, 180
    maximum = max(counts, default=1)
    step = max(1, math.ceil(maximum / 4))
    ceiling = step * 4
    for index in range(5):
        y = bottom - index * (bottom - top) / 4
        chart.line(
            (left * scale, y * scale, right * scale, y * scale), fill=(48, 70, 55, 160), width=2
        )
        label = str(index * step)
        draw.text(
            (84 + left - 14 - draw.textlength(label, font=small), panel_top + 101 + y - 11),
            label,
            font=small,
            fill=(116, 145, 125),
        )
    for index in range(5):
        x = left + index * (right - left) / 4
        chart.line(
            (x * scale, top * scale, x * scale, bottom * scale), fill=(40, 61, 47, 100), width=2
        )
        stamp = datetime.fromtimestamp(now - 86400 + index * 21600, CHINA_TIME).strftime("%H:%M")
        draw.text(
            (84 + x - draw.textlength(stamp, font=small) / 2, panel_top + 290),
            stamp,
            font=small,
            fill=(116, 145, 125),
        )

    def coordinates(point):
        return (
            left + (point["timestamp"] - (now - 86400)) / 86400 * (right - left),
            bottom - point["players"] / ceiling * (bottom - top),
        )

    gradient = Image.new("RGBA", layer.size)
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(top * scale, bottom * scale + 1):
        alpha = int(48 * (1 - (y - top * scale) / ((bottom - top) * scale))) + 4
        gradient_draw.line((left * scale, y, right * scale, y), fill=(71, 218, 135, alpha))
    last_point = None
    for segment in history_segments(history, now, interval):
        samples = [coordinates(point) for point in segment]
        curve = smooth_points(samples, resolution=0.5)
        scaled = [(x * scale, y * scale) for x, y in curve]
        if len(curve) > 1:
            mask = Image.new("L", layer.size)
            ImageDraw.Draw(mask).polygon(
                scaled + [(scaled[-1][0], bottom * scale), (scaled[0][0], bottom * scale)], fill=255
            )
            fill = gradient.copy()
            fill.putalpha(ImageChops.multiply(gradient.getchannel("A"), mask))
            layer.alpha_composite(fill)
            chart.line(scaled, fill=(47, 139, 90, 65), width=9 * scale, joint="curve")
            chart.line(scaled, fill=(107, 231, 154, 255), width=3 * scale, joint="curve")
        for x, y in samples[:1] + samples[-1:] if len(samples) > 1 else samples:
            radius = 1.5 * scale
            chart.ellipse(
                (x * scale - radius, y * scale - radius, x * scale + radius, y * scale + radius),
                fill=(107, 231, 154, 255),
            )
        if samples:
            last_point = samples[-1]
    if last_point:
        x, y = (coordinate * scale for coordinate in last_point)
        chart.ellipse(
            (x - 6 * scale, y - 6 * scale, x + 6 * scale, y + 6 * scale), fill=(67, 167, 106, 50)
        )
        chart.ellipse(
            (x - 3.5 * scale, y - 3.5 * scale, x + 3.5 * scale, y + 3.5 * scale),
            fill=(191, 255, 212, 255),
        )
    for point in history:
        if point.get("online") != 1:
            x = left + (point["timestamp"] - (now - 86400)) / 86400 * (right - left)
            color = (241, 96, 89, 255) if point.get("online") == 0 else (212, 175, 99, 255)
            chart.rounded_rectangle(
                (
                    x * scale - 1 * scale,
                    (bottom - 5) * scale,
                    x * scale + 1 * scale,
                    bottom * scale,
                ),
                radius=scale,
                fill=color,
            )
    image.alpha_composite(
        layer.resize((width, height), Image.Resampling.LANCZOS), (84, panel_top + 101)
    )
    if not counts:
        text = "开始采集后将在这里显示曲线" if not history else "服务器离线或查询失败，暂无人数曲线"
        draw.text((302, panel_top + 184), text, font=font, fill=(145, 171, 152))
    if len(counts) == 1:
        note = "仅有 1 个人数采样，继续运行后自动形成曲线"
    else:
        note = f"人数采样 {len(counts)} 次 · 间隔 {interval // 60} 分钟 · 红：离线  黄：失败  空白：无数据"
    if not enabled:
        note = "后台采样已关闭，仅记录手动查询 · 空白时段没有数据"
    draw.text(
        (106, panel_top + 330), _ellipsize(draw, note, small, 936), font=small, fill=(116, 145, 125)
    )
    if any(p.get("source") == "api" for p in history):
        text, color = "部分采样来自公开 API，人数可能有缓存延迟 · 时间 UTC+8", (212, 175, 99)
    else:
        text, color = "曲线经过实测采样点 · 缺失时段保持断线 · 时间 UTC+8", (116, 145, 125)
    draw.text((106, panel_top + 356), text, font=small, fill=color)
