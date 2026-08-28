import asyncio
import socket
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, TCPConnector

from utils.speed import create_speed_test_session, _install_aiohttp_dns_error_filter


class MemoryArtifactWriter:
    def __init__(self, *_args, **_kwargs):
        self.count = 0

    def write(self, _record):
        self.count += 1

    def close(self):
        pass


@asynccontextmanager
async def speed_session(_concurrency):
    yield object()


def speed_test_config():
    return SimpleNamespace(
        open_ipv6=False,
        speed_test_mode="full",
        open_full_speed_test=True,
        open_filter_resolution=False,
        open_stream_screenshot=False,
        performance_settings=SimpleNamespace(
            speed_test_concurrency=1,
            probe_concurrency=1,
        ),
        speed_test_target=1,
        speed_test_timeout=1,
    )


class FailingResolver:
    async def resolve(self, host, port=0, family=socket.AF_INET):
        await asyncio.sleep(0.02)
        raise socket.gaierror(-3, "Try again")

    async def close(self):
        pass


class SpeedDnsErrorLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_speed_session_installs_dns_error_filter(self):
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(None)

        async with create_speed_test_session(1):
            handler = loop.get_exception_handler()

        self.assertTrue(
            getattr(handler, "_filters_aiohttp_dns_shield_errors", False)
        )

    async def test_cancelled_aiohttp_dns_failure_is_filtered(self):
        loop = asyncio.get_running_loop()
        contexts = []
        loop.set_exception_handler(lambda _loop, context: contexts.append(context))
        _install_aiohttp_dns_error_filter()

        async with ClientSession(
            connector=TCPConnector(resolver=FailingResolver())
        ) as session:
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(
                    session.get("http://unresolvable.invalid"),
                    timeout=0.001,
                )
            await asyncio.sleep(0.05)

        self.assertEqual(contexts, [])

    async def test_unrelated_asyncio_exception_uses_previous_handler(self):
        loop = asyncio.get_running_loop()
        contexts = []
        loop.set_exception_handler(lambda _loop, context: contexts.append(context))
        _install_aiohttp_dns_error_filter()
        context = {
            "message": "unexpected background failure",
            "exception": RuntimeError("boom"),
        }

        loop.call_exception_handler(context)

        self.assertEqual(contexts, [context])

    async def test_isolated_request_cancellation_does_not_cancel_batch(self):
        from utils.channel import test_speed

        channel_data = {
            "Test": {
                "Channel": [{
                    "url": "http://unresolvable.invalid/live",
                    "host": "unresolvable.invalid",
                    "resolution": None,
                    "ipv_type": "ipv4",
                    "origin": "subscribe",
                }]
            }
        }
        with (
            patch("utils.channel.config", speed_test_config()),
            patch("utils.channel.ArtifactWriter", MemoryArtifactWriter),
            patch("utils.channel.create_speed_test_session", speed_session),
            patch("utils.channel.get_speed", AsyncMock(side_effect=asyncio.CancelledError)),
            patch("utils.channel.mark_url_bad"),
            patch("utils.channel.mark_url_good"),
        ):
            result = await test_speed(channel_data)

        item = result["Test"]["Channel"][0]
        self.assertEqual(item["test_status"], "request_error")
        self.assertEqual(item["error_type"], "CancelledError")

    async def test_real_batch_cancellation_still_propagates(self):
        from utils.channel import test_speed

        started = asyncio.Event()

        async def wait_for_cancellation(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        channel_data = {
            "Test": {
                "Channel": [{
                    "url": "http://example.invalid/live",
                    "host": "example.invalid",
                    "resolution": None,
                    "ipv_type": "ipv4",
                    "origin": "subscribe",
                }]
            }
        }

        with (
            patch("utils.channel.config", speed_test_config()),
            patch("utils.channel.ArtifactWriter", MemoryArtifactWriter),
            patch("utils.channel.create_speed_test_session", speed_session),
            patch("utils.channel.get_speed", wait_for_cancellation),
        ):
            task = asyncio.create_task(test_speed(channel_data))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
