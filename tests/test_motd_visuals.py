import io

import pytest
from PIL import Image, ImageDraw

from services.chart import smooth_points
from services.minecraft_text import TextStyle, motd_runs
from services.motd_card import (
    _layout_motd,
    player_pages,
    player_rows,
    render_motd_card,
    render_motd_cards,
)


def test_legacy_colors_hex_and_reset():
    runs = motd_runs("§cRed §lBold §rPlain §x§1§2§3§4§5§6Hex §#ABCDEFMore")
    assert "".join(run.text for run in runs) == "Red Bold Plain Hex More"
    assert runs[0].style.color == "#FF5555"
    assert runs[1].style.bold and runs[1].style.color == "#FF5555"
    assert runs[2].style == TextStyle()
    assert runs[3].style.color == "#123456"
    assert runs[4].style.color == "#ABCDEF"


def test_json_component_inheritance_and_explicit_reset():
    runs = motd_runs(
        {
            "text": "A",
            "color": "gold",
            "bold": True,
            "extra": [
                {"text": "B"},
                {"text": "C", "bold": False, "color": "#123456"},
                {"text": "D", "color": "reset"},
            ],
        }
    )
    assert "".join(run.text for run in runs) == "ABCD"
    assert runs[1].style.bold and runs[1].style.color == "#FFAA00"
    assert not runs[2].style.bold and runs[2].style.color == "#123456"
    assert runs[3].style == TextStyle()


def test_wrapping_preserves_long_motd_and_line_styles():
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text = "完整中文标语" * 100
    lines = _layout_motd(draw, motd_runs("§c" + text), 940, "")
    assert len(lines) > 2
    assert "".join(run.text for line in lines for run in line) == text
    assert all(run.style.color == "#FF5555" for line in lines for run in line)


def test_card_contains_real_colored_glyph_pixels():
    image = Image.open(
        io.BytesIO(
            render_motd_card(
                {
                    "address": "example.test",
                    "online": True,
                    "motd_raw": "§c████ §b████ §x§1§2§3§4§5§6████",
                },
                now=100000,
            )
        )
    )
    colors = image.crop((100, 495, 800, 540)).getcolors(800 * 100)
    colors = {color: count for count, color in colors}
    for color in [(255, 85, 85), (85, 255, 255), (18, 52, 86)]:
        assert colors.get(color, 0) > 20


def test_player_wrap_preserves_long_names_and_pages_all_players():
    names = [f"Player_{i:03d}_LongName" for i in range(100)] + ["超长中文名字" * 20]
    rows = player_rows(names)
    chips = [chip for row in rows for chip in row["chips"]]
    assert ["".join(chip["lines"]) for chip in chips] == names
    assert all(sum(chip["width"] + 10 for chip in row["chips"]) - 10 <= 944 for row in rows)
    pages = player_pages(names)
    assert len(pages) > 1 and [name for page in pages for name in page] == names


def test_multiple_cards_actually_draw_every_player(monkeypatch):
    names = [f"Player_{i:03d}_LongName" for i in range(100)]
    drawn = []
    original = ImageDraw.ImageDraw.text

    def track(self, position, text, *args, **kwargs):
        drawn.append(text)
        return original(self, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", track)
    cards = render_motd_cards(
        {"online": True, "address": "example.test", "player_list": names, "players_online": 100},
        now=100000,
    )
    assert len(cards) > 1
    assert all(drawn.count(name) == 1 for name in names)
    assert all(Image.open(io.BytesIO(card)).height < 1800 for card in cards)


@pytest.mark.parametrize(
    "points",
    [
        [(0, 0), (10, 100), (20, 0)],
        [(0, 8), (1, 8), (20, 2), (30, 2)],
        [(0, 0), (0.3, 2), (50, 4)],
        [(0, 10), (8, 0), (15, 20), (30, 1)],
    ],
)
def test_curve_passes_samples_and_does_not_invent_extrema(points):
    curve = smooth_points(points, 0.25)
    assert curve[0] == points[0] and curve[-1] == pytest.approx(points[-1])
    for x, y in points:
        assert next(point[1] for point in curve if abs(point[0] - x) < 1e-8) == pytest.approx(y)
    for start, end in zip(points, points[1:]):
        for x, y in curve:
            if start[0] <= x <= end[0]:
                assert min(start[1], end[1]) - 1e-8 <= y <= max(start[1], end[1]) + 1e-8


def test_single_sample_duplicate_timestamp_and_zero_series():
    assert smooth_points([(5, 0)]) == [(5, 0)]
    assert smooth_points([(1, 0), (1, 2)]) == [(1, 2)]
    assert all(y == 0 for x, y in smooth_points([(0, 0), (10, 0), (20, 0)]))
