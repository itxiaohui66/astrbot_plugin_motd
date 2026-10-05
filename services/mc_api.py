"""Minecraft Java 直连查询、真实 Ping 和原项目公开 API 备用服务。"""

import asyncio
import logging
import struct
import time
from collections import OrderedDict
from urllib.parse import quote

import httpx
from mcstatus import JavaServer

logger = logging.getLogger("qqbot.mc_api")


class MinecraftServerAPI:
    """Minecraft 服务器状态查询客户端"""

    def __init__(self, config: dict):
        mc_cfg = config.get("mc_status", config)
        self.api_url: str = mc_cfg.get("api_url", "https://api.mcsrvstat.us/2/{address}")
        self.timeout: float = mc_cfg.get("request_timeout", 10)
        self.ping_timeout: float = mc_cfg.get("ping_timeout", 4)
        self.default_port: int = mc_cfg.get("default_port", 25565)
        self._metadata = OrderedDict()

    async def query(self, address: str) -> dict | None:
        """Prefer uncached Java status for player history; retain the original public API fallback."""

        async def direct():
            server = await JavaServer.async_lookup(address, timeout=self.ping_timeout)
            status = await server.async_status(tries=1)
            raw = status.raw
            sample = raw.get("players", {}).get("sample", []) or []
            return {
                "online": True,
                "address": address,
                "hostname": "",
                "ip": server.address.host,
                "port": server.address.port,
                "version": status.version.name,
                "motd": status.motd.to_plain(),
                "players_online": status.players.online,
                "players_max": status.players.max,
                "player_list": [
                    str(p.get("name", "")) for p in sample if isinstance(p, dict) and p.get("name")
                ],
                "latency_ms": round(status.latency, 1),
                "software": raw.get("software", ""),
                "plugins": raw.get("plugins", {}),
                "icon": raw.get("favicon", ""),
                "source": "direct",
            }

        try:
            data = await asyncio.wait_for(direct(), timeout=self.ping_timeout + 2)
        except Exception as exc:
            logger.debug("MC 直连失败，使用原公开 API %s: %s", address, exc)
            data = await self._query_api(address)
            if data is not None:
                data["source"] = "api"
            return data
        # Java status does not normally expose software/plugin names. Preserve the
        # old API's extra fields while NEVER replacing direct player counts or RTT.
        cached = self._metadata.get(address)
        if not cached or time.monotonic() - cached[0] >= 3600:
            extra = await self._query_api(address, measure_latency=False)
            self._metadata[address] = (time.monotonic(), extra)
            self._metadata.move_to_end(address)
            while len(self._metadata) > 128:
                self._metadata.popitem(last=False)
        else:
            extra = cached[1]
        if extra and extra.get("online"):
            for key in ("hostname", "software", "plugins", "icon", "player_list"):
                if not data.get(key) and extra.get(key):
                    data[key] = extra[key]
                    if key == "player_list":
                        data["player_list_cached"] = True
        return data

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        value &= 0xFFFFFFFF
        encoded = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            encoded.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(encoded)

    @classmethod
    def _encode_string(cls, value: str) -> bytes:
        raw = value.encode("utf-8")
        return cls._encode_varint(len(raw)) + raw

    @staticmethod
    async def _read_varint(reader: asyncio.StreamReader) -> int:
        value = 0
        for position in range(5):
            byte = (await reader.readexactly(1))[0]
            value |= (byte & 0x7F) << (7 * position)
            if not byte & 0x80:
                return value
        raise ValueError("Minecraft VarInt 过长")

    async def _measure_minecraft_latency(
        self, connect_host: str, port: int, handshake_host: str, protocol: int
    ) -> float | None:
        """执行 Minecraft Java 状态握手与 ping/pong，返回真实 RTT。"""

        async def probe() -> float:
            reader, writer = await asyncio.open_connection(connect_host, port)
            try:
                handshake = (
                    self._encode_varint(0)
                    + self._encode_varint(protocol)
                    + self._encode_string(handshake_host)
                    + struct.pack(">H", port)
                    + self._encode_varint(1)
                )
                writer.write(self._encode_varint(len(handshake)) + handshake)
                writer.write(b"\x01\x00")  # status request: length=1, packet id=0
                await writer.drain()

                packet_length = await self._read_varint(reader)
                if not 1 <= packet_length <= 2_097_152:
                    raise ValueError("Minecraft 状态响应大小超出限制")
                if await self._read_varint(reader) != 0:
                    raise ValueError("非法的 Minecraft 状态响应")
                json_length = await self._read_varint(reader)
                if not 0 <= json_length <= packet_length:
                    raise ValueError("非法的 Minecraft JSON 长度")
                await reader.readexactly(json_length)

                payload = int(time.time_ns() // 1_000_000)
                ping_packet = self._encode_varint(1) + struct.pack(">q", payload)
                started = time.perf_counter()
                writer.write(self._encode_varint(len(ping_packet)) + ping_packet)
                await writer.drain()
                if await self._read_varint(reader) != 9:
                    raise ValueError("非法的 Minecraft pong 长度")
                if await self._read_varint(reader) != 1:
                    raise ValueError("非法的 Minecraft pong 响应")
                if await reader.readexactly(8) != struct.pack(">q", payload):
                    raise ValueError("Minecraft pong 校验失败")
                return round((time.perf_counter() - started) * 1000, 1)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        try:
            return await asyncio.wait_for(probe(), timeout=self.ping_timeout)
        except Exception as exc:
            logger.warning("Minecraft Ping 测量失败 %s:%s: %s", connect_host, port, exc)
            return None

    async def _query_api(self, address: str, measure_latency: bool = True) -> dict | None:
        """
        查询 Minecraft 服务器状态

        Args:
            address: 服务器地址，如 "mc.hypixel.net" 或 "mc.example.com:25565"

        Returns:
            服务器信息字典，失败返回 None
        """
        # 不要给纯域名强制补 :25565。查询服务会先解析
        # _minecraft._tcp SRV 记录；只有用户显式填写端口时才固定端口。
        url = self.api_url.format(address=quote(address, safe=""))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)

                if response.status_code != 200:
                    logger.warning(f"MC服务器查询失败: {address} HTTP {response.status_code}")
                    return None

                data = response.json()

                if not data.get("online", False):
                    return {
                        "online": False,
                        "address": address,
                        "latency_ms": None,
                    }

                # 提取 MOTD 纯文本
                motd_text = ""
                motd = data.get("motd", {})
                if isinstance(motd, dict):
                    motd_text = " | ".join(motd.get("clean", []))
                elif isinstance(motd, str):
                    motd_text = motd

                players = data.get("players", {})
                player_list = []
                for player in players.get("list", []) or []:
                    if isinstance(player, dict):
                        name = player.get("name") or player.get("username") or ""
                    else:
                        name = str(player)
                    name = name.strip()
                    if name:
                        player_list.append(name)
                resolved_ip = str(data.get("ip") or "")
                resolved_port = int(data.get("port") or self.default_port)
                protocol = int(data.get("protocol") or 47)
                handshake_host = address.rsplit(":", 1)[0] if ":" in address else address
                latency_ms = None
                if resolved_ip and measure_latency:
                    latency_ms = await self._measure_minecraft_latency(
                        resolved_ip, resolved_port, handshake_host, protocol
                    )
                return {
                    "online": True,
                    "address": address,
                    "hostname": data.get("hostname", ""),
                    "ip": data.get("ip", ""),
                    "port": resolved_port,
                    "version": data.get("version", "未知"),
                    "motd": motd_text,
                    "players_online": players.get("online", 0),
                    "players_max": players.get("max", 0),
                    "player_list": player_list,
                    "latency_ms": latency_ms,
                    "software": data.get("software", ""),
                    "plugins": data.get("plugins", {}),
                    "icon": data.get("icon", ""),
                }

        except httpx.TimeoutException:
            logger.error(f"MC服务器查询超时: {address}")
            return None
        except Exception as e:
            logger.error(f"MC服务器查询失败 ({address}): {e}")
            return None

    def format_server_info(self, data: dict | None) -> str:
        """格式化服务器信息为文本"""
        if not data:
            return "❌ 无法查询该服务器，请检查地址是否正确。"

        if not data.get("online", False):
            addr = data.get("address", "未知服务器")
            return f"⚠️ 服务器离线\n🖥️ 地址: {addr}"

        # 在线服务器
        lines = [
            "🟢 服务器在线",
        ]

        if data.get("hostname"):
            lines.append(f"🏷️ 名称: {data['hostname']}")

        addr = data.get("address", "")
        if addr:
            lines.append(f"🖥️ 地址: {addr}")

        lines.append(f"📦 版本: {data.get('version', '未知')}")

        if data.get("software"):
            lines.append(f"🔧 服务端: {data['software']}")

        motd = data.get("motd", "")
        if motd:
            # 限制标语长度
            if len(motd) > 200:
                motd = motd[:200] + "..."
            lines.append(f"📝 标语: {motd}")

        players_online = data.get("players_online", 0)
        players_max = data.get("players_max", 0)
        # 玩家数进度条
        if players_max > 0:
            bar_len = 10
            ratio = min(players_online / players_max, 1.0)
            filled = int(bar_len * ratio)
            bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(f"👥 在线: {players_online}/{players_max} [{bar}]")
        else:
            lines.append(f"👥 在线: {players_online}")

        latency = data.get("latency_ms")
        lines.append(f"📡 MC Ping: {latency}ms" if latency is not None else "📡 MC Ping: 未提供")

        # 如果安装了插件，显示数量
        plugins = data.get("plugins", {})
        if isinstance(plugins, dict) and plugins.get("names"):
            plug_count = len(plugins["names"])
            lines.append(f"🔌 插件: {plug_count} 个")

        player_list = data.get("player_list") or []
        if player_list:
            lines.append(
                f"👤 已公开玩家（{len(player_list)}/{players_online}）: "
                + "、".join(player_list)[:400]
            )
        elif players_online:
            lines.append("👤 服务器未公开玩家名单")
        else:
            lines.append("👤 暂无玩家在线")
        if data.get("source") == "api":
            lines.append("数据来源: 公开 API（可能有缓存）")
        if data.get("player_list_cached"):
            lines.append("玩家名单来源: API 缓存样本")

        return "\n".join(lines)
