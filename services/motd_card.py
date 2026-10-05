"""Minecraft MOTD 图片卡片渲染器。"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import math
import re
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_WIDTH = 1200
CARD_HEIGHT = 1080
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
    for y in range(CARD_HEIGHT):
        ratio = y / max(CARD_HEIGHT - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line((0, y, CARD_WIDTH, y), fill=(*color, 255))

    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    palette = [(17, 39, 26, 255), (20, 46, 29, 255), (15, 34, 24, 255)]
    for index in range(34):
        x = (digest[index % len(digest)] * 47 + index * 113) % CARD_WIDTH
        y = (digest[(index + 9) % len(digest)] * 31 + index * 71) % CARD_HEIGHT
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


def _wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 2
) -> list[str]:
    clean = (
        html.unescape(re.sub(r"§.", "", str(text))).replace("\r", " ").replace("\n", " ").strip()
    )
    if not clean:
        return []
    lines: list[str] = []
    current = ""
    for char in clean:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current)
    consumed = "".join(lines)
    if len(consumed) < len(clean) and lines:
        lines[-1] = _ellipsize(draw, lines[-1] + "...", font, max_width)
    return lines[:max_lines]


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

    image = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (12, 23, 18, 255))
    _draw_pixel_background(image, address)
    draw = ImageDraw.Draw(image, "RGBA")

    # 主面板和左侧像素饰条
    _rounded_panel(draw, (44, 42, 1156, 1040), (8, 16, 13, 222), (77, 123, 90, 110), 30)
    draw.rounded_rectangle((44, 42, 58, 1040), radius=7, fill=accent)
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
    motd_font = _load_font(25, font_path=font_path)
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

    # MOTD
    _rounded_panel(draw, (84, 452, 714, 568), (17, 31, 24, 230), (65, 104, 76, 90), 18)
    draw.text((106, 468), "MOTD", font=label_font, fill=(145, 171, 152, 255))
    motd = str(data.get("motd") or ("暂无服务器标语" if online else "无法连接到该服务器"))
    for line_index, line in enumerate(_wrap_text(draw, motd, motd_font, 580, 2)):
        draw.text((106, 500 + line_index * 32), line, font=motd_font, fill=(226, 236, 229, 255))

    # 在线玩家名单（Minecraft 状态协议可能只返回部分样本）
    _rounded_panel(draw, (736, 452, 1072, 568), (17, 31, 24, 230), (65, 104, 76, 90), 18)
    player_list = [str(name) for name in (data.get("player_list") or []) if str(name).strip()]
    if player_list:
        public_count = len(player_list)
        player_label = f"在线玩家（已公开 {public_count}/{players_online}）"
        if data.get("player_list_cached"):
            player_label = "公开玩家（API 缓存样本）"
        player_text = "、".join(player_list)
    elif online and players_online > 0:
        player_label = "在线玩家"
        player_text = "服务器未公开玩家名单"
    elif online:
        player_label = "在线玩家"
        player_text = "暂无玩家在线"
    else:
        player_label = "在线玩家"
        player_text = "-"
    draw.text(
        (758, 468),
        _ellipsize(draw, player_label, label_font, 292),
        font=label_font,
        fill=(145, 171, 152, 255),
    )
    for line_index, line in enumerate(_wrap_text(draw, player_text, label_font, 290, 2)):
        draw.text((758, 504 + line_index * 28), line, font=label_font, fill=(226, 236, 229, 255))

    now = int(time.time()) if now is None else now
    _draw_history(draw, history or [], now, sample_interval, history_enabled, font_path)
    source = "公开 API（可能有缓存）" if data.get("source") == "api" else "Minecraft 直连"
    if data.get("query_failed"):
        source = "本次查询失败"
    update_time = datetime.fromtimestamp(now, CHINA_TIME).strftime("%m-%d %H:%M")
    footer = f"{bot_name} · itxiaohui  ·  {source}  ·  {update_time} (UTC+8)"
    draw.text(
        (84, 996),
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


def _draw_history(draw, history, now, interval, enabled, font_path):
    _rounded_panel(draw, (84, 590, 1072, 974), (17, 31, 24, 230), (65, 104, 76, 90), 18)
    title = _load_font(25, True, font_path)
    font = _load_font(19, font_path=font_path)
    small = _load_font(17, font_path=font_path)
    draw.text((106, 606), "最近 24 小时 · 在线玩家波动", font=title, fill=(226, 236, 229))
    history = sorted(
        (p for p in history if now - 86400 <= p["timestamp"] <= now), key=lambda p: p["timestamp"]
    )
    counts = [
        p["players"] for p in history if p.get("online") == 1 and p.get("players") is not None
    ]
    if counts:
        summary = f"最低 {min(counts)}  /  最高 {max(counts)}  /  采样均值 {sum(counts) / len(counts):.1f}"
    else:
        summary = "暂无成功的人数采样"
    draw.text((106, 644), summary, font=font, fill=(145, 171, 152))
    # Fixed rolling 24h axis: partial histories only occupy their actual time range.
    left, right, top, bottom = 162, 1034, 694, 874
    maximum = max(counts, default=1)
    step = max(1, math.ceil(maximum / 4))
    ceiling = step * 4
    for index in range(5):
        y = bottom - index * (bottom - top) / 4
        draw.line((left, y, right, y), fill=(48, 70, 55), width=1)
        label = str(index * step)
        draw.text(
            (left - 14 - draw.textlength(label, font=small), y - 12),
            label,
            font=small,
            fill=(116, 145, 125),
        )
    for index in range(5):
        x = left + index * (right - left) / 4
        draw.line((x, top, x, bottom), fill=(32, 51, 39), width=1)
        stamp = datetime.fromtimestamp(now - 86400 + index * 21600, CHINA_TIME).strftime("%H:%M")
        draw.text(
            (x - draw.textlength(stamp, font=small) / 2, 883),
            stamp,
            font=small,
            fill=(116, 145, 125),
        )

    def coordinates(p):
        return (
            left + (p["timestamp"] - (now - 86400)) / 86400 * (right - left),
            bottom - p["players"] / ceiling * (bottom - top),
        )

    for segment in history_segments(history, now, interval):
        points = [coordinates(p) for p in segment]
        if len(points) > 1:
            draw.line(points, fill=(91, 214, 111), width=3)
        for x, y in points:
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(123, 238, 141))
    for point in history:
        if point.get("online") != 1:
            x = left + (point["timestamp"] - (now - 86400)) / 86400 * (right - left)
            color = (241, 96, 89) if point.get("online") == 0 else (212, 175, 99)
            draw.line((x, bottom - 6, x, bottom), fill=color, width=2)
    if not counts:
        text = "开始采集后将在这里显示曲线" if not history else "服务器离线或查询失败，暂无人数曲线"
        draw.text((left + 140, top + 70), text, font=font, fill=(145, 171, 152))
    if len(counts) == 1:
        note = "仅有 1 个人数采样，继续运行后自动形成曲线"
    else:
        note = f"人数采样 {len(counts)} 次 · 间隔 {interval // 60} 分钟 · 红：离线  黄：失败  空白：无数据"
    if not enabled:
        note = "后台采样已关闭，仅记录手动查询 · 空白时段没有数据"
    draw.text((106, 918), _ellipsize(draw, note, small, 936), font=small, fill=(116, 145, 125))
    if any(p.get("source") == "api" for p in history):
        draw.text(
            (106, 944),
            "部分采样来自公开 API，人数可能有缓存延迟 · 时间 UTC+8",
            font=small,
            fill=(212, 175, 99),
        )
    else:
        draw.text(
            (106, 944),
            "从首次查询或绑定开始积累 · 重启后保留历史 · 时间 UTC+8",
            font=small,
            fill=(116, 145, 125),
        )
