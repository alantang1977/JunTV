import asyncio
import hashlib
import re
import zlib
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from time import time
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector

import utils.constants as constants
from utils.channel import format_channel_name
from utils.config import config
from utils.i18n import t
from utils.reporting import Reporter
from utils.requests.async_tools import SSL_CONTEXT, merge_headers
from utils.retry import max_retries
from utils.tools import (
    count_disabled_urls,
    disable_urls_in_file,
    get_pbar_remaining,
    get_request_url_candidates,
    get_subscribe_entries,
    github_blob_to_raw,
    opencc_t2s,
)

SUBSCRIBE_EPG_MAX_SOURCES = 3
EPG_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
EPG_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
EPG_MAX_PROGRAMMES = 500000
EPG_DAYS_BACK = 1
EPG_DAYS_AHEAD = 14


class EpgResourceLimitError(ValueError):
    pass


def _local_name(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else tag


def _child_text(element, name):
    for child in element:
        if _local_name(child.tag) == name:
            return child.text or ""
    return ""


class EpgStreamParser:
    def __init__(
            self,
            normalized_names=None,
            include_unmatched=True,
            max_programmes=None,
            days_back=None,
            days_ahead=None,
    ):
        self.parser = ET.XMLPullParser(events=("end",))
        self.normalized_names = set(normalized_names or ())
        self.include_unmatched = include_unmatched
        self.max_programmes = max_programmes
        self.days_back = days_back
        self.days_ahead = days_ahead
        self.channels = {}
        self.included_channel_ids = set()
        self.programmes = defaultdict(list)
        self.programme_count = 0
        self.retained_programme_count = 0
        self.now_by_timezone = {}

    def feed(self, content):
        self.parser.feed(content)
        self._drain()

    def close(self):
        self.parser.close()
        self._drain()
        return self.channels, self.programmes

    def _drain(self):
        for _, element in self.parser.read_events():
            tag = _local_name(element.tag)
            if tag == "channel":
                self._process_channel(element)
                element.clear()
            elif tag == "programme":
                self._process_programme(element)
                element.clear()

    def _process_channel(self, element):
        channel_id = element.get("id")
        display_name = _child_text(element, "display-name").strip()
        if not channel_id or not display_name:
            return
        normalized_name = format_channel_name(display_name)
        if (
                not self.include_unmatched
                and self.normalized_names
                and normalized_name not in self.normalized_names
        ):
            return
        self.channels[channel_id] = display_name
        self.included_channel_ids.add(channel_id)

    def _process_programme(self, element):
        self.programme_count += 1
        if self.max_programmes and self.programme_count > self.max_programmes:
            raise EpgResourceLimitError(
                f"EPG programme count exceeds {self.max_programmes}"
            )

        channel_id = element.get("channel")
        if channel_id not in self.included_channel_ids:
            return
        try:
            channel_start = datetime.strptime(
                re.sub(r"\s+", "", element.get("start") or ""),
                "%Y%m%d%H%M%S%z",
            )
            channel_stop = datetime.strptime(
                re.sub(r"\s+", "", element.get("stop") or ""),
                "%Y%m%d%H%M%S%z",
            )
        except (TypeError, ValueError):
            return

        timezone = channel_start.tzinfo
        if timezone not in self.now_by_timezone:
            self.now_by_timezone[timezone] = (
                datetime.now(timezone) if timezone else datetime.now()
            )
        now = self.now_by_timezone[timezone]
        if self.days_back is not None and channel_stop < now - timedelta(days=self.days_back):
            return
        if self.days_ahead is not None and channel_start > now + timedelta(days=self.days_ahead):
            return

        title = _child_text(element, "title").strip()
        if not title:
            return
        output = ET.Element(
            "programme",
            attrib={
                "channel": channel_id,
                "start": channel_start.strftime("%Y%m%d%H%M%S %z"),
                "stop": channel_stop.strftime("%Y%m%d%H%M%S %z"),
            },
        )
        ET.SubElement(output, "title", attrib={"lang": "zh"}).text = opencc_t2s.convert(title)
        self.programmes[channel_id].append(output)
        self.retained_programme_count += 1


def parse_epg(
        epg_content,
        reporter=None,
        request_url=None,
        normalized_names=None,
        include_unmatched=True,
        max_programmes=None,
        days_back=None,
        days_ahead=None,
):
    parser = EpgStreamParser(
        normalized_names=normalized_names,
        include_unmatched=include_unmatched,
        max_programmes=max_programmes,
        days_back=days_back,
        days_ahead=days_ahead,
    )
    try:
        if isinstance(epg_content, str):
            epg_content = epg_content.encode("utf-8")
        elif isinstance(epg_content, bytearray):
            epg_content = bytes(epg_content)
        if isinstance(epg_content, bytes) and epg_content.startswith(b"\x1f\x8b"):
            epg_content = zlib.decompress(epg_content, 16 + zlib.MAX_WBITS)
        parser.feed(epg_content)
        return parser.close()
    except (ET.ParseError, EpgResourceLimitError) as exc:
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


def _canonical_epg_url(url):
    raw_url = github_blob_to_raw(str(url or "").strip())
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        if not parsed.scheme or not hostname:
            return raw_url
        userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
        host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
        port = parsed.port
        if port and not (
                parsed.scheme.lower() == "http" and port == 80
                or parsed.scheme.lower() == "https" and port == 443
        ):
            host = f"{host}:{port}"
        return urlunsplit((
            parsed.scheme.lower(),
            f"{userinfo}{host}",
            parsed.path or "/",
            parsed.query,
            "",
        ))
    except (TypeError, ValueError):
        return raw_url


def _entry_key(entry):
    url = entry.get("url") if isinstance(entry, dict) else entry
    headers = entry.get("headers") if isinstance(entry, dict) else None
    header_key = tuple(
        sorted((str(key).lower(), str(value)) for key, value in (headers or {}).items())
    )
    return _canonical_epg_url(url), header_key


def dedupe_epg_entries(whitelist_entries, default_entries, discovered_entries, discovered_limit):
    result = []
    seen = set()
    duplicate_count = 0
    limited_count = 0
    discovered_added = 0
    groups = (
        (whitelist_entries, 0, "whitelist"),
        (default_entries, 1, "configured"),
        (discovered_entries, 2, "discovered"),
    )
    for entries, priority, origin in groups:
        for raw_entry in entries or ():
            if origin == "discovered" and discovered_added >= discovered_limit:
                limited_count += 1
                continue
            entry = dict(raw_entry) if isinstance(raw_entry, dict) else {"url": raw_entry}
            key = _entry_key(entry)
            if not key[0] or key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            entry["_priority"] = priority
            entry["_origin"] = origin
            result.append(entry)
            if origin == "discovered":
                discovered_added += 1
    return result, duplicate_count, limited_count, discovered_added


async def _consume_epg_response(response, normalized_names, include_unmatched):
    parser = EpgStreamParser(
        normalized_names=normalized_names,
        include_unmatched=include_unmatched,
        max_programmes=EPG_MAX_PROGRAMMES,
        days_back=EPG_DAYS_BACK,
        days_ahead=EPG_DAYS_AHEAD,
    )
    compressed_size = 0
    decompressed_size = 0
    digest = hashlib.sha256()
    decompressor = None
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    undecided = encoding not in {"gzip", "x-gzip", "deflate"}
    prefix = b""

    def feed(content):
        nonlocal decompressed_size
        if not content:
            return
        decompressed_size += len(content)
        if decompressed_size > EPG_MAX_DECOMPRESSED_BYTES:
            raise EpgResourceLimitError(
                f"EPG decompressed content exceeds {EPG_MAX_DECOMPRESSED_BYTES} bytes"
            )
        digest.update(content)
        parser.feed(content)

    if encoding in {"gzip", "x-gzip"}:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj()

    async for chunk in response.content.iter_chunked(64 * 1024):
        compressed_size += len(chunk)
        if compressed_size > EPG_MAX_DOWNLOAD_BYTES:
            raise EpgResourceLimitError(
                f"EPG response exceeds {EPG_MAX_DOWNLOAD_BYTES} bytes"
            )
        if undecided:
            prefix += chunk
            if len(prefix) < 2:
                continue
            undecided = False
            if prefix.startswith(b"\x1f\x8b"):
                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            chunk = prefix
            prefix = b""
        if decompressor:
            pending = chunk
            while pending:
                remaining = EPG_MAX_DECOMPRESSED_BYTES - decompressed_size
                if remaining <= 0:
                    raise EpgResourceLimitError(
                        f"EPG decompressed content exceeds {EPG_MAX_DECOMPRESSED_BYTES} bytes"
                    )
                feed(decompressor.decompress(pending, remaining + 1))
                pending = decompressor.unconsumed_tail
        else:
            feed(chunk)
        await asyncio.sleep(0)

    if undecided and prefix:
        feed(prefix)
    if decompressor:
        remaining = EPG_MAX_DECOMPRESSED_BYTES - decompressed_size
        if remaining <= 0:
            raise EpgResourceLimitError(
                f"EPG decompressed content exceeds {EPG_MAX_DECOMPRESSED_BYTES} bytes"
            )
        feed(decompressor.flush(remaining + 1))
    if decompressed_size == 0:
        raise ValueError("Empty EPG response")
    channels, programmes = parser.close()
    return channels, programmes, digest.hexdigest(), {
        "downloaded_bytes": compressed_size,
        "decompressed_bytes": decompressed_size,
        "programmes": parser.programme_count,
        "retained_programmes": parser.retained_programme_count,
    }


async def _fetch_epg(session, entry, normalized_names, include_unmatched, reporter):
    request_url = entry.get("url")
    headers = merge_headers(entry.get("headers"))
    candidates = get_request_url_candidates(request_url)
    last_error = None
    timeout = ClientTimeout(
        total=max(60, config.request_timeout * 6),
        connect=config.request_timeout,
        sock_read=config.request_timeout,
    )
    for attempt in range(max_retries):
        for candidate in candidates:
            try:
                async with session.get(
                        candidate,
                        headers=headers,
                        proxy=config.http_proxy or None,
                        ssl=SSL_CONTEXT,
                        timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    return await _consume_epg_response(
                        response,
                        normalized_names,
                        include_unmatched,
                    )
            except asyncio.CancelledError:
                raise
            except EpgResourceLimitError:
                raise
            except Exception as exc:
                last_error = exc
        if attempt < max_retries - 1:
            reporter.warning(
                "request.retrying",
                t("msg.failed_retrying_count").format(name=request_url, count=attempt + 1),
                phase="fetch",
                url=request_url,
                attempt=attempt + 1,
            )
    raise Exception(t("msg.failed_retry_max").format(name=request_url)) from last_error


async def get_epg(names=None, callback=None, extra_entries=None, pause_wait=None, reporter: Reporter | None = None):
    owned_reporter = reporter is None
    reporter = reporter or Reporter()
    normalized_names = {format_channel_name(name) for name in (names or []) if name}
    whitelist_entries, default_entries = get_subscribe_entries(constants.epg_path)
    entries, duplicate_count, limited_count, discovered_count = dedupe_epg_entries(
        whitelist_entries,
        default_entries,
        extra_entries or (),
        SUBSCRIBE_EPG_MAX_SOURCES,
    )
    disabled_count = count_disabled_urls(constants.epg_path)
    reporter.info(
        "sources.epg.summary",
        t("msg.epg_urls_whitelist_total").format(
            default_count=len(default_entries),
            whitelist_count=len(whitelist_entries),
            disabled_count=disabled_count,
            total=len(entries),
        ),
        phase="fetch",
        default_count=len(default_entries),
        whitelist_count=len(whitelist_entries),
        disabled_count=disabled_count,
        discovered_count=discovered_count,
        duplicate_count=duplicate_count,
        limited_count=limited_count,
        total=len(entries),
    )
    if not entries:
        if owned_reporter:
            reporter.close()
        return {}

    urls_len = len(entries)
    reporter.start_progress("epg", t("pbar.getting_name").format(name=t("name.epg")), urls_len, phase="fetch")
    start_time = time()
    disabled_urls = set()
    completed_sources = 0
    seen_content_hashes = set()
    programme_records = defaultdict(dict)
    performance_settings = config.performance_settings
    process_semaphore = asyncio.Semaphore(performance_settings.epg_parse_concurrency)

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

    async def process_run(session, entry):
        request_url = entry.get("url")
        source_url = entry.get("source_url", request_url)
        disable_reason = None
        cancelled = False
        try:
            if pause_wait:
                await pause_wait()
            async with process_semaphore:
                if pause_wait:
                    await pause_wait()
                channels, programmes, content_hash, stats = await _fetch_epg(
                    session,
                    entry,
                    normalized_names,
                    config.open_unmatch_category,
                    reporter,
                )
            if content_hash in seen_content_hashes:
                reporter.info(
                    "epg.source_duplicate",
                    t("msg.epg_duplicate_skipped").format(url=request_url),
                    phase="fetch",
                    url=request_url,
                    **stats,
                )
                return
            seen_content_hashes.add(content_hash)
            priority = entry.get("_priority", 2)
            for channel_id, display_name in channels.items():
                normalized_name = format_channel_name(display_name)
                for programme in programmes.get(channel_id, ()):
                    programme_key = (programme.get("start"), programme.get("stop"))
                    existing = programme_records[normalized_name].get(programme_key)
                    if existing is None or priority < existing[0]:
                        programme_records[normalized_name][programme_key] = (priority, programme)
            reporter.info(
                "epg.source_parsed",
                t("msg.epg_source_parsed").format(
                    url=request_url,
                    programmes=stats["retained_programmes"],
                ),
                phase="fetch",
                url=request_url,
                **stats,
            )
            if not channels:
                disable_reason = t("msg.auto_disable_no_match")
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
            disable_reason = t("msg.auto_disable_request_failed")
        finally:
            if disable_reason and config.open_auto_disable_source:
                disabled_urls.add(source_url)
                reporter.warning(
                    "source.disabled",
                    t("msg.auto_disable_source").format(
                        name=t("name.epg"),
                        url=source_url,
                        reason=disable_reason,
                    ),
                    phase="fetch",
                    source=t("name.epg"),
                    url=source_url,
                    reason=disable_reason,
                )
            if not cancelled:
                advance_progress()

    tasks = []
    try:
        async with ClientSession(
                connector=TCPConnector(limit=performance_settings.epg_fetch_concurrency),
                auto_decompress=False,
                trust_env=True,
        ) as session:
            tasks = [asyncio.create_task(process_run(session, entry)) for entry in entries]
            if tasks:
                await asyncio.gather(*tasks)

        active_count = len(whitelist_entries) + len(default_entries)
        disabled_count = 0
        if disabled_urls:
            counts = disable_urls_in_file(constants.epg_path, disabled_urls)
            active_count = counts["active"]
            disabled_count = counts["disabled"]
        result = {
            channel_name: [
                record[1]
                for _, record in sorted(
                    records.items(),
                    key=lambda item: item[0],
                )
            ]
            for channel_name, records in programme_records.items()
        }
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
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        matched_channels = len(programme_records)
        reporter.finish_progress(
            "epg",
            status=t("log.matched_channels").format(count=matched_channels),
            matched_channels=matched_channels,
        )
        if owned_reporter:
            reporter.close()
