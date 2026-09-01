import asyncio
import unittest

from main import UpdateSource


class UpdateSourcePauseResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.source = UpdateSource()
        self.source._initialize_pause_control()

    async def test_pause_blocks_until_resume(self):
        self.source.pause()
        waiter = asyncio.create_task(self.source.wait_if_paused())

        await asyncio.sleep(0.01)
        self.assertFalse(waiter.done())
        self.assertTrue(self.source.is_paused)

        self.source.resume()
        await asyncio.wait_for(waiter, timeout=0.5)
        self.assertFalse(self.source.is_paused)

    async def test_resume_before_checkpoint_does_not_block(self):
        self.source.pause()
        self.source.resume()

        await asyncio.wait_for(self.source.wait_if_paused(), timeout=0.5)

    async def test_waiting_task_can_be_cancelled_while_paused(self):
        self.source.pause()
        waiter = asyncio.create_task(self.source.wait_if_paused())
        await asyncio.sleep(0.01)

        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
