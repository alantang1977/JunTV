import copy
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import PropertyMock, patch

from utils.channel import (
    check_channel_need_frozen,
    get_speed_test_status,
    is_valid_speed_result,
)
from utils.channel_quality import is_channel_result_valid
from utils.channel_repository import (
    _is_valid,
    list_candidate_history,
    list_candidate_measurements,
    list_candidate_pool,
    list_output_snapshot,
    list_channel_results,
    reset_channel_selection,
    set_channel_selection,
    start_run,
    sync_channel_snapshot,
)
from utils.config import config
from utils.identity import stable_channel_id, stable_result_id
from utils.speed import get_sort_result


def make_result(*, speed=5.0, delay=10.0, resolution="1920x1080", origin="subscribe"):
    url = "https://example.com/live.m3u8"
    return {
        "id": stable_result_id(url),
        "url": url,
        "host": "example.com",
        "origin": origin,
        "ipv_type": "ipv4",
        "speed": speed,
        "delay": delay,
        "resolution": resolution,
    }


class ChannelQualityTests(unittest.TestCase):
    def quality_config(self, *, supply=False):
        stack = ExitStack()
        values = {
            "open_supply": supply,
            "open_filter_speed": True,
            "min_speed": 1.0,
            "resolution_speed_map": {},
            "open_filter_resolution": True,
            "min_resolution_value": 1280 * 720,
            "max_resolution_value": 1920 * 1080,
        }
        for name, value in values.items():
            stack.enter_context(
                patch.object(type(config), name, new=PropertyMock(return_value=value))
            )
        stack.enter_context(patch("utils.speed.resolution_speed_map", {}))
        return stack

    def sort_results(self, *items, supply=False):
        return get_sort_result(
            [copy.deepcopy(item) for item in items],
            supply=supply,
            filter_speed=True,
            min_speed=1.0,
            filter_resolution=True,
            min_resolution=1280 * 720,
            max_resolution=1920 * 1080,
        )

    def test_maximum_resolution_is_consistent_across_pipeline_and_repository(self):
        result = make_result(resolution="3840x2160")

        with self.quality_config():
            self.assertFalse(is_valid_speed_result(result))
            self.assertFalse(_is_valid(result))
            self.assertTrue(check_channel_need_frozen(result))
            self.assertEqual(get_speed_test_status(result, False), "filtered_resolution")
            self.assertEqual(self.sort_results(result), [])

    def test_low_speed_is_not_reported_as_valid_in_repository(self):
        result = make_result(speed=0.25)

        with self.quality_config(), tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "channels.db")
            data = {"News": {"Demo": [result]}}
            sync_channel_snapshot(db_path, data, tested_data=data)
            rows = list_channel_results(
                db_path,
                stable_channel_id("News", "Demo"),
            )

            self.assertFalse(is_valid_speed_result(result))
            self.assertEqual(self.sort_results(result), [])
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["valid"])

    def test_repository_keeps_unmeasured_candidates_separate_from_tested_results(self):
        tested = make_result()
        pending = make_result()
        pending["url"] = "https://example.net/live.m3u8"
        pending["id"] = stable_result_id(pending["url"])
        data = {"News": {"Demo": [tested, pending]}}

        with self.quality_config(), tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "channels.db")
            sync_channel_snapshot(
                db_path,
                data,
                tested_data={"News": {"Demo": [tested]}},
                selected_data={"News": {"Demo": [tested]}},
            )
            channel_key = stable_channel_id("News", "Demo")
            rows = list_channel_results(db_path, channel_key)

            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(row["test_state"] == "untested" for row in rows), 1)

    def test_manual_output_selection_survives_next_snapshot(self):
        first = make_result()
        second = make_result()
        second["url"] = "https://example.net/live.m3u8"
        second["id"] = stable_result_id(second["url"])
        third = make_result()
        third["url"] = "https://example.org/live.m3u8"
        third["id"] = stable_result_id(third["url"])
        data = {"News": {"Demo": [first, second, third]}}

        with self.quality_config(), tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "channels.db")
            run_id = start_run(db_path)
            sync_channel_snapshot(
                db_path,
                data,
                tested_data=data,
                selected_data={"News": {"Demo": [first, second]}},
                run_id=run_id,
            )
            channel_key = stable_channel_id("News", "Demo")
            set_channel_selection(db_path, channel_key, [second])
            sync_channel_snapshot(
                db_path,
                data,
                tested_data=data,
                selected_data={"News": {"Demo": [first, second, third]}},
                run_id=run_id,
            )
            rows = list_channel_results(db_path, channel_key)
            selected = [row for row in rows if row.get("selected_rank") is not None]

            self.assertEqual([row["url"] for row in selected], [second["url"]])
            self.assertEqual([row["result_key"] for row in list_output_snapshot(db_path, run_id)], [second["id"]])

    def test_candidate_history_and_measurement_snapshots_are_recorded(self):
        result = make_result()
        data = {"News": {"Demo": [result]}}

        with self.quality_config(), tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "channels.db")
            run_id = start_run(db_path)
            sync_channel_snapshot(db_path, data, tested_data=data, run_id=run_id)
            history = list_candidate_history(db_path)
            measurements = list_candidate_measurements(db_path)
            pool = list_candidate_pool(db_path)

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["run_id"], run_id)
            self.assertEqual(len(measurements), 1)
            self.assertEqual(measurements[0]["test_status"], "valid")
            self.assertEqual(pool[0]["seen_count"], 1)

            next_run = start_run(db_path)
            sync_channel_snapshot(db_path, data, tested_data=data, run_id=next_run)
            refreshed_pool = list_candidate_pool(db_path)
            self.assertEqual(refreshed_pool[0]["seen_count"], 2)
            self.assertEqual(refreshed_pool[0]["first_seen_at"], pool[0]["first_seen_at"])

    def test_reset_selection_recomputes_automatic_output(self):
        slow = make_result(speed=2.0)
        fast = make_result(speed=8.0)
        fast["url"] = "https://example.net/live.m3u8"
        fast["id"] = stable_result_id(fast["url"])
        data = {"News": {"Demo": [slow, fast]}}

        with self.quality_config(), tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "channels.db")
            with patch.object(type(config), "output_urls_limit", new=PropertyMock(return_value=1)):
                sync_channel_snapshot(db_path, data, tested_data=data)
                channel_key = stable_channel_id("News", "Demo")
                set_channel_selection(db_path, channel_key, [slow])
                reset_channel_selection(db_path, channel_key)
                rows = list_channel_results(db_path, channel_key)

            selected = [row for row in rows if row.get("selected_rank") is not None]
            self.assertEqual([row["url"] for row in selected], [fast["url"]])

    def test_missing_resolution_is_allowed_when_measurement_is_reachable(self):
        result = make_result(resolution=None)

        with self.quality_config():
            self.assertTrue(is_valid_speed_result(result))
            self.assertTrue(_is_valid(result))
            self.assertEqual(self.sort_results(result), [result])

    def test_supply_mode_bypasses_thresholds_but_not_reachability(self):
        fallback = make_result(speed=0.25, resolution="3840x2160")
        unreachable = make_result(speed=0, resolution="3840x2160")

        with self.quality_config(supply=True):
            self.assertTrue(is_valid_speed_result(fallback))
            self.assertTrue(_is_valid(fallback))
            self.assertFalse(check_channel_need_frozen(fallback))
            self.assertEqual(self.sort_results(fallback, supply=True), [fallback])

            self.assertFalse(is_valid_speed_result(unreachable))
            self.assertFalse(_is_valid(unreachable))
            self.assertTrue(check_channel_need_frozen(unreachable))
            self.assertEqual(self.sort_results(unreachable, supply=True), [])

    def test_retained_origins_remain_valid_without_measurements(self):
        retained = make_result(
            speed=None,
            delay=None,
            resolution=None,
            origin="whitelist",
        )

        with self.quality_config():
            self.assertFalse(is_channel_result_valid(retained))
            self.assertTrue(is_channel_result_valid(retained, retain_special=True))
            self.assertTrue(_is_valid(retained))


if __name__ == "__main__":
    unittest.main()
