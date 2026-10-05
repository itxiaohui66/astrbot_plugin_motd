import asyncio

from services.monitor import PlayerMonitor
from services.storage import MotdStore


async def test_binding_isolation_persistence_and_history_window(tmp_path):
    path = tmp_path / "motd.db"
    store = MotdStore(path)
    await store.initialize()
    await store.set_binding("bot-a:group-1", "one.test", "admin")
    await store.set_binding("bot-b:group-1", "two.test", "admin")
    await store.record(
        "one.test", {"online": True, "players_online": 0, "source": "direct"}, 100000
    )
    await store.record("one.test", {"online": False}, 100300)
    await store.record("one.test", None, 100600)
    await store.record("one.test", {"online": True, "players_online": 9}, 1)
    reloaded = MotdStore(path)
    await reloaded.initialize()
    assert await reloaded.get_binding("bot-a:group-1") == "one.test"
    assert await reloaded.get_binding("bot-b:group-1") == "two.test"
    assert [(p["online"], p["players"]) for p in await reloaded.history("one.test", 101000)] == [
        (1, 0),
        (0, None),
        (None, None),
    ]
    await reloaded.clear_binding("bot-a:group-1")
    assert await reloaded.get_binding("bot-a:group-1") is None
    assert await reloaded.get_binding("bot-b:group-1") == "two.test"


async def test_tracking_expiry_bindings_and_prune(tmp_path, monkeypatch):
    monkeypatch.setattr("services.storage.time.time", lambda: 200000)
    store = MotdStore(tmp_path / "motd.db")
    await store.initialize()
    await store.track("temporary.test")
    await store.set_binding("a", "permanent.test", "admin")
    assert set(await store.monitored_addresses(200001)) == {"temporary.test", "permanent.test"}
    assert await store.monitored_addresses(286401) == ["permanent.test"]
    await store.record("temporary.test", {"online": True, "players_online": 4}, 1)
    await store.record("temporary.test", {"online": True, "players_online": 5}, 199999)
    await store.prune(48)
    assert len(await store.history("temporary.test", 200000)) == 1


async def test_concurrent_queries_and_background_sampling(tmp_path):
    class API:
        calls = 0

        async def query(self, address):
            self.calls += 1
            await asyncio.sleep(0.02)
            return {"online": True, "players_online": 3, "source": "direct"}

    store = MotdStore(tmp_path / "motd.db")
    await store.initialize()
    await store.set_binding("group-a", "same.test", "a")
    await store.set_binding("group-b", "same.test", "a")
    api = API()
    monitor = PlayerMonitor(api, store, addresses=["extra.test"])
    result = await asyncio.gather(*(monitor.query("same.test") for _ in range(5)))
    assert api.calls == 1 and all(p["players_online"] == 3 for p in result)
    await monitor.sample_once()
    assert api.calls == 2  # same.test cached, extra.test polled
    assert len(await store.history("extra.test")) == 1
    monitor.start()
    task = monitor._task
    await monitor.close()
    assert task.done() and not monitor._pending


async def test_api_exception_records_unknown_not_zero(tmp_path):
    class API:
        async def query(self, address):
            raise OSError("network down")

    store = MotdStore(tmp_path / "motd.db")
    await store.initialize()
    monitor = PlayerMonitor(API(), store)
    assert await monitor.query("a.test") is None
    point = (await store.history("a.test"))[0]
    assert point["players"] is None and point["online"] is None


async def test_cancellation_joins_inflight_queries(tmp_path):
    started = asyncio.Event()

    class API:
        async def query(self, address):
            started.set()
            await asyncio.Event().wait()

    store = MotdStore(tmp_path / "motd.db")
    await store.initialize()
    monitor = PlayerMonitor(API(), store)
    request = asyncio.create_task(monitor.query("a.test"))
    await started.wait()
    await monitor.close()
    results = await asyncio.gather(request, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
