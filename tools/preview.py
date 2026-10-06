"""Render synthetic examples: generated data is never written to the plugin database."""

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.motd_card import render_motd_cards


def main():
    now = int(time.time())
    history = []
    for i in range(289):
        stamp = now - 86400 + i * 300
        players = max(0, int(45 + 25 * math.sin(i / 32) + 10 * math.sin(i / 7)))
        history.append({"timestamp": stamp, "online": 1, "players": players, "source": "direct"})
    # Include a gap and downtime to verify broken curves.
    history = [p for i, p in enumerate(history) if not 85 <= i < 100]
    for point in history[165:175]:
        point.update(online=0, players=None)
    data = {
        "online": True,
        "address": "mc.example.com:25565",
        "version": "1.21.1",
        "motd": "欢迎来到栗子服务器！生存 · 建筑 · 冒险，和朋友一起探索方块世界",
        "motd_raw": {
            "text": "xiaohuicat",
            "color": "#80EAC0",
            "bold": True,
            "extra": [
                {"text": "  生存 · 建筑 · 冒险", "color": "gold", "bold": False},
                {"text": "\n欢迎和朋友一起探索方块世界！", "color": "aqua", "bold": False},
                {"text": "  §d温馨社区 §a自由冒险"},
            ],
        },
        "players_online": history[-1]["players"],
        "players_max": 100,
        "player_list": [
            "itxiaohui",
            "Steve",
            "Alex",
            "栗子玩家",
            "Builder_Orange",
            "DreamExplorer",
            "xiaohuicat",
            "AdventureCat",
            "RedStone_Master",
            "Minecraft_Lover",
            "Moonlight",
            "世界探索者",
        ],
        "latency_ms": 32.6,
        "software": "Paper",
        "plugins": {"names": ["Example"]},
        "source": "direct",
    }
    folder = ROOT / "dist" / "previews"
    folder.mkdir(parents=True, exist_ok=True)
    examples = {
        "24h": (data, history),
        "first": (data, history[-1:]),
        "offline": ({"online": False, "address": data["address"], "source": "api"}, history),
        "failed": ({"online": False, "address": data["address"], "query_failed": True}, []),
        "many-players": (
            dict(
                data,
                player_list=[f"Player_{i:03d}_LongName" for i in range(1, 101)],
                players_online=100,
            ),
            history,
        ),
    }
    for name, (status, points) in examples.items():
        for index, card in enumerate(
            render_motd_cards(status, "xiaohuicat · 演示数据", points, now), 1
        ):
            suffix = "" if index == 1 else f"-{index}"
            path = folder / f"motd-{name}{suffix}.png"
            path.write_bytes(card)
            print(path)


if __name__ == "__main__":
    main()
