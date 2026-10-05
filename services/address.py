"""Validate Minecraft addresses without changing implicit SRV semantics."""

import ipaddress
import re

LABEL = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def normalize_address(value: str) -> str:
    value = value.strip().lower()
    if not value or len(value) > 259 or "/" in value:
        raise ValueError("服务器地址格式不正确。请输入域名或 IP，可附带 :端口。")
    # Brackets make IPv6 + port unambiguous.
    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)\](?::([0-9]{1,5}))?", value)
        if not match:
            raise ValueError("IPv6 地址请使用 [IPv6]:端口 格式。")
        try:
            ipaddress.IPv6Address(match[1])
        except ValueError as exc:
            raise ValueError("IPv6 地址格式不正确。") from exc
        port = match[2]
    else:
        host, separator, port = value.partition(":")
        if len(host) > 253 or not all(LABEL.fullmatch(p) for p in host.split(".")):
            raise ValueError("服务器地址格式不正确。")
        if separator and (not port.isascii() or not port.isdigit()):
            raise ValueError("端口必须是 1 到 65535 的整数。")
    if port and not 1 <= int(port) <= 65535:
        raise ValueError("端口必须是 1 到 65535 的整数。")
    return value
