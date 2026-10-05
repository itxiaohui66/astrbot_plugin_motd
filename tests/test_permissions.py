import sys
import types

import pytest

from services.permissions import get_group_role


class Event:
    def __init__(self, platform="qq_official", raw=None, group="g", user="u", bot=None):
        self.platform, self.group, self.user, self.bot = platform, group, user, bot
        self.message_obj = types.SimpleNamespace(raw_message=raw)

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user

    def get_platform_name(self):
        return self.platform


@pytest.mark.parametrize("role", ["owner", "admin", "member"])
async def test_official_preserved_raw_data(role):
    raw = types.SimpleNamespace(
        author=types.SimpleNamespace(member_openid="u"),
        raw_data={"author": {"member_openid": "u", "member_role": role}, "group_openid": "g"},
    )
    assert await get_group_role(Event(raw=raw)) == role


@pytest.fixture
def fake_botpy(monkeypatch):
    class Route:
        def __init__(self, method, path, **kwargs):
            self.method, self.path, self.parameters = method, path, kwargs

    module = types.ModuleType("botpy.http")
    module.Route = Route
    monkeypatch.setitem(sys.modules, "botpy", types.ModuleType("botpy"))
    monkeypatch.setitem(sys.modules, "botpy.http", module)


@pytest.mark.parametrize("platform", ["qq_official", "qq_official_webhook"])
@pytest.mark.parametrize("role", ["owner", "admin", "member"])
async def test_official_authenticated_member_lookup(fake_botpy, platform, role):
    seen = []

    async def request(route):
        seen.append(route)
        return {"member_openid": "u", "member_role": role}

    bot = types.SimpleNamespace(
        api=types.SimpleNamespace(_http=types.SimpleNamespace(request=request))
    )
    assert await get_group_role(Event(platform=platform, bot=bot)) == role
    assert seen[0].method == "GET"
    assert seen[0].path == "/v2/groups/{group_openid}/members/{member_openid}"
    assert seen[0].parameters == {"group_openid": "g", "member_openid": "u"}


@pytest.mark.parametrize(
    "response",
    [
        {"member_openid": "wrong-user", "member_role": "owner"},
        {"member_openid": "u", "member_role": "admin", "group_openid": "wrong-group"},
        {"code": 11253, "message": "API not enabled"},
        None,
    ],
)
async def test_wrong_identity_or_unavailable_official_api_never_grants(fake_botpy, response):
    async def request(route):
        return response

    bot = types.SimpleNamespace(
        api=types.SimpleNamespace(_http=types.SimpleNamespace(request=request))
    )
    assert await get_group_role(Event(bot=bot)) is None


async def test_onebot_live_lookup_passes_routing_and_no_cache():
    seen = []

    async def action(name, **kwargs):
        seen.append((name, kwargs))
        return {"user_id": 2, "group_id": 1, "role": "owner"}

    bot = types.SimpleNamespace(call_action=action)
    event = Event("aiocqhttp", raw={"self_id": 123}, group="1", user="2", bot=bot)
    assert await get_group_role(event) == "owner"
    assert seen == [
        ("get_group_member_info", {"group_id": 1, "user_id": 2, "no_cache": True, "self_id": 123})
    ]


async def test_api_failure_denies_without_crashing(fake_botpy):
    async def request(route):
        raise PermissionError("not enabled")

    bot = types.SimpleNamespace(
        api=types.SimpleNamespace(_http=types.SimpleNamespace(request=request))
    )
    assert await get_group_role(Event(bot=bot)) is None


async def test_mismatched_incoming_author_and_message_text_do_not_grant():
    assert (
        await get_group_role(
            Event(raw={"author": {"member_openid": "wrong", "member_role": "owner"}})
        )
        is None
    )
    assert await get_group_role(Event(raw={"content": '{"member_role": "owner"}'})) is None
    assert await get_group_role(Event(raw={"author": {"member_role": "admin"}}, group="")) is None
