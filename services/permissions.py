"""Resolve the sender's group role without confusing it with AstrBot's global admin role."""

import asyncio
import logging
from collections.abc import Mapping
from urllib.parse import quote

logger = logging.getLogger(__name__)
ROLES = {"owner", "admin", "member"}


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def role_of(value):
    for key in ("member_role", "role"):
        role = field(value, key)
        if isinstance(role, str) and role.lower() in ROLES:
            return role.lower()
    return None


async def get_group_role(event) -> str | None:
    """Return owner/admin/member, or None if the platform cannot verify membership."""
    group_id, user_id = str(event.get_group_id()), str(event.get_sender_id())
    if not group_id or not user_id:
        return None
    platform = event.get_platform_name()
    message = getattr(event, "message_obj", None)
    raw = field(message, "raw_message")
    if platform == "aiocqhttp":
        if (
            field(raw, "group_id", group_id) is not None
            and str(field(raw, "group_id", group_id)) != group_id
        ):
            return None
        if str(field(field(raw, "sender"), "user_id", user_id)) != user_id:
            return None
        role = role_of(field(raw, "sender"))
    elif platform in {"qq_official", "qq_official_webhook"}:
        # New AstrBot parsers retain raw_data even when botpy drops author fields.
        raw_data = field(raw, "raw_data")
        if str(field(raw_data, "group_openid", field(raw, "group_openid", group_id))) != group_id:
            return None
        author = field(raw_data, "author", field(raw, "author"))
        if str(field(author, "member_openid", user_id)) != user_id:
            return None
        role = role_of(field(raw_data, "author")) or role_of(field(raw, "author"))
    else:
        return None
    if role:
        return role

    async def lookup():
        bot = getattr(event, "bot", None)
        if platform == "aiocqhttp":
            routing = {}
            self_id = field(raw, "self_id")
            if self_id is not None:
                routing["self_id"] = self_id
            info = await bot.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=True,
                **routing,
            )
            if not isinstance(info, Mapping):
                return None
            if str(info.get("user_id", "")) != user_id or str(info.get("group_id", "")) != group_id:
                return None
            return role_of(info)
        # Reuse the adapter's authenticated SDK client; do not duplicate secrets.
        from botpy.http import Route

        info = await bot.api._http.request(
            Route(
                "GET",
                "/v2/groups/{group_openid}/members/{member_openid}",
                group_openid=quote(group_id, safe=""),
                member_openid=quote(user_id, safe=""),
            )
        )
        if not isinstance(info, Mapping):
            return None
        # Some clients wrap the member response; never accept a different user's role.
        info = info.get("data", info)
        if not isinstance(info, Mapping) or str(info.get("member_openid", "")) != user_id:
            return None
        response_group = info.get("group_openid")
        if response_group is not None and str(response_group) != group_id:
            return None
        return role_of(info)

    try:
        return await asyncio.wait_for(lookup(), timeout=5)
    except Exception as exc:
        logger.warning("MOTD 群角色查询不可用 (%s): %s", platform, type(exc).__name__)
        return None
