"""Framework-free adapter contract tests; the actual QQ account needs deployment verification."""

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest


class Event:
    def __init__(
        self,
        platform="qq-main",
        group="group-openid",
        admin=False,
        image_error=False,
        platform_name="qq_official",
        raw=None,
        user="user-openid",
    ):
        self.platform, self.group, self.admin = platform, group, admin
        self.platform_name, self.user = platform_name, user
        self.message_obj = types.SimpleNamespace(raw_message=raw)
        self.image_error = image_error
        self.unified_msg_origin = f"{platform}:private:user"
        self.messages = []
        self.stopped = False

    def get_group_id(self):
        return self.group

    def get_platform_id(self):
        return self.platform

    def get_platform_name(self):
        return self.platform_name

    def get_sender_id(self):
        return self.user

    def is_admin(self):
        return self.admin

    def plain_result(self, text):
        return ("text", text)

    def image_result(self, path):
        return ("image", path)

    def stop_event(self):
        self.stopped = True

    async def send(self, result):
        if result[0] == "image":
            assert Path(result[1]).is_file()
            if self.image_error:
                raise RuntimeError("QQ upload failed")
        self.messages.append(result)


@pytest.fixture
def plugin_class(monkeypatch, tmp_path):
    class Star:
        def __init__(self, context):
            self.context = context

    def command(name, alias):
        def decorate(func):
            func.registered_aliases = alias
            return func

        return decorate

    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.event": types.ModuleType("astrbot.api.event"),
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
    }
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("test")
    modules["astrbot.api.event"].AstrMessageEvent = Event
    modules["astrbot.api.event"].filter = types.SimpleNamespace(command=command)
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].StarTools = types.SimpleNamespace(
        get_data_dir=lambda name: tmp_path
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("plugin_under_test")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, "plugin_under_test", package)
    spec = importlib.util.spec_from_file_location("plugin_under_test.main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module.MotdPlugin


async def test_permission_binding_scope_aliases_and_clear(plugin_class):
    plugin = plugin_class(object(), {"history_enabled": False})

    async def no_send(*args):
        pass

    plugin._query_and_send = no_send
    ordinary = Event()
    await plugin.motd(ordinary, "set", "mc.example.com")
    assert "管理员" in ordinary.messages[-1][1]
    assert await plugin.store.get_binding(plugin._scope(ordinary)) is None
    admin = Event(admin=True)
    await plugin.motd(admin, "设置", "MC.Example.COM")
    assert await plugin.store.get_binding(plugin._scope(admin)) == "mc.example.com"
    assert await plugin.store.get_binding(plugin._scope(Event(platform="second-bot"))) is None
    await plugin.motd(admin, "清除")
    assert await plugin.store.get_binding(plugin._scope(admin)) is None
    assert admin.stopped and plugin.motd.registered_aliases == {"mc", "mcstatus"}
    await plugin.terminate()


async def test_query_image_fallback_and_file_cleanup(plugin_class, tmp_path):
    plugin = plugin_class(object(), {"history_enabled": False})

    async def query(address):
        return {
            "online": True,
            "address": address,
            "players_online": 5,
            "players_max": 20,
            "player_list": ["Alex"],
            "source": "direct",
        }

    plugin.api.query = query
    event = Event(image_error=True)
    await plugin.motd(event, "mc.example.com")
    assert "Alex" in event.messages[-1][1] and "人数最低 5" in event.messages[-1][1]
    assert not list(tmp_path.glob("card-*.png"))
    success = Event()
    await plugin.motd(success, "history", "mc.example.com")
    assert success.messages[-1][0] == "image"
    assert not list(tmp_path.glob("card-*.png"))
    await plugin.terminate()


async def test_missing_default_invalid_and_private_setting(plugin_class):
    plugin = plugin_class(object(), {"history_enabled": False})
    event = Event()
    await plugin.motd(event)
    assert "还没有设置" in event.messages[-1][1]
    await plugin.motd(event, "https://bad")
    assert "格式不正确" in event.messages[-1][1]
    private = Event(group="", admin=True)
    await plugin.motd(private, "set", "a.test")
    assert "请在群内" in private.messages[-1][1]
    await plugin.motd(event, "help")
    assert "/mcstatus" in event.messages[-1][1]
    await plugin.terminate()


@pytest.mark.parametrize("platform_name", ["qq_official", "qq_official_webhook", "aiocqhttp"])
@pytest.mark.parametrize("role", ["owner", "admin", "member"])
async def test_group_roles_can_set_and_clear_only_when_privileged(
    plugin_class, platform_name, role
):
    plugin = plugin_class(object(), {"history_enabled": False})

    async def no_send(*args):
        pass

    plugin._query_and_send = no_send
    raw = (
        {"sender": {"role": role}}
        if platform_name == "aiocqhttp"
        else {"author": {"member_role": role}}
    )
    event = Event(platform_name=platform_name, raw=raw)
    await plugin.motd(event, "set", "a.test")
    scope = plugin._scope(event)
    if role == "member":
        assert await plugin.store.get_binding(scope) is None
        assert "暂时无法核实" not in event.messages[-1][1]
        await plugin.store.set_binding(scope, "original.test", "bot-admin")
        await plugin.motd(event, "unset")
        assert await plugin.store.get_binding(scope) == "original.test"
    else:
        assert await plugin.store.get_binding(scope) == "a.test"
        await plugin.motd(event, "unset")
        assert await plugin.store.get_binding(scope) is None
    await plugin.terminate()


async def test_group_grant_is_limited_to_one_group_and_adapter(plugin_class):
    plugin = plugin_class(
        object(),
        {"history_enabled": False, "group_admin_ids": ["qq-main|group-openid|user-openid"]},
    )

    async def no_send(*args):
        pass

    plugin._query_and_send = no_send
    event = Event()
    await plugin.motd(event, "set", "a.test")
    assert await plugin.store.get_binding(plugin._scope(event)) == "a.test"
    for outsider in [
        Event(group="another-group"),
        Event(platform="another-bot"),
        Event(user="another-user"),
    ]:
        await plugin.motd(outsider, "set", "unauthorized.test")
        assert "无法核实" in outsider.messages[-1][1]
        if outsider.group != event.group or outsider.platform != event.platform:
            assert await plugin.store.get_binding(plugin._scope(outsider)) is None
        else:
            assert await plugin.store.get_binding(plugin._scope(outsider)) == "a.test"
    await plugin.motd(event, "identity")
    assert "qq-main|group-openid|user-openid" in event.messages[-1][1]
    assert not plugin._is_admin(event)
    await plugin.terminate()


async def test_paged_images_uploaded_before_replacing_file_and_cleaned(
    plugin_class, monkeypatch, tmp_path
):
    plugin = plugin_class(object(), {"history_enabled": False})
    pages = [b"page-one", b"page-two", b"page-three"]
    monkeypatch.setattr(
        sys.modules[plugin_class.__module__], "render_motd_cards", lambda *args: pages
    )

    async def query(address):
        return {"online": True, "address": address, "players_online": 100, "players_max": 200}

    plugin.api.query = query
    uploaded = []

    class UploadEvent(Event):
        async def send(self, result):
            if result[0] == "image":
                uploaded.append(Path(result[1]).read_bytes())
            await super().send(result)

    event = UploadEvent()
    await plugin.motd(event, "mc.example.com")
    assert uploaded == pages
    assert not list(tmp_path.glob("card-*.png"))
    await plugin.terminate()
