import io

import pytest
from PIL import Image

from services.address import normalize_address
from services.motd_card import history_segments, render_motd_card


@pytest.mark.parametrize(
    "address", ["mc.example.com", "127.0.0.1:25565", "localhost", "[::1]:25565", "[2001:db8::1]"]
)
def test_valid_address(address):
    assert normalize_address(" " + address.upper() + " ") == address


@pytest.mark.parametrize(
    "address",
    [
        "",
        "https://a.test",
        "a.test:0",
        "a.test:65536",
        "a.test:",
        "../a",
        "-a.test",
        "a..test",
        "[broken]:1",
        "a.test:１",
        "a.test;command",
    ],
)
def test_invalid_address(address):
    with pytest.raises(ValueError):
        normalize_address(address)


def test_history_does_not_bridge_offline_errors_or_shutdown():
    points = [
        {"timestamp": stamp, "online": online, "players": players}
        for stamp, online, players in [
            (100000, 1, 0),
            (100300, 1, 2),
            (100600, 0, None),
            (100900, 1, 5),
            (101200, None, None),
            (101500, 1, 7),
            (110000, 1, 10),
        ]
    ]
    segments = history_segments(points, 110000, 300)
    assert [[p["players"] for p in segment] for segment in segments] == [[0, 2], [5], [7], [10]]


@pytest.mark.parametrize(
    "online,failed,history",
    [
        (True, False, []),
        (False, False, []),
        (False, True, []),
        (True, False, [{"timestamp": 100000, "online": 1, "players": 0}]),
    ],
)
def test_card_handles_empty_history_offline_failure_and_zero(online, failed, history):
    raw = render_motd_card(
        {
            "online": online,
            "query_failed": failed,
            "address": "mc.example.com",
            "icon": "invalid-base64",
            "motd": "中文标语 " * 100,
            "players_online": 0,
            "players_max": 0,
        },
        history=history,
        now=100000,
    )
    image = Image.open(io.BytesIO(raw))
    assert image.format == "PNG" and image.size == (1200, 1080)
