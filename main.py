import asyncio
import copy
import datetime
import gzip
import os
import pickle
import threading
from time import time
from typing import Callable, Optional, Any

import pytz

import utils.constants as constants
import utils.frozen as frozen
from updates.epg import get_epg
from updates.epg.tools import write_to_xml, compress_to_gz
from updates.subscribe import get_channels_by_subscribe_urls
from utils.aggregator import ResultAggregator
from utils.channel import get_channel_items, append_total_data, get_speed_test_status, test_speed
from utils.channel_repository import finish_run, prune_stream_screenshots, start_run
from utils.config import config
from utils.i18n import t
from utils.requests.async_tools import check_ipv6_support_async
from utils.reporting import Reporter
from utils.run_state import write_run_state
from utils.speed import clear_cache
from utils.tools import (
    process_nested_dict,
    format_interval,
    get_urls_from_file,
    get_version_info,
    get_urls_len,
    get_total_urls,
    get_public_url,
    parse_times,
    to_serializable,
    get_subscribe_entries,
    count_disabled_urls,
    get_resolution_value,
)
from utils.types import CategoryChannelData
from utils.whitelist import load_whitelist_maps
from utils.version_check import log_new_version_if_available, start_version_log_monitor

ProgressCallback = Callable[..., Any]


class UpdateSource:
    def __init__(self, reporter: Reporter | None = None):
        self.whitelist_maps = None
        self.blacklist = None

        self.update_progress: Optional[ProgressCallback] = None
        self.run_ui = False

        self.tasks: list[asyncio.Task] = []

        self.channel_items: CategoryChannelData = {}
        self.channel_names: list[str] = []

        self.subscribe_result = {}
        self.epg_result = {}

        self.channel_data: CategoryChannelData = {}

        self.total = 0
        self.start_time = None
        self.reporter = reporter
        self._owns_reporter = reporter is None
        self.run_metrics = {}
        self.source_metrics = {}
        self.run_outcome = None

        self.stop_event: Optional[asyncio.Event] = None
        self.ipv6_support = False
        self.now = None

        self.aggregator: Optional[ResultAggregator] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._resume_event: Optional[asyncio.Event] = None
        self._pause_requested = threading.Event()

    # ----------------------------
    # pause / resume control
    # ----------------------------
    def _initialize_pause_control(self):
        self._loop = asyncio.get_running_loop()
        self._resume_event = asyncio.Event()
        if not self._pause_requested.is_set():
            self._resume_event.set()

    async def wait_if_paused(self):
        """Cooperatively wait until a paused update is resumed."""
        if self._resume_event is None:
            self._initialize_pause_control()
        while self._pause_requested.is_set():
            self._resume_event.clear()
            await self._resume_event.wait()

    def _set_resume_event(self, resumed: bool):
        event = self._resume_event
        if event is None:
            return
        if resumed:
            event.set()
        else:
            event.clear()

    def pause(self):
        self._pause_requested.set()
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._set_resume_event, False)
        else:
            self._set_resume_event(False)

    def resume(self):
        self._pause_requested.clear()
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._set_resume_event, True)
        else:
            self._set_resume_event(True)

    @property
    def is_paused(self) -> bool:
        return self._pause_requested.is_set()

    # ----------------------------
    # IO: cache
    # ----------------------------
    def _load_cache(self) -> dict:
        if not (config.open_history and os.path.exists(constants.cache_path)):
            return {}
        try:
            with gzip.open(constants.cache_path, "rb") as f:
                return pickle.load(f) or {}
        except Exception:
            return {}

    def _save_cache(self, cache_result: dict):
        serializable = to_serializable(cache_result or {})
        cache_dir = os.path.dirname(constants.cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with gzip.open(constants.cache_path, "wb") as f:
            pickle.dump(serializable, f)

    # ----------------------------
    # stage 1: prepare
    # ----------------------------
    def _prepare_channel_data(self):
        self.run_metrics = {}
        self.run_outcome = None
        self.whitelist_maps = load_whitelist_maps(constants.whitelist_path)
        self.blacklist = get_urls_from_file(constants.blacklist_path, pattern_search=False)
        self.channel_items = get_channel_items(self.whitelist_maps, self.blacklist, reporter=self.reporter)
        self.channel_data = {}

        self.channel_names = [
            name for channel_obj in self.channel_items.values() for name in channel_obj.keys()
        ]
        self.source_metrics = {
            "template_channels": len(self.channel_names),
            "prepared_items": get_urls_len(self.channel_items),
            "subscription_urls": 0,
            "subscription_channels": 0,
            "subscription_items": 0,
            "aggregated_items": 0,
            "output_items": 0,
        }

        if config.open_history and os.path.exists(constants.frozen_path):
            frozen.load(constants.frozen_path)

    def _set_empty_outcome(self, reason: str, phase: str):
        if self.run_outcome:
            return
        metrics = dict(self.source_metrics)
        message_key = {
            "template_empty": "msg.empty_template_diagnostic",
            "no_source_configured": "msg.empty_no_source_configured",
            "sources_unavailable": "msg.empty_sources_unavailable",
            "no_matching_channels": "msg.empty_no_matching_channels",
            "all_results_filtered": "msg.empty_all_results_filtered",
        }.get(reason, "msg.empty_no_usable_data")
        message = t(message_key).format(
            channels=metrics.get("template_channels", 0),
            prepared=metrics.get("prepared_items", 0),
            subscriptions=metrics.get("subscription_urls", 0),
            discovered=metrics.get("subscription_items", 0),
            aggregated=metrics.get("aggregated_items", 0),
        )
        if reason == "template_empty":
            guidance = t("msg.empty_template_guidance").format(
                template_file=config.source_file,
                docs_url=t("msg.empty_data_docs_url"),
            )
        else:
            guidance = t("msg.empty_data_guidance").format(
                subscribe_file=constants.subscribe_path,
                local_file=constants.local_path,
                docs_url=t("msg.empty_data_docs_url"),
            )
        self.run_outcome = {
            "status": "empty",
            "reason": reason,
            "message": message,
            "guidance": guidance,
            **metrics,
        }
        self.reporter.warning(
            "run.empty_data",
            f"{message}\n{guidance}",
            phase=phase,
            outcome="empty",
            reason=reason,
            **metrics,
        )

    def _diagnose_aggregated_data(self):
        aggregated = get_urls_len(self.channel_data)
        self.source_metrics["aggregated_items"] = aggregated
        if aggregated:
            return
        prepared = self.source_metrics.get("prepared_items", 0)
        subscriptions = self.source_metrics.get("subscription_urls", 0)
        discovered = self.source_metrics.get("subscription_items", 0)
        if not prepared and not subscriptions:
            reason = "no_source_configured"
        elif subscriptions and not discovered and not prepared:
            reason = "sources_unavailable"
        elif discovered:
            reason = "no_matching_channels"
        else:
            reason = "no_usable_data"
        self._set_empty_outcome(reason, "aggregate")

    # ----------------------------
    # stage 2: fetch subscribe/epg (concurrent)
    # ----------------------------
    async def _fetch_subscribe(self, channel_names: list[str], epg_urls_out: set = None):
        whitelist_entries, default_entries = get_subscribe_entries(constants.subscribe_path)
        disabled_count = count_disabled_urls(constants.subscribe_path)

        seen = set()
        subscribe_entries = []
        for e in (whitelist_entries + default_entries):
            url = e['url'] if isinstance(e, dict) else e
            if url in seen:
                continue
            seen.add(url)
            subscribe_entries.append(e)

        self.reporter.info(
            "sources.subscribe.summary",
            t("msg.subscribe_urls_whitelist_total").format(
                default_count=len(default_entries),
                whitelist_count=len(whitelist_entries),
                disabled_count=disabled_count,
                total=len(subscribe_entries),
            ),
            phase="fetch",
            default_count=len(default_entries),
            whitelist_count=len(whitelist_entries),
            disabled_count=disabled_count,
            total=len(subscribe_entries),
        )
        self.source_metrics["subscription_urls"] = len(subscribe_entries)

        if not subscribe_entries:
            self.reporter.warning(
                "sources.subscribe.empty",
                t("msg.no_subscribe_urls").format(file=constants.subscribe_path),
                phase="fetch",
            )
            return {}

        whitelist_urls = [e['url'] for e in whitelist_entries]

        result = await get_channels_by_subscribe_urls(
            subscribe_entries,
            names=channel_names,
            whitelist=whitelist_urls,
            callback=self.update_progress,
            epg_urls_out=epg_urls_out,
            pause_wait=self.wait_if_paused,
            reporter=self.reporter,
        )
        self.source_metrics["subscription_channels"] = len(result)
        self.source_metrics["subscription_items"] = sum(len(items) for items in result.values())
        return result

    async def _fetch_epg(self, channel_names: list[str], extra_entries: list = None):
        return await get_epg(
            channel_names,
            callback=self.update_progress,
            extra_entries=extra_entries,
            pause_wait=self.wait_if_paused,
            reporter=self.reporter,
        )

    async def visit_page(self, channel_names: list[str] = None):
        """
        Visits subscribe and epg pages concurrently to fetch data.
        """
        channel_names = channel_names or []
        open_subscribe = config.open_method.get("subscribe")
        open_epg = config.open_method.get("epg")

        if open_subscribe and open_epg and config.open_subscribe_epg:
            discovered_epg_urls: set[str] = set()
            try:
                self.subscribe_result = await self._fetch_subscribe(channel_names, epg_urls_out=discovered_epg_urls)
            except Exception as e:
                self.reporter.error(
                    "sources.subscribe.failed",
                    t("log.source_process_failed").format(name=t("name.subscribe"), info=e),
                    phase="fetch",
                    error_type=type(e).__name__,
                )
                self.subscribe_result = {}
            try:
                self.epg_result = await self._fetch_epg(channel_names, extra_entries=sorted(discovered_epg_urls))
            except Exception as e:
                self.reporter.error(
                    "sources.epg.failed",
                    t("log.source_process_failed").format(name=t("name.epg"), info=e),
                    phase="fetch",
                    error_type=type(e).__name__,
                )
                self.epg_result = {}
            return

        cors: list[tuple[str, asyncio.Future]] = []
        if open_subscribe:
            cors.append(("subscribe_result", asyncio.create_task(self._fetch_subscribe(channel_names))))
        if open_epg:
            cors.append(("epg_result", asyncio.create_task(self._fetch_epg(channel_names))))

        if not cors:
            return

        results = await asyncio.gather(*(c for _, c in cors), return_exceptions=True)
        for (attr, _), res in zip(cors, results):
            if isinstance(res, Exception):
                self.reporter.error(
                    f"sources.{attr}.failed",
                    t("log.source_process_failed").format(name=attr, info=res),
                    phase="fetch",
                    error_type=type(res).__name__,
                )
                setattr(self, attr, {})
            else:
                setattr(self, attr, res)

    def _write_epg_files_if_needed(self):
        if not self.epg_result:
            return
        write_to_xml(self.epg_result, constants.epg_result_path)
        compress_to_gz(constants.epg_result_path, constants.epg_gz_result_path)

    @staticmethod
    def _count_exported_items(data: CategoryChannelData) -> int:
        """Count records that the configured playlist renderer will export."""
        ipv_type_prefer = list(config.ipv_type_prefer)
        if any(value == "auto" for value in ipv_type_prefer):
            ipv_type_prefer = ["ipv4", "ipv6"]
        total = 0
        unmatch_category = t("content.unmatch_channel")
        for category, channels in (data or {}).items():
            for items in channels.values():
                total += len(
                    get_total_urls(
                        items,
                        ipv_type_prefer,
                        config.origin_type_prefer,
                        apply_limit=category != unmatch_category,
                    )
                )
        return total

    # ----------------------------
    # stage 3: aggregator lifecycle
    # ----------------------------
    async def _start_aggregator(self, cache: dict):
        self.aggregator = ResultAggregator(
            base_data=self.channel_data,
            first_channel_name=self.channel_names[0] if self.channel_names else None,
            ipv6_support=self.ipv6_support,
            write_interval=10.0,
            flush_debounce=2.0,
            min_items_before_flush=max(25, config.output_urls_limit),
            result=cache,
            channel_catalog=self.channel_items,
            reporter=self.reporter,
        )
        await self.aggregator.start()

    async def _stop_aggregator(self, flush: bool = True):
        if self.aggregator:
            aggregator = self.aggregator
            try:
                await aggregator.stop(flush=flush)
                return aggregator.result
            finally:
                self.aggregator = None
        return {}

    # ----------------------------
    # stage 4: speed test
    # ----------------------------
    async def _run_speed_test(self) -> CategoryChannelData:
        """
        Run speed test on the channel data and return the test results.
        """
        test_data = {
            category: copy.deepcopy(items)
            for category, items in self.channel_data.items()
            if category != t("content.unmatch_channel")
        }
        urls_total = get_urls_len(test_data)

        process_nested_dict(
            test_data,
            seen=set(),
            filter_host=config.speed_test_filter_host,
            ipv6_support=self.ipv6_support,
        )
        self.total = get_urls_len(test_data)

        self.reporter.info(
            "speed_test.planned",
            t("msg.total_urls_need_test_speed").format(total=urls_total, speed_total=self.total),
            phase="speed_test",
            discovered=urls_total,
            total=self.total,
        )

        if self.total <= 0:
            self.aggregator.is_last = True
            return {}
        if self.update_progress:
            self.update_progress(
                t("msg.progress_speed_test").format(total=urls_total, speed_total=self.total),
                0,
            )

        self.start_time = time()
        completed_items = 0
        invalid_items = 0
        status_counts = {}
        valid_counts = {}
        playable_results = {}

        def channel_metadata(cate, name):
            items = self.aggregator.test_results[cate][name]
            speeds = [item.get("speed") for item in items if (item.get("speed") or 0) > 0]
            resolutions = [item.get("resolution") for item in items if item.get("resolution")]
            playable_items = playable_results.get((cate, name), [])
            best_item = max(playable_items, key=lambda item: item.get("speed") or 0, default=None)
            logo = next((item.get("tvg_logo") for item in items if item.get("tvg_logo")), None)
            if not logo:
                logo = next(
                    (item.get("tvg_logo") for item in self.channel_data.get(cate, {}).get(name, []) if item.get("tvg_logo")),
                    None,
                )
            return {
                "total_results": len(items),
                "best_speed": max(speeds, default=None),
                "max_resolution": max(resolutions, key=get_resolution_value, default=None),
                "best_url": best_item.get("url") if best_item else None,
                "playable_results": playable_items,
                "logo": logo,
            }

        def handle_task_complete(cate, name, item, is_channel_last=False, is_last=False, is_valid=True):
            nonlocal completed_items, invalid_items
            self.aggregator.add_item(cate, name, item, is_channel_last, is_last, is_valid)
            completed_items += 1
            key = (cate, name)
            if is_valid:
                valid_counts[key] = valid_counts.get(key, 0) + 1
                playable_results.setdefault(key, []).append({
                    "url": item.get("url"),
                    "speed": item.get("speed"),
                    "resolution": item.get("resolution"),
                })
            else:
                invalid_items += 1
            test_status = get_speed_test_status(item, is_valid)
            status_counts[test_status] = status_counts.get(test_status, 0) + 1
            if self.update_progress:
                self.update_progress(
                    name,
                    int(completed_items / self.total * 100) if self.total else 0,
                    url={
                        "category": cate,
                        "channel": name,
                        "status": "completed" if is_channel_last else "testing",
                        "valid_count": valid_counts.get(key, 0),
                        "updated_at": time(),
                        **channel_metadata(cate, name),
                    },
                )
            valid_total = sum(valid_counts.values())
            channel_suffix = f"  {name}" if name else ""
            self.reporter.update_progress(
                "speed_test",
                completed=completed_items,
                status=t("log.speed_progress_status").format(
                    valid=valid_total,
                    invalid=invalid_items,
                    channel=channel_suffix,
                ),
                valid=valid_total,
                invalid=invalid_items,
                channel=name,
                category=cate,
            )

        self.reporter.start_progress(
            "speed_test",
            t("pbar.speed_test"),
            self.total,
            phase="speed_test",
        )
        try:
            result = await test_speed(
                test_data,
                ipv6=self.ipv6_support,
                pause_wait=self.wait_if_paused,
                on_task_complete=handle_task_complete,
                reporter=self.reporter,
            )
            self.aggregator.is_last = True
            return result
        finally:
            self.run_metrics = {
                "channels": len(self.channel_names),
                "planned": self.total,
                "completed": completed_items,
                "valid": sum(valid_counts.values()),
                "invalid": invalid_items,
                "skipped": max(0, self.total - completed_items),
                "status_counts": dict(status_counts),
            }
            self.reporter.finish_progress(
                "speed_test",
                status=t("log.speed_progress_status").format(
                    valid=sum(valid_counts.values()),
                    invalid=invalid_items,
                    channel="",
                ),
                valid=sum(valid_counts.values()),
                invalid=invalid_items,
            )

    # ----------------------------
    # stage 5: ui final notify
    # ----------------------------
    def _notify_ui_finished(self, main_start_time: float):
        if not self.run_ui:
            return

        open_service = config.open_service
        service_tip = t("msg.service_tip") if open_service else ""

        if self.run_outcome:
            tip = t("msg.update_completed_empty")
        else:
            tip = (
                t("msg.service_run_success").format(service_tip=service_tip)
                if open_service and config.open_update is False
                else t("msg.update_completed").format(
                    time=format_interval(time() - main_start_time),
                    service_tip=service_tip,
                )
            )
        metadata = {
            **(self.run_outcome or {"status": "success"}),
            "service_url": f"{get_public_url()}" if open_service else None,
        }

        if self.update_progress:
            self.update_progress(
                tip,
                100,
                finished=True,
                url=metadata,
                now=self.now,
            )

    # ----------------------------
    # main flow
    # ----------------------------
    async def main(self):
        run_id = start_run(constants.channel_results_path)
        self.reporter.bind_run(run_id)
        write_run_state("running", run_id=run_id, started_at=time())
        run_status = "failed"
        run_error = None
        try:
            main_start_time = time()
            performance = config.performance_settings
            self.reporter.info(
                "run.started",
                t("msg.performance_settings").format(
                    mode=performance.requested_mode,
                    resolved=performance.resolved_mode,
                    cpu=performance.cpu_count,
                    memory=performance.memory_gb,
                    speed=performance.speed_test_concurrency,
                    probe=performance.probe_concurrency,
                    fetch=performance.fetch_workers,
                ),
                phase="prepare",
                performance_mode=performance.requested_mode,
                resolved_mode=performance.resolved_mode,
                cpu=performance.cpu_count,
                memory_gb=performance.memory_gb,
                speed_concurrency=performance.speed_test_concurrency,
                probe_concurrency=performance.probe_concurrency,
                fetch_workers=performance.fetch_workers,
            )

            self._prepare_channel_data()
            await self.wait_if_paused()

            if not self.channel_names:
                self._set_empty_outcome("template_empty", "prepare")
                self.reporter.warning(
                    "run.finished",
                    t("msg.update_completed_empty"),
                    phase="complete",
                    status="success",
                    outcome="empty",
                    reason="template_empty",
                )
                self._notify_ui_finished(main_start_time)
                run_status = "success"
                return

            await self.visit_page(self.channel_names)
            await self.wait_if_paused()
            self.tasks = []
            self._write_epg_files_if_needed()

            append_total_data(
                self.channel_items.items(),
                self.channel_data,
                self.subscribe_result,
                self.whitelist_maps,
                self.blacklist,
                reporter=self.reporter,
            )
            self._diagnose_aggregated_data()

            cache = self._load_cache()

            await self._start_aggregator(cache)
            try:
                if config.speed_test_mode != "manual":
                    clear_cache()
                    await self._run_speed_test()
                else:
                    # Manual mode only refreshes the candidate pool. Do not
                    # mark candidates as measured merely because they were
                    # collected; GUI retest actions create measurements.
                    self.aggregator.test_results = {}
                    self.aggregator.result = {}
                    self.aggregator.is_last = True
                    completed_channels = 0
                    channel_total = sum(len(channels) for channels in self.channel_data.values())
                    for category, channels in self.channel_data.items():
                        for name, items in channels.items():
                            await self.wait_if_paused()
                            completed_channels += 1
                            if self.update_progress:
                                self.update_progress(
                                    name,
                                    int(completed_channels / channel_total * 100) if channel_total else 100,
                                    url={
                                        "category": category,
                                        "channel": name,
                                        "status": "completed",
                                        "valid_count": len(items),
                                        "total_results": len(items),
                                        "best_speed": max(
                                            (item.get("speed") for item in items if (item.get("speed") or 0) > 0),
                                            default=None,
                                        ),
                                        "max_resolution": max(
                                            (item.get("resolution") for item in items if item.get("resolution")),
                                            key=get_resolution_value,
                                            default=None,
                                        ),
                                        "best_url": next(
                                            (item.get("url") for item in items if item.get("url")),
                                            None,
                                        ),
                                        "playable_results": [
                                            {
                                                "url": item.get("url"),
                                                "speed": item.get("speed"),
                                                "resolution": item.get("resolution"),
                                            }
                                            for item in items if item.get("url")
                                        ],
                                        "updated_at": time(),
                                        "logo": next(
                                            (item.get("tvg_logo") for item in items if item.get("tvg_logo")),
                                            None,
                                        ),
                                    },
                                )

            except asyncio.CancelledError:
                await self._stop_aggregator(flush=False)
                raise
            except Exception:
                await self._stop_aggregator(flush=False)
                raise
            else:
                final_result = await self._stop_aggregator(flush=True)
                self.source_metrics["output_items"] = self._count_exported_items(final_result)
                try:
                    await asyncio.to_thread(
                        prune_stream_screenshots,
                        constants.channel_results_path,
                        constants.screenshot_dir,
                    )
                except Exception as exc:
                    self.reporter.warning(
                        "screenshots.cleanup_failed",
                        t("msg.screenshot_cleanup_failed").format(info=exc),
                        phase="output",
                        error_type=type(exc).__name__,
                    )
                if (
                    not self.run_outcome
                    and self.source_metrics["aggregated_items"] > 0
                    and self.source_metrics["output_items"] <= 0
                ):
                    self._set_empty_outcome("all_results_filtered", "complete")
                if config.open_history:
                    self._save_cache(final_result)
                    frozen.save(constants.frozen_path)

            completed_message = (
                t("msg.update_completed_empty")
                if self.run_outcome
                else t("msg.update_completed").format(
                    time=format_interval(time() - main_start_time),
                    service_tip="",
                )
            )
            self.reporter.stop_progress()
            if config.speed_test_mode != "manual":
                summary_metrics = [
                    (t("summary.channels"), self.run_metrics.get("channels", len(self.channel_names))),
                    (t("summary.planned"), self.run_metrics.get("planned", 0)),
                    (t("summary.completed"), self.run_metrics.get("completed", 0)),
                    (t("summary.valid"), self.run_metrics.get("valid", 0)),
                    (t("summary.invalid"), self.run_metrics.get("invalid", 0)),
                    (
                        t("summary.timeouts"),
                        self.run_metrics.get("status_counts", {}).get("timeout", 0),
                    ),
                    (
                        t("summary.filtered"),
                        sum(
                            self.run_metrics.get("status_counts", {}).get(key, 0)
                            for key in ("filtered_speed", "filtered_resolution")
                        ),
                    ),
                    (t("summary.skipped"), self.run_metrics.get("skipped", 0)),
                    (t("summary.duration"), format_interval(time() - main_start_time)),
                ]
            else:
                summary_metrics = [
                    (t("summary.channels"), len(self.channel_names)),
                    (t("summary.retained"), get_urls_len(self.channel_data)),
                    (t("summary.speed_test"), t("summary.disabled")),
                    (t("summary.duration"), format_interval(time() - main_start_time)),
                ]
            self.reporter.summary(
                t("summary.title"),
                summary_metrics,
                outputs=[
                    (t("summary.result_file"), config.final_file),
                    (t("summary.speed_log"), constants.speed_test_log_path),
                    (t("summary.statistic_log"), constants.statistic_log_path),
                ],
            )
            finish_data = {
                "status": "success",
                "outcome": "empty" if self.run_outcome else "success",
                "duration_seconds": round(time() - main_start_time, 3),
                "channels": len(self.channel_names),
            }
            if self.run_outcome:
                finish_data["reason"] = self.run_outcome["reason"]
                self.reporter.warning("run.finished", completed_message, phase="complete", **finish_data)
            else:
                self.reporter.info("run.finished", completed_message, phase="complete", **finish_data)
            self._notify_ui_finished(main_start_time)
            run_status = "success"

        except asyncio.exceptions.CancelledError:
            run_status = "cancelled"
            self.reporter.stop_progress()
            self.reporter.warning("run.cancelled", t("msg.update_cancelled"), phase="complete")
        except Exception as exc:
            run_error = str(exc)
            self.reporter.stop_progress()
            self.reporter.error(
                "run.failed",
                f"{t('name.error')}: {exc}",
                phase="complete",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            state = "completed_empty" if run_status == "success" and self.run_outcome else {
                "success": "completed",
                "cancelled": "cancelled",
                "failed": "failed",
            }.get(run_status, "failed")
            state_data = {"run_id": run_id}
            if self.run_outcome:
                state_data["reason"] = self.run_outcome.get("reason")
                state_data["message"] = self.run_outcome.get("message")
            write_run_state(state, **state_data)
            finish_run(constants.channel_results_path, run_id, run_status, run_error)
            self.reporter.bind_run(None)

    # ----------------------------
    # lifecycle control
    # ----------------------------
    async def run_once(self, callback=None, event_callback=None):
        def default_callback(*args, **kwargs):
            pass

        self.update_progress = callback or default_callback
        self.run_ui = bool(callback)
        self._initialize_pause_control()
        if self.reporter is None:
            self.reporter = Reporter(
                event_callback=event_callback,
                enable_console=event_callback is None,
            )

        try:
            if not config.open_update:
                if self.run_ui:
                    self.update_progress(t("msg.update_disabled"), 0, finished=True)
                self.reporter.warning("run.disabled", t("msg.update_disabled"))
                return

            if self.run_ui:
                self.update_progress(t("msg.check_ipv6_support"), 0)

            self.ipv6_support = config.ipv6_support or await check_ipv6_support_async(reporter=self.reporter)
            await self.main()
        finally:
            if self._owns_reporter:
                self.reporter.close()
                self.reporter = None

    async def start(self, callback=None, event_callback=None):
        def default_callback(*args, **kwargs):
            pass

        self.update_progress = callback or default_callback
        self.run_ui = True if callback else False
        self._initialize_pause_control()
        if self.reporter is None:
            self.reporter = Reporter(
                event_callback=event_callback,
                enable_console=event_callback is None,
            )

        try:
            if not config.open_update:
                if self.run_ui:
                    self.update_progress(t("msg.update_disabled"), 0, finished=True)
                self.reporter.warning("run.disabled", t("msg.update_disabled"))
                return

            if self.run_ui:
                self.update_progress(t("msg.check_ipv6_support"), 0)

            self.ipv6_support = config.ipv6_support or await check_ipv6_support_async(reporter=self.reporter)

            if not os.getenv("GITHUB_ACTIONS") and (config.update_interval or config.update_times):
                await self.scheduler(asyncio.Event())
            elif config.update_startup:
                await self.main()
        finally:
            if self._owns_reporter:
                self.reporter.close()
                self.reporter = None

    def stop(self):
        self.resume()
        for task in self.tasks:
            task.cancel()
        self.tasks = []

        if self.stop_event:
            self.stop_event.set()

    async def scheduler(self, stop_event: asyncio.Event):
        self.stop_event = stop_event
        tz = pytz.timezone(config.time_zone)
        mode = config.update_mode
        update_times = parse_times(config.update_times)

        try:
            self.now = datetime.datetime.now(tz)
            if config.update_startup:
                await self.main()

            while not stop_event.is_set():
                self.now = datetime.datetime.now(tz)

                if mode == "time" and update_times:
                    candidates = []
                    for h, m in update_times:
                        candidate = self.now.replace(hour=h, minute=m, second=0, microsecond=0)
                        if candidate <= self.now:
                            candidate = candidate + datetime.timedelta(days=1)
                        candidates.append(candidate)

                    next_time = min(candidates)
                    wait_seconds = (next_time - self.now).total_seconds()
                    self.reporter.info(
                        "schedule.next_run",
                        t("msg.schedule_update_time").format(time=next_time.strftime("%Y-%m-%d %H:%M:%S")),
                        phase="schedule",
                        scheduled_at=next_time.isoformat(),
                    )

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
                        if stop_event.is_set():
                            break
                    except asyncio.TimeoutError:
                        self.now = datetime.datetime.now(tz)
                        await self.main()
                else:
                    next_time = self.now + datetime.timedelta(hours=config.update_interval)
                    self.reporter.info(
                        "schedule.next_run",
                        t("msg.schedule_update_time").format(time=next_time.strftime("%Y-%m-%d %H:%M:%S")),
                        phase="schedule",
                        scheduled_at=next_time.isoformat(),
                    )

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=config.update_interval * 3600)
                    except asyncio.TimeoutError:
                        self.now = datetime.datetime.now(tz)
                        await self.main()

        except asyncio.CancelledError:
            self.reporter.warning("schedule.cancelled", t("msg.schedule_cancelled"), phase="schedule")


if __name__ == "__main__":
    info = get_version_info()
    cli_reporter = Reporter()
    cli_reporter.info(
        "application.started",
        t("msg.version_info").format(name=info["name"], version=info["version"], build_time=info["build_time"]),
        version=info["version"],
        build_time=info["build_time"],
    )
    log_new_version_if_available(info["version"], reporter=cli_reporter)
    start_version_log_monitor(info["version"], reporter=cli_reporter)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    update_source = UpdateSource(reporter=cli_reporter)
    try:
        loop.run_until_complete(update_source.start())
    finally:
        cli_reporter.close()
