"""AstrBot MOTD plugin, using platform-neutral messages for official QQ bots."""

import asyncio
import contextlib
import json
import time
import uuid

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .services.address import normalize_address
from .services.mc_api import MinecraftServerAPI
from .services.monitor import PlayerMonitor
from .services.motd_card import render_motd_cards
from .services.permissions import get_group_role
from .services.storage import MotdStore

HELP = """Minecraft MOTD 查询
/motd                         查询本群默认服务器
/motd 服务器地址               临时查询（不改变默认设置）
/motd set 服务器地址           设置本群服务器（群主/群管理员/机器人管理员）
/motd unset                   清除本群设置（群主/群管理员/机器人管理员）
/motd identity                查看本群授权标识（平台无法识别群角色时使用）
/motd history [服务器地址]     查看最近 24 小时玩家波动
/motd help                    查看帮助
别名：/mc、/mcstatus；设置、清除、取消、波动、趋势也可使用。
服务器地址示例：mc.example.com 或 mc.example.com:25565
每张状态卡自带 24 小时波动图；首次使用从当前时刻开始积累。"""


class MotdPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_motd")
        self.store = MotdStore(self.data_dir / "motd.db")
        self.api = MinecraftServerAPI(config)
        self._ready = False
        self._init_lock = asyncio.Lock()
        self.monitor = PlayerMonitor(
            self.api,
            self.store,
            interval=max(60, int(config.get("sample_interval", 300))),
            retention_hours=max(24, int(config.get("retention_hours", 48))),
            enabled=bool(config.get("history_enabled", True)),
            addresses=[
                normalize_address(a) for a in config.get("monitor_servers", []) if a.strip()
            ],
        )

    async def initialize(self):
        async with self._init_lock:
            if self._ready:
                return
            await self.store.initialize()
            self.monitor.start()
            self._ready = True

    @staticmethod
    def _scope(event: AstrMessageEvent) -> str:
        # Official QQ uses group openids, which differ from old OneBot numeric group IDs.
        # Include the adapter instance to prevent collisions between bots/platforms.
        group = event.get_group_id()
        if group:
            return json.dumps([event.get_platform_id(), "group", group], ensure_ascii=False)
        return event.unified_msg_origin

    def _is_admin(self, event):
        return event.is_admin() or str(event.get_sender_id()) in {
            str(value) for value in self.config.get("admin_ids", [])
        }

    @staticmethod
    def _group_authorization_key(event):
        return f"{event.get_platform_id()}|{event.get_group_id()}|{event.get_sender_id()}"

    async def _can_manage_group(self, event):
        if self._is_admin(event):
            return True, True
        # Manual grants are confined to one adapter instance, group and user.
        if self._group_authorization_key(event) in self.config.get("group_admin_ids", []):
            return True, True
        role = await get_group_role(event)
        return role in {"owner", "admin"}, role is not None

    @filter.command("motd", alias={"mc", "mcstatus"})
    async def motd(self, event: AstrMessageEvent, action: str = "", address: str = ""):
        """查询 Minecraft 状态、设置群默认服务器，以及查看 24 小时玩家波动图。"""
        try:
            await self.initialize()
            await self._handle(event, action, address)
        except ValueError as exc:
            await event.send(event.plain_result(f"❌ {exc}"))
        except Exception:
            logger.exception("MOTD 插件处理失败")
            await event.send(
                event.plain_result("❌ MOTD 处理失败，请稍后重试或查看 AstrBot 日志。")
            )
        finally:
            event.stop_event()

    async def _handle(self, event, action, address):
        action = action.strip()
        verb = action.lower()
        scope = self._scope(event)
        if verb in {"help", "帮助"}:
            await event.send(event.plain_result(HELP))
            return
        if verb in {"identity", "身份"}:
            if not event.get_group_id():
                await event.send(event.plain_result("⚠️ 请在群内查看授权标识。"))
            else:
                await event.send(
                    event.plain_result(
                        "本群授权标识（仅供机器人管理员配置，本命令不会授予权限）：\n"
                        + self._group_authorization_key(event)
                    )
                )
            return
        if verb in {"set", "设置", "unset", "clear", "清除", "取消"}:
            if not event.get_group_id():
                await event.send(
                    event.plain_result(
                        "⚠️ 请在群内设置或清除默认服务器；私聊可使用 /motd 服务器地址。"
                    )
                )
                return
            allowed, verified = await self._can_manage_group(event)
            if not allowed:
                message = "❌ 只有本群群主、群管理员或机器人管理员可以设置或清除 MOTD 服务器。"
                if not verified:
                    message += (
                        "\n⚠️ 平台未提供可用的群角色信息，暂时无法核实身份。"
                        "请检查群成员查询接口权限，或发送 /motd identity，"
                        "由机器人管理员在插件配置 group_admin_ids 中按群授权。"
                    )
                await event.send(event.plain_result(message))
                return
            if verb in {"set", "设置"}:
                if not address:
                    raise ValueError("格式: /motd set 服务器地址")
                address = normalize_address(address)
                await self.store.set_binding(scope, address, event.get_sender_id())
                await event.send(event.plain_result(f"✅ 已将本群 MOTD 服务器设为: {address}"))
                await self._query_and_send(event, address)
            else:
                if address:
                    raise ValueError("格式: /motd unset")
                await self.store.clear_binding(scope)
                await event.send(event.plain_result("✅ 已清除本群 MOTD 服务器设置。"))
            return
        if verb in {"history", "chart", "波动", "趋势", "历史"}:
            target = address or await self.store.get_binding(scope)
        else:
            if address:
                raise ValueError("格式: /motd 服务器地址；查看帮助: /motd help")
            target = action or await self.store.get_binding(scope)
        if not target:
            await event.send(
                event.plain_result(
                    "⚠️ 本群还没有设置 MOTD 服务器。\n管理员请使用: /motd set 服务器地址\n"
                    "也可以临时查询: /motd 服务器地址"
                )
            )
            return
        await self._query_and_send(event, normalize_address(target))

    async def _query_and_send(self, event, address):
        await self.store.track(address)
        await event.send(event.plain_result(f"🔍 正在查询 {address} ..."))
        data = await self.monitor.query(address)
        now = int(time.time())
        history = await self.store.history(address, now)
        if data is None:
            # Keep the previous history visible even when the current query failed.
            data = {"address": address, "online": False, "query_failed": True}
        path = self.data_dir / f"card-{uuid.uuid4().hex}.png"
        try:
            cards = await asyncio.to_thread(
                render_motd_cards,
                data,
                str(self.config.get("bot_name", "xiaohuicat")),
                history,
                now,
                self.monitor.interval,
                self.monitor.enabled,
                str(self.config.get("font_path", "")),
            )
            for image_bytes in cards:
                await asyncio.to_thread(path.write_bytes, image_bytes)
                # Finish each upload before replacing the local file with the next page.
                await event.send(event.image_result(str(path.resolve())))
        except Exception:
            logger.exception("MOTD 图片生成或发送失败: %s", address)
            text = self.api.format_server_info(None if data.get("query_failed") else data)
            counts = [p["players"] for p in history if p["players"] is not None]
            if counts:
                text += (
                    f"\n24小时采样: {len(counts)} 次，人数最低 {min(counts)}，最高 {max(counts)}"
                )
            else:
                text += "\n24小时波动: 暂无成功采样，正在积累历史。"
            await event.send(event.plain_result(text))
        finally:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    async def terminate(self):
        await self.monitor.close()
