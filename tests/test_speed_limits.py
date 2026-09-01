import asyncio
import unittest
from unittest.mock import patch

from utils.speed import get_result


class SpeedDownloadLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bytes_sent = 0
        self.server = await asyncio.start_server(self._handle_request, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle_request(self, reader, writer):
        try:
            request_line = await reader.readline()
            while await reader.readline() not in {b"\r\n", b"\n", b""}:
                pass
            if request_line.startswith(b"HEAD "):
                writer.close()
                await writer.wait_closed()
                return
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/octet-stream\r\n"
                b"Connection: close\r\n\r\n"
            )
            chunk = b"x" * 65536
            while True:
                writer.write(chunk)
                await writer.drain()
                self.bytes_sent += len(chunk)
                await asyncio.sleep(0.001)
        except (ConnectionError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def test_head_failure_fallback_stops_at_byte_limit(self):
        url = f"http://127.0.0.1:{self.port}/live"
        with (
            patch("utils.speed.stream_sample_max_bytes", 128 * 1024),
            patch("utils.speed.stream_sample_max_seconds", 1.0),
        ):
            result = await asyncio.wait_for(
                get_result(url, filter_resolution=False, timeout=5),
                timeout=2,
            )

        self.assertGreater(result["speed"], 0)
        self.assertGreaterEqual(self.bytes_sent, 128 * 1024)
        self.assertLess(self.bytes_sent, 512 * 1024)


if __name__ == "__main__":
    unittest.main()
