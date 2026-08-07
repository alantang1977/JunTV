import asyncio
import gzip
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from time import time

from aiohttp import ClientSession, TCPConnector

import utils.constants as constants
from utils.channel import format_channel_name
from utils.config import config
from utils.i18n import t
from utils.reporting import Reporter
from utils.requests.async_tools import fetch_first
from utils.tools import (
    get_pbar_remaining,
    opencc_t2s,
    github_blob_to_raw,
    get_request_url_candidates,
    get_subscribe_entries,
    count_disabled_urls,
    disable_urls_in_file,
)


def _normalize_epg_content(content, request_url=None, response=None):
    if not content:
        return None

    if isinstance(content, str):
        return content

    if isinstance(content, bytearray):
        content = bytes(content)

    if isinstance(content, bytes) and content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)

    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content


def parse_epg(epg_content, reporter=None, request_url=None):
    try:
        parser = ET.XMLParser(encoding='UTF-8')
        root = ET.fromstring(epg_content, parser=parser)
    except ET.ParseError as exc:
        if reporter:
            reporter.warning(
                "epg.xml_invalid",
                t("log.epg_xml_invalid").format(info=exc),
                phase="fetch",
                url=request_url,
                error_type=type(exc).__name__,
            )
        else:
            print(f"Error parsing XML: {exc}")
        return {}, defaultdict(list)

    channels = {}
    programmes = defaultdict(list)
    now_by_timezone = {}

    for channel in root.findall('channel'):
        channel_id = channel.get('id')
        display_name = channel.find('display-name').text
        channels[channel_id] = display_name

    for programme in root.findall('programme'):
        channel_id = programme.get('channel')
        channel_start = datetime.strptime(
            re.sub(r'\s+', '', programme.get('start')), "%Y%m%d%H%M%S%z")
        channel_stop = datetime.strptime(
            re.sub(r'\s+', '', programme.get('stop')), "%Y%m%d%H%M%S%z")

        timezone = channel_start.tzinfo
        if timezone not in now_by_timezone:
            now_by_timezone[timezone] = datetime.now(timezone) if timezone else datetime.now()
        now = now_by_timezone[timezone]
        if channel_start < (now - timedelta(days=7)):
            continue

        channel_text = opencc_t2s.convert(programme.find('title').text)
        channel_elem = ET.Element(
            'programme',
            attrib={
                "channel": channel_id,
                "start": channel_start.strftime("%Y%m%d%H%M%S %z"),
                "stop": channel_stop.strftime("%Y%m%d%H%M%S %z"),
            },
        )
        channel_elem_s = ET.SubElement(channel_elem, 'title', attrib={"lang": "zh"})
        channel_elem_s.text = channel_text
        programmes[channel_id].append(channel_elem)

    return channels, programmes


def _epg_dedup_key(url) -> str:
    if not url:
        return ""
    key = github_blob_to_raw(str(url)).strip()
    key = re.sub(r"^https?://", "", key, flags=re.IGNORECASE).rstrip("/")
    if key.endswith(".gz"):
        key = key[:-3]
    return key.lower()


async def get_epg(names=None, callback=None, extra_entries=None, pause_wait=None, reporter: Reporter | None = None):
    owned_reporter = reporter is None
    reporter = reporter or Reporter()
    normalized_names = {format_channel_name(name) for name in (names or []) if name}
    whitelist_entries, default_entries = get_subscribe_entries(constants.epg_path)
    configured_entries = whitelist_entries + default_entries
    discovered_entries = []
    if extra_entries:
        seen_keys = {_epg_dedup_key(entry.get("url") if isinstance(entry, dict) else entry)
                     for entry in configured_entries}
        for url in extra_entries:
            key = _epg_dedup_key(url)
            if url and key and key not in seen_keys:
                discovered_entries.append(url)
                seen_keys.add(key)

    disabled_count = count_disabled_urls(constants.epg_path)
    reporter.info(
        "sources.epg.summary",
        t("msg.epg_urls_whitelist_total").format(
            default_count=len(default_entries),
            whitelist_count=len(whitelist_entries),
            disabled_count=disabled_count,
            total=len(configured_entries),
        ),
        phase="fetch",
        default_count=len(default_entries),
        whitelist_count=len(whitelist_entries),
        disabled_count=disabled_count,
        discovered_count=len(discovered_entries),
        total=len(configured_entries) + len(discovered_entries),
    )
    entries = configured_entries + discovered_entries
    if not entries:
        if owned_reporter:
            reporter.close()
        return {}

    urls_len = len(entries)
    reporter.start_progress(
        "epg",
        t("pbar.getting_name").format(name=t("name.epg")),
        urls_len,
        phase="fetch",
    )
    start_time = time()
    result = defaultdict(list)
    all_result_verify = set()
    open_unmatch_category = config.open_unmatch_category
    open_auto_disable_source = config.open_auto_disable_source
    disabled_urls = set()
    fetch_workers = config.performance_settings.fetch_workers
    semaphore = asyncio.Semaphore(fetch_workers)
    completed_sources = 0

    def mark_disabled(source_url: str, reason: str):
        if not open_auto_disable_source or not source_url:
            return
        disabled_urls.add(source_url)
        reporter.warning(
            "source.disabled",
            t("msg.auto_disable_source").format(name=t("name.epg"), url=source_url, reason=reason),
            phase="fetch",
            source=t("name.epg"),
            url=source_url,
            reason=reason,
        )

    def advance_progress():
        nonlocal completed_sources
        completed_sources += 1
        reporter.update_progress(
            "epg",
            completed=completed_sources,
            status=t("log.remaining").format(count=max(0, urls_len - completed_sources)),
        )
        if callback:
            callback(
                t("msg.progress_desc").format(
                    name=f"{t('pbar.get')}{t('name.epg')}",
                    remaining_total=urls_len - completed_sources,
                    item_name=t("pbar.source"),
                    remaining_time=get_pbar_remaining(
                        n=completed_sources,
                        total=urls_len,
                        start_time=start_time,
                    ),
                ),
                int((completed_sources / urls_len) * 100),
            )

    async def process_run(session: ClientSession, entry):
        request_url = entry.get('url') if isinstance(entry, dict) else entry
        source_url = entry.get('source_url', request_url) if isinstance(entry, dict) else request_url
        headers = entry.get('headers') if isinstance(entry, dict) else None
        disable_reason = None
        cancelled = False
        try:
            content = None
            try:
                if pause_wait:
                    await pause_wait()
                async with semaphore:
                    if pause_wait:
                        await pause_wait()
                    payload = await fetch_first(
                        session,
                        get_request_url_candidates(request_url),
                        name=request_url,
                        headers=headers,
                        timeout=config.request_timeout,
                        as_bytes=True,
                        reporter=reporter,
                    )
                content = _normalize_epg_content(payload, request_url=request_url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reporter.warning(
                    "source.request_failed",
                    t("log.request_failed").format(name=t("name.epg"), info=exc),
                    phase="fetch",
                    url=request_url,
                    error_type=type(exc).__name__,
                )
                disable_reason = t("msg.auto_disable_request_failed")

            if content:
                channels, programmes = await asyncio.to_thread(
                    parse_epg,
                    content,
                    reporter,
                    request_url,
                )
                entry_matched = False
                for index, (channel_id, display_name) in enumerate(channels.items()):
                    if index % 250 == 0:
                        if pause_wait:
                            await pause_wait()
                        else:
                            await asyncio.sleep(0)
                    display_name = format_channel_name(display_name)
                    if not open_unmatch_category and normalized_names and display_name not in normalized_names:
                        continue
                    entry_matched = True
                    if channel_id in all_result_verify or display_name in all_result_verify:
                        continue
                    if not channel_id.isdigit():
                        all_result_verify.add(channel_id)
                    all_result_verify.add(display_name)
                    result[display_name] = programmes[channel_id]
                if not entry_matched and not disable_reason:
                    disable_reason = t("msg.auto_disable_no_match")
            elif not disable_reason:
                disable_reason = t("msg.auto_disable_empty_content")
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            reporter.error(
                "source.parse_failed",
                t("msg.error_name_info").format(name=request_url, info=exc),
                phase="fetch",
                url=request_url,
                error_type=type(exc).__name__,
            )
            if not disable_reason:
                disable_reason = t("msg.auto_disable_request_failed")
        finally:
            if disable_reason:
                mark_disabled(source_url, disable_reason)
            if not cancelled:
                advance_progress()

    async def run_batch(session, batch):
        tasks = [asyncio.create_task(process_run(session, entry)) for entry in batch]
        try:
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    try:
        async with ClientSession(
                connector=TCPConnector(limit=fetch_workers),
                trust_env=True,
        ) as session:
            await run_batch(session, configured_entries)
            await run_batch(session, discovered_entries)

        active_count = len(configured_entries)
        disabled_count = 0
        if disabled_urls:
            counts = disable_urls_in_file(constants.epg_path, disabled_urls)
            active_count = counts["active"]
            disabled_count = counts["disabled"]
        reporter.info(
            "sources.epg.finished",
            t("msg.auto_disable_source_done").format(
                name=t("name.epg"),
                active_count=active_count,
                disabled_count=disabled_count,
            ),
            phase="fetch",
            active_count=active_count,
            disabled_count=disabled_count,
            matched_channels=len(result),
        )
        return result
    finally:
        reporter.finish_progress(
            "epg",
            status=t("log.matched_channels").format(count=len(result)),
            matched_channels=len(result),
        )
        if owned_reporter:
            reporter.close()
