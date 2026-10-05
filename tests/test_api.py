import asyncio
import json
from types import SimpleNamespace

import httpx

from services.mc_api import MinecraftServerAPI


async def test_real_java_status_protocol_on_local_server():
    api = MinecraftServerAPI({"ping_timeout": 2})

    async def extra(*args, **kwargs):
        return {
            "online": True,
            "software": "Paper",
            "plugins": {"names": ["Demo"]},
            "players_online": 999,
            "latency_ms": 999,
        }

    api._query_api = extra

    async def handle(reader, writer):
        try:
            length = await api._read_varint(reader)
            handshake = await reader.readexactly(length)
            assert handshake[0] == 0
            assert await reader.readexactly(2) == b"\x01\x00"
            payload = json.dumps(
                {
                    "version": {"name": "1.21.1", "protocol": 767},
                    "players": {
                        "online": 2,
                        "max": 50,
                        "sample": [{"name": "Alex", "id": "00000000-0000-0000-0000-000000000000"}],
                    },
                    "description": {"text": "Hello server"},
                }
            ).encode()
            packet = b"\x00" + api._encode_varint(len(payload)) + payload
            writer.write(api._encode_varint(len(packet)) + packet)
            await writer.drain()
            size = await api._read_varint(reader)
            ping = await reader.readexactly(size)
            writer.write(api._encode_varint(len(ping)) + ping)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        result = await api.query(f"127.0.0.1:{port}")
        assert result["source"] == "direct"
        assert result["software"] == "Paper" and result["plugins"]["names"] == ["Demo"]
        assert result["players_online"] == 2 and result["players_max"] == 50
        assert result["player_list"] == ["Alex"] and result["version"] == "1.21.1"
        assert result["motd"] == "Hello server" and result["latency_ms"] >= 0
        # Exercise the migrated fallback ping/pong path too.
        latency = await api._measure_minecraft_latency("127.0.0.1", port, "127.0.0.1", 767)
        assert latency is not None and latency >= 0
    finally:
        server.close()
        await server.wait_closed()


async def test_api_fallback_and_player_normalization(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise OSError("direct unavailable")

    monkeypatch.setattr("services.mc_api.JavaServer.async_lookup", unavailable)
    real_client = httpx.AsyncClient
    seen = []

    def response(request):
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "online": True,
                "version": "Paper 1.21",
                "port": 25565,
                "motd": {"clean": ["First", "Second"]},
                "players": {
                    "online": 5,
                    "max": 20,
                    "list": ["Steve", {"name": "Alex"}, {"username": "Third"}],
                },
                "plugins": {"names": ["Example"]},
                "software": "Paper",
            },
        )

    monkeypatch.setattr(
        "services.mc_api.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(response), **kwargs),
    )
    api = MinecraftServerAPI({})
    data = await api.query("mc.example.com")
    assert data["source"] == "api" and data["motd"] == "First | Second"
    assert data["player_list"] == ["Steve", "Alex", "Third"]
    assert seen == ["https://api.mcsrvstat.us/2/mc.example.com"]  # no forced :25565
    text = api.format_server_info(data)
    assert "Steve" in text and "插件: 1 个" in text and "缓存" in text


async def test_api_offline_different_from_transport_error(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise OSError("down")

    monkeypatch.setattr("services.mc_api.JavaServer.async_lookup", unavailable)
    real_client = httpx.AsyncClient
    status = [200]
    monkeypatch.setattr(
        "services.mc_api.httpx.AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(status[0], json={"online": False})
            ),
            **kwargs,
        ),
    )
    api = MinecraftServerAPI({})
    assert (await api.query("a.test"))["online"] is False
    status[0] = 503
    assert await api.query("a.test") is None


async def test_srv_lookup_input_is_not_changed(monkeypatch):
    seen = []

    async def status(**kwargs):
        return SimpleNamespace(
            raw={},
            version=SimpleNamespace(name="1.21"),
            motd=SimpleNamespace(to_plain=lambda: "Hi"),
            players=SimpleNamespace(online=0, max=20),
            latency=5,
        )

    async def lookup(address, **kwargs):
        seen.append(address)
        return SimpleNamespace(
            address=SimpleNamespace(host="srv-target.test", port=25570), async_status=status
        )

    monkeypatch.setattr("services.mc_api.JavaServer.async_lookup", lookup)
    api = MinecraftServerAPI({})

    async def no_extra(*args, **kwargs):
        return None

    api._query_api = no_extra
    data = await api.query("mc.example.com")
    assert seen == ["mc.example.com"] and data["port"] == 25570
