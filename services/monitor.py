"""Shared query cache and bounded background sampling, with clean cancellation."""

import asyncio
import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)


class PlayerMonitor:
    def __init__(self, api, store, interval=300, retention_hours=48, enabled=True, addresses=()):
        self.api, self.store = api, store
        self.interval, self.retention_hours, self.enabled = interval, retention_hours, enabled
        self.addresses = set(addresses)
        self._semaphore = asyncio.Semaphore(4)
        self._cache = OrderedDict()
        self._pending = {}
        self._task = None

    async def query(self, address: str):
        cached = self._cache.get(address)
        if cached and time.monotonic() - cached[0] < 15:
            return cached[1]
        # Concurrent requests for the same server share one query and one sample.
        if address not in self._pending:
            self._pending[address] = asyncio.create_task(self._fetch(address))
        task = self._pending[address]
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._pending.get(address) is task:
                self._pending.pop(address, None)

    async def _fetch(self, address):
        async with self._semaphore:
            try:
                data = await self.api.query(address)
            except Exception:
                logger.exception("MOTD 查询异常: %s", address)
                data = None
            await self.store.record(address, data)
            self._cache[address] = (time.monotonic(), data)
            self._cache.move_to_end(address)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            return data

    def start(self):
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="motd-player-monitor")

    async def sample_once(self):
        addresses = set(await self.store.monitored_addresses()) | self.addresses
        results = await asyncio.gather(*(self.query(a) for a in addresses), return_exceptions=True)
        for address, result in zip(addresses, results):
            if isinstance(result, Exception):
                logger.error("MOTD 采样失败 %s: %s", address, result)
        await self.store.prune(self.retention_hours)

    async def _loop(self):
        while True:
            started = time.monotonic()
            try:
                await self.sample_once()
            except Exception:
                logger.exception("MOTD 后台采样异常")
            await asyncio.sleep(max(1, self.interval - (time.monotonic() - started)))

    async def close(self):
        tasks = list(self._pending.values())
        if self._task:
            tasks.append(self._task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._task = None
