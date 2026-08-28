import asyncio
import gzip
import hashlib
import json
import math
import os
import pickle
import re
import tempfile
from collections import defaultdict, Counter, OrderedDict
from itertools import chain
from typing import cast

import utils.constants as constants
from utils.artifacts import ArtifactWriter
from utils.alias import Alias
from utils.channel_quality import channel_result_rejection, is_channel_result_valid
from utils.config import config
from utils.channel_repository import upsert_stream_screenshot
from utils.db import sync_result_data
from utils.ffmpeg import capture_stream_screenshot, check_ffmpeg_installed_status
from utils.frozen import is_url_frozen, mark_url_bad, mark_url_good
from utils.i18n import t
from utils.identity import stable_result_id
from utils.ip_checker import IPChecker
from utils.requests.tools import headers as request_headers
from utils.speed import (
    create_speed_test_session,
    get_speed,
    get_speed_result,
    get_sort_result
)
from utils.tools import (
    format_name,
    get_name_value,
    check_url_by_keywords,
    get_total_urls,
    add_url_info,
    resource_path,
    get_name_urls_from_file,
    get_datetime_now,
    get_url_host,
    check_ipv_type_match,
    convert_to_m3u,
    custom_print,
    get_name_uri_from_dir,
    get_resolution_value,
    get_public_url,
    build_path_list,
    get_real_path,
    count_files_by_ext,
    fast_get_ipv_type
)
from utils.types import ChannelData, OriginType, CategoryChannelData, WhitelistMaps
from utils.whitelist import is_url_whitelisted, get_whitelist_url, get_whitelist_total_count

channel_alias = Alias()
ip_checker = IPChecker()
location_list = config.location
isp_list = config.isp
open_supply = config.open_supply
min_speed = config.min_speed
resolution_speed_map = config.resolution_speed_map
open_history = config.open_history
open_local = config.open_local
open_rtmp = config.rtmp_available
retain_origin = ["whitelist", "hls"]

_TOTAL_URLS_CACHE_MAX_SIZE = 2048
_TOTAL_URLS_CACHE = OrderedDict()

_CHANNEL_OUTPUT_FIELDS = (
    "id",
    "url",
    "origin",
    "ipv_type",
    "extra_info",
    "headers",
    "catchup",
    "tvg_logo",
    "supply",
    "video_codec",
    "audio_codec",
    "resolution",
    "fps",
)


def _build_total_urls_signature(info_list: list[ChannelData]) -> str:
    """
    Build a stable signature for a channel info list.
    """
    hasher = hashlib.sha1()
    for info in info_list or []:
        if not isinstance(info, dict):
            hasher.update(repr(info).encode("utf-8", errors="ignore"))
            hasher.update(b"\x1e")
            continue

        output_info = {key: info.get(key) for key in _CHANNEL_OUTPUT_FIELDS}
        origin = output_info.get("origin") or ""
        extra_info = output_info.get("extra_info") or ""
        if origin not in retain_origin and not extra_info:
            extra_info = constants.origin_map.get(origin, "")
        output_info["extra_info"] = extra_info
        hasher.update(
            json.dumps(
                output_info,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8", errors="ignore")
        )
        hasher.update(b"\x1e")

    return hasher.hexdigest()


def _get_total_urls_cached(
        info_list: list[ChannelData],
        ipv_type_prefer,
        origin_type_prefer,
        rtmp_type=None,
        apply_limit: bool = True,
) -> tuple:
    """
    Cached wrapper for `get_total_urls()`.
    """
    ipv_key = tuple(ipv_type_prefer or ())
    origin_key = tuple(origin_type_prefer or ())
    rtmp_key = tuple(rtmp_type or ())
    cache_key = (
        _build_total_urls_signature(info_list),
        ipv_key,
        origin_key,
        rtmp_key,
        bool(apply_limit),
        config.output_urls_limit,
    )
    cached = _TOTAL_URLS_CACHE.get(cache_key)
    if cached is not None:
        _TOTAL_URLS_CACHE.move_to_end(cache_key)
        return cached

    total_urls = tuple(get_total_urls(info_list, ipv_type_prefer, origin_type_prefer, rtmp_type, apply_limit))
    _TOTAL_URLS_CACHE[cache_key] = total_urls
    if len(_TOTAL_URLS_CACHE) > _TOTAL_URLS_CACHE_MAX_SIZE:
        _TOTAL_URLS_CACHE.popitem(last=False)
    return total_urls


def format_channel_data(url: str, origin: OriginType) -> ChannelData:
    """
    Format the channel data
    """
    url_partition = url.partition("$")
    url = url_partition[0]
    info = url_partition[2]
    if info and info.startswith("!"):
        origin = "whitelist"
        info = info[1:]
    return {
        "id": stable_result_id(url),
        "url": url,
        "host": get_url_host(url),
        "origin": cast(OriginType, origin),
        "ipv_type": None,
        "extra_info": info
    }


def check_channel_need_frozen(info) -> bool:
    """
    Check if the channel need to be frozen
    """
    return channel_result_rejection(
        info,
        retain_special=True,
    ) in {"unreachable", "filtered_resolution"}


def get_channel_data_from_file(channels, file, whitelist_maps, blacklist,
                               local_data=None, hls_data=None) -> CategoryChannelData:
    """
    Get the channel data from the file
    """
    current_category = ""
    matched_local_names = set()
    matched_hls_names = set()
    unmatch_category = t("content.unmatch_channel")

    def append_unmatch_data(name: str, info_list: list):
        category_dict = channels[unmatch_category]
        if name not in category_dict:
            category_dict[name] = []
        existing_urls = {d.get("url") for d in category_dict.get(name, []) if d.get("url")}
        for item in info_list:
            if not item:
                continue
            url = item.get("url")
            if not url or url in existing_urls:
                continue
            category_dict[name].append(item)
            existing_urls.add(url)

    for line in file:
        line = line.strip()
        if "#genre#" in line:
            current_category = re.split(r"[，,]", line, maxsplit=1)[0]
        else:
            name_value = get_name_value(
                line, pattern=constants.demo_txt_pattern, check_value=False
            )
            if name_value and name_value[0]:
                name = name_value[0]["name"]
                url = name_value[0]["value"]
                category_dict = channels[current_category]
                first_time = name not in category_dict
                if first_time:
                    category_dict[name] = []
                existing_urls = {d.get("url") for d in category_dict.get(name, []) if d.get("url")}

                if first_time:
                    for whitelist_url in get_whitelist_url(whitelist_maps, name):
                        formatted = format_channel_data(whitelist_url, "whitelist")
                        if formatted["url"] not in existing_urls:
                            category_dict[name].append(formatted)
                            existing_urls.add(formatted["url"])

                    if hls_data and name in hls_data:
                        matched_hls_names.add(name)
                        for hls_url in hls_data[name]:
                            formatted = format_channel_data(hls_url, "hls")
                            if formatted["url"] not in existing_urls:
                                category_dict[name].append(formatted)
                                existing_urls.add(formatted["url"])

                    if open_local and local_data:
                        alias_names = channel_alias.get(name)
                        alias_names.update([name, format_name(name)])
                        for alias_name in alias_names:
                            if alias_name in local_data:
                                matched_local_names.add(alias_name)
                                for local_url in local_data[alias_name]:
                                    if not check_url_by_keywords(local_url, blacklist):
                                        local_url_origin: OriginType = "whitelist" if is_url_whitelisted(whitelist_maps,
                                                                                                         local_url,
                                                                                                         name) else "local"
                                        formatted = format_channel_data(local_url, local_url_origin)
                                        if formatted["url"] not in existing_urls:
                                            category_dict[name].append(formatted)
                                            existing_urls.add(formatted["url"])
                            elif alias_name.startswith("re:"):
                                raw_pattern = alias_name[3:]
                                try:
                                    pattern = re.compile(raw_pattern)
                                    for local_name in local_data:
                                        if re.match(pattern, local_name):
                                            matched_local_names.add(local_name)
                                            for local_url in local_data[local_name]:
                                                if not check_url_by_keywords(local_url, blacklist):
                                                    local_url_origin: OriginType = "whitelist" if is_url_whitelisted(
                                                        whitelist_maps,
                                                        local_url,
                                                        name) else "local"
                                                    formatted = format_channel_data(local_url, local_url_origin)
                                                    if formatted["url"] not in existing_urls:
                                                        category_dict[name].append(formatted)
                                                        existing_urls.add(formatted["url"])
                                except re.error:
                                    pass
                if url:
                    if is_url_whitelisted(whitelist_maps, url, name):
                        formatted = format_channel_data(url, "whitelist")
                        if formatted["url"] not in existing_urls:
                            category_dict[name].append(formatted)
                            existing_urls.add(formatted["url"])
                    elif open_local and not check_url_by_keywords(url, blacklist):
                        formatted = format_channel_data(url, "local")
                        if formatted["url"] not in existing_urls:
                            category_dict[name].append(formatted)
                            existing_urls.add(formatted["url"])

    if config.open_unmatch_category:
        if open_local and local_data:
            for local_name, local_urls in local_data.items():
                if local_name in matched_local_names:
                    continue
                unmatch_local_urls = [
                    format_channel_data(local_url, "whitelist" if is_url_whitelisted(whitelist_maps, local_url,
                                                                                     local_name) else "local")
                    for local_url in local_urls
                    if not check_url_by_keywords(local_url, blacklist)
                ]
                if unmatch_local_urls:
                    append_unmatch_data(local_name, unmatch_local_urls)

        if hls_data and open_rtmp:
            for hls_name, hls_urls in hls_data.items():
                if hls_name in matched_hls_names:
                    continue
                unmatch_hls_urls = [format_channel_data(hls_url, "hls") for hls_url in hls_urls]
                if unmatch_hls_urls:
                    append_unmatch_data(hls_name, unmatch_hls_urls)
    return channels


def get_channel_items(whitelist_maps, blacklist, reporter=None) -> CategoryChannelData:
    """
    Get the channel items from the source file
    """
    user_source_file = resource_path(config.source_file)
    channels = defaultdict(lambda: defaultdict(list))
    hls_data = None
    if config.rtmp_available:
        hls_data = get_name_uri_from_dir(constants.hls_path)
    local_paths = build_path_list(constants.local_dir_path)
    local_data = get_name_urls_from_file([get_real_path(constants.local_path)] + local_paths)
    whitelist_count = get_whitelist_total_count(whitelist_maps)
    blacklist_count = len(blacklist)
    channel_logo_count = count_files_by_ext(resource_path(constants.channel_logo_path), [config.logo_type])
    if reporter:
        reporter.info(
            "channels.catalog_loaded",
            t("log.channels_catalog_loaded").format(
                whitelist=whitelist_count,
                blacklist=blacklist_count,
                logos=channel_logo_count,
            ),
            phase="prepare",
            whitelist=whitelist_count,
            blacklist=blacklist_count,
            logos=channel_logo_count,
        )
    else:
        if whitelist_count:
            print(t("msg.whitelist_found").format(count=whitelist_count))
        if blacklist_count:
            print(t("msg.blacklist_found").format(count=blacklist_count))
        if channel_logo_count:
            print(t("msg.channel_logo_found").format(count=channel_logo_count))

    if os.path.exists(user_source_file):
        with open(user_source_file, "r", encoding="utf-8") as file:
            channels = get_channel_data_from_file(
                channels, file, whitelist_maps, blacklist, local_data, hls_data
            )

    source_name_targets = defaultdict(list)
    for cate, data in channels.items():
        for name in data.keys():
            source_name_targets[format_channel_name(name)].append((cate, name))

    if config.open_history and os.path.exists(constants.cache_path):
        unmatched_history = defaultdict(list)

        def _append_history_items(channel_data, info_list):
            urls = [url for item in channel_data if (url := item.get("url"))]
            for info in info_list:
                if not info:
                    continue
                info_url = info.get("url")
                try:
                    if info.get("origin") in retain_origin or check_url_by_keywords(info_url, blacklist):
                        continue
                    if check_channel_need_frozen(info):
                        mark_url_bad(info_url, initial=True)
                        continue
                except Exception:
                    pass
                if info_url and info_url not in urls:
                    channel_data.append(info)
                    urls.append(info_url)

        try:
            with gzip.open(constants.cache_path, "rb") as file:
                old_result = pickle.load(file) or {}
                for cate, data in old_result.items():
                    for name, info_list in data.items():
                        targets = source_name_targets.get(format_channel_name(name))
                        if targets:
                            for target_cate, target_name in targets:
                                channel_data = channels[target_cate][target_name]
                                _append_history_items(channel_data, info_list)
                                if not channel_data:
                                    for info in info_list:
                                        old_result_url = info.get("url") if info else None
                                        if info and info.get(
                                                "origin") not in retain_origin and old_result_url and not check_url_by_keywords(
                                            old_result_url, blacklist):
                                            channel_data.append(info)
                        else:
                            unmatched_history[name].extend(info_list)
        except Exception as e:
            if reporter:
                reporter.warning(
                    "cache.load_failed",
                    t("msg.error_load_cache").format(info=e),
                    phase="prepare",
                    error_type=type(e).__name__,
                )
            else:
                print(t("msg.error_load_cache").format(info=e))

        if unmatched_history and config.open_unmatch_category:
            unmatch_category = t("content.unmatch_channel")
            for name, info_list in unmatched_history.items():
                append_data_to_info_data(
                    channels,
                    unmatch_category,
                    name,
                    info_list,
                    whitelist_maps=whitelist_maps,
                    blacklist=blacklist,
                    skip_validation=True,
                )
    return channels


def format_channel_name(name):
    """
    Format the channel name with sub and replace and lower
    """
    return channel_alias.get_primary(name)


def channel_name_is_equal(name1, name2):
    """
    Check if the channel name is equal
    """
    name1_format = format_channel_name(name1)
    name2_format = format_channel_name(name2)
    return name1_format == name2_format


def get_channel_results_by_name(name, data):
    """
    Get channel results from data by name
    """
    format_name = format_channel_name(name)
    results = data.get(format_name, [])
    return results


def get_channel_url(text):
    """
    Get the url from text
    """
    url = None
    url_search = constants.url_pattern.search(text)
    if url_search:
        url = url_search.group()
    return url


def init_info_data(data: dict, category: str, name: str) -> None:
    """
    Initialize channel info data structure if not exists
    """
    data.setdefault(category, {}).setdefault(name, [])


def append_data_to_info_data(
        info_data: dict,
        category: str,
        name: str,
        data: list,
        origin: str = None,
        whitelist_maps: WhitelistMaps = None,
        blacklist: list = None,
        ipv_type_data: dict = None,
        skip_validation: bool = False
) -> None:
    """
    Append channel data to total info data with deduplication and validation

    Args:
        info_data: The main data structure to update
        category: Category key for the data
        name: Name key within the category
        data: List of channel items to process
        origin: Default origin for items
        whitelist_maps: Maps of whitelist keywords
        blacklist: List of blacklist keywords
        ipv_type_data: Dictionary to cache IP type information
        skip_validation: If True, skip validation and directly append data
    """
    init_info_data(info_data, category, name)

    channel_list = info_data[category][name]
    existing_map = {
        stable_result_id(info["url"], info.get("headers")): idx
        for idx, info in enumerate(channel_list)
        if info.get("url")
    }

    for item in data:
        try:
            raw_url = item.get("url")
            host = item.get("host") or (get_url_host(raw_url) if raw_url else None)
            date = item.get("date")
            delay = item.get("delay")
            speed = item.get("speed")
            resolution = item.get("resolution")
            url_origin = item.get("origin", origin)
            ipv_type = item.get("ipv_type")
            location = item.get("location")
            isp = item.get("isp")
            headers = item.get("headers")
            catchup = item.get("catchup")
            tvg_logo = item.get("tvg_logo")
            extra_info = item.get("extra_info", "")

            if not raw_url:
                continue

            normalized_url = raw_url
            if url_origin not in retain_origin:
                normalized_url = get_channel_url(raw_url)
                if not normalized_url:
                    continue
                if is_url_frozen(normalized_url):
                    continue
                if blacklist and check_url_by_keywords(normalized_url, blacklist):
                    continue

            channel_id = stable_result_id(normalized_url, headers)

            if url_origin != "whitelist" and whitelist_maps and is_url_whitelisted(whitelist_maps, normalized_url,
                                                                                   name):
                url_origin = "whitelist"

            if skip_validation and url_origin not in retain_origin and not ipv_type:
                if ipv_type_data and host in ipv_type_data:
                    ipv_type = ipv_type_data[host]
                else:
                    ipv_type = fast_get_ipv_type(host)
                    if ipv_type_data is not None and host:
                        ipv_type_data[host] = ipv_type

            if channel_id in existing_map:
                existing_idx = existing_map[channel_id]
                existing_origin = channel_list[existing_idx].get("origin")
                if existing_origin != "whitelist" and url_origin == "whitelist":
                    channel_list[existing_idx] = {
                        "id": channel_id,
                        "url": normalized_url,
                        "host": host or get_url_host(normalized_url),
                        "date": date,
                        "delay": delay,
                        "speed": speed,
                        "resolution": resolution,
                        "origin": url_origin,
                        "ipv_type": ipv_type,
                        "location": location,
                        "isp": isp,
                        "headers": headers,
                        "catchup": catchup,
                        "tvg_logo": tvg_logo,
                        "extra_info": extra_info
                    }
                    continue
                else:
                    continue

            url = normalized_url
            supply = False

            if url_origin not in retain_origin:
                if not skip_validation:
                    if not ipv_type:
                        if ipv_type_data and host in ipv_type_data:
                            ipv_type = ipv_type_data[host]
                        else:
                            ipv_type = ip_checker.get_ipv_type(url)
                            if ipv_type_data is not None:
                                ipv_type_data[host] = ipv_type

                    if not check_ipv_type_match(ipv_type):
                        continue

                    if not location or not isp:
                        ip = ip_checker.get_ip(url)
                        if ip:
                            location, isp = ip_checker.find_map(ip)

                    if location and location_list and not any(item in location for item in location_list):
                        if not open_supply:
                            continue
                        supply = True

                    if isp and isp_list and not any(item in isp for item in isp_list):
                        if not open_supply:
                            continue
                        supply = True

            channel_list.append({
                "id": channel_id,
                "url": url,
                "host": host or get_url_host(url),
                "date": date,
                "delay": delay,
                "speed": speed,
                "resolution": resolution,
                "origin": url_origin,
                "ipv_type": ipv_type,
                "location": location,
                "isp": isp,
                "headers": headers,
                "catchup": catchup,
                "tvg_logo": tvg_logo,
                "extra_info": extra_info,
                "supply": supply
            })
            existing_map[channel_id] = len(channel_list) - 1

        except Exception as e:
            print(t("msg.error_append_channel_data").format(info=e))
            continue


def append_old_data_to_info_data(
        info_data,
        cate,
        name,
        data,
        whitelist_maps=None,
        blacklist=None,
        ipv_type_data=None,
        silent=False,
):
    """
    Append old existed channel data to total info data
    """

    def append_and_print(items, origin, label):
        if items:
            append_data_to_info_data(
                info_data, cate, name, items,
                origin=origin if origin else None,
                whitelist_maps=whitelist_maps,
                blacklist=blacklist,
                ipv_type_data=ipv_type_data
            )
        items_len = len(items)
        if items_len > 0 and not silent:
            print(f"{label}: {items_len}", end=", ")

    whitelist_data = [item for item in data if item["origin"] == "whitelist"]
    append_and_print(whitelist_data, "whitelist", t("name.whitelist"))

    if open_local:
        local_data = [item for item in data if item["origin"] == "local"]
        append_and_print(local_data, "local", t("name.local"))

    if open_rtmp:
        hls_data = [item for item in data if item["origin"] == "hls"]
        append_and_print(hls_data, None, t("name.hls"))

    if open_history:
        history_data = [item for item in data if item["origin"] not in ["hls", "local", "whitelist"]]
        append_and_print(history_data, None, t("name.history"))


def print_channel_number(data: CategoryChannelData, cate: str, name: str, silent=False):
    """
    Print channel number
    """
    channel_list = data.get(cate, {}).get(name, [])
    if silent:
        return
    print("IPv4:", len([channel for channel in channel_list if channel["ipv_type"] == "ipv4"]), end=", ")
    print("IPv6:", len([channel for channel in channel_list if channel["ipv_type"] == "ipv6"]), end=", ")
    print(
        f"{t("name.total")}:",
        len(channel_list),
    )


def append_total_data(
        items,
        data,
        subscribe_result=None,
        whitelist_maps=None,
        blacklist=None,
        reporter=None,
):
    """
    Append all method data to total info data
    """
    items = list(items)
    total_result = [
        ("subscribe", subscribe_result),
    ]
    unmatch_category = t("content.unmatch_channel")
    source_names = {
        format_channel_name(name)
        for cate, channel_obj in items
        if cate != unmatch_category
        for name in channel_obj.keys()
    }
    url_hosts_ipv_type = {}
    for obj in data.values():
        for value_list in obj.values():
            for value in value_list:
                if value_ipv_type := value.get("ipv_type", None):
                    url_hosts_ipv_type[get_url_host(value["url"])] = value_ipv_type
    for cate, channel_obj in items:
        if cate == unmatch_category:
            for name, old_info_list in channel_obj.items():
                if old_info_list:
                    append_data_to_info_data(
                        data,
                        cate,
                        name,
                        old_info_list,
                        whitelist_maps=whitelist_maps,
                        blacklist=blacklist,
                        ipv_type_data=url_hosts_ipv_type,
                        skip_validation=True,
                    )
            continue

        for name, old_info_list in channel_obj.items():
            if not reporter:
                print(f"{name}:", end=" ")
            if old_info_list:
                append_old_data_to_info_data(data, cate, name, old_info_list, whitelist_maps=whitelist_maps,
                                             blacklist=blacklist,
                                             ipv_type_data=url_hosts_ipv_type, silent=bool(reporter))
            for method, result in total_result:
                if config.open_method[method]:
                    name_results = get_channel_results_by_name(name, result)
                    append_data_to_info_data(
                        data, cate, name, name_results, origin=method, whitelist_maps=whitelist_maps,
                        blacklist=blacklist,
                        ipv_type_data=url_hosts_ipv_type
                    )
                    if not reporter:
                        print(f"{t(f"name.{method}")}:", len(name_results), end=", ")
            print_channel_number(data, cate, name, silent=bool(reporter))

    if config.open_unmatch_category and subscribe_result:
        unmatch_result = {
            name: info_list
            for name, info_list in subscribe_result.items()
            if name not in source_names
        }
        if unmatch_result:
            for name, info_list in unmatch_result.items():
                append_data_to_info_data(
                    data,
                    unmatch_category,
                    name,
                    info_list,
                    origin="subscribe",
                    whitelist_maps=whitelist_maps,
                    blacklist=blacklist,
                    ipv_type_data=url_hosts_ipv_type,
                    skip_validation=True,
                )
    if reporter:
        ipv4_count = sum(
            1
            for channel_obj in data.values()
            for info_list in channel_obj.values()
            for item in info_list
            if item.get("ipv_type") == "ipv4"
        )
        ipv6_count = sum(
            1
            for channel_obj in data.values()
            for info_list in channel_obj.values()
            for item in info_list
            if item.get("ipv_type") == "ipv6"
        )
        total_count = sum(
            len(info_list)
            for channel_obj in data.values()
            for info_list in channel_obj.values()
        )
        reporter.info(
            "channels.aggregated",
            t("log.channels_aggregated").format(
                channels=len(source_names),
                total=total_count,
                ipv4=ipv4_count,
                ipv6=ipv6_count,
            ),
            phase="aggregate",
            channels=len(source_names),
            total=total_count,
            ipv4=ipv4_count,
            ipv6=ipv6_count,
        )


def is_valid_speed_result(info) -> bool:
    """
    Check if the speed test result is valid
    """
    return is_channel_result_valid(info)


def get_speed_test_status(info, is_valid: bool) -> str:
    status = info.get("test_status")
    if status in {"timeout", "request_error", "probe_error", "cancelled"}:
        return status
    if is_valid:
        return "valid"
    delay = info.get("delay")
    speed = info.get("speed") or 0
    if delay is None or delay == -1 or not speed:
        return status or "unreachable"
    return channel_result_rejection(info) or status or "invalid"


def format_speed_test_record(record):
    status = record.get("status") or "unknown"
    status_label = t(f"status.{status}", status)
    return (
        f"ID: {record.get('id')}, {t('name.name')}: {record.get('name')}, "
        f"{t('log.status')}: {status_label}, {t('pbar.url')}: {record.get('url')}, "
        f"{t('name.from')}: {record.get('origin_name')}, "
        f"{t('name.ipv_type')}: {record.get('ipv_type')}, "
        f"{t('name.location')}: {record.get('location') or '—'}, "
        f"{t('name.isp')}: {record.get('isp') or '—'}, "
        f"{t('name.delay')}: {record.get('delay_ms') if record.get('delay_ms') is not None else '—'} ms, "
        f"{t('name.speed')}: {record.get('speed_mib_s', 0):.2f} MiB/s, "
        f"{t('name.resolution')}: {record.get('resolution') or '—'}, "
        f"{t('name.fps')}: {record.get('fps') or t('name.unknown')}, "
        f"{t('name.video_codec')}: {record.get('video_codec') or t('name.unknown')}, "
        f"{t('name.audio_codec')}: {record.get('audio_codec') or t('name.unknown')}"
    )


def build_speed_test_record(cate, name, merged, is_valid, run_id=None):
    origin = merged.get("origin")
    return {
        "event": "speed_test.completed",
        "run_id": run_id,
        "category": cate,
        "id": merged.get("id"),
        "name": name,
        "url": merged.get("url"),
        "origin": origin,
        "origin_name": t(f"name.{origin}") if origin else origin,
        "ipv_type": merged.get("ipv_type"),
        "location": merged.get("location"),
        "isp": merged.get("isp"),
        "delay_ms": merged.get("delay") if merged.get("delay") not in {-1, None} else None,
        "speed_mib_s": float(merged.get("speed") or 0),
        "resolution": merged.get("resolution"),
        "fps": merged.get("fps"),
        "video_codec": merged.get("video_codec"),
        "audio_codec": merged.get("audio_codec"),
        "error_type": merged.get("error_type"),
        "status": get_speed_test_status(merged, is_valid),
        "valid": is_valid,
    }


async def test_speed(
        data,
        ipv6=False,
        callback=None,
        on_task_complete=None,
        pause_wait=None,
        reporter=None,
):
    """
    Test speed of channel data
    """
    ipv6_proxy_url = None if (not config.open_ipv6 or ipv6) else constants.ipv6_proxy
    open_full_speed_test = config.speed_test_mode == "full" or config.open_full_speed_test
    needs_ffmpeg = config.open_filter_resolution or config.open_stream_screenshot
    ffmpeg_available = check_ffmpeg_installed_status() if needs_ffmpeg else False
    get_resolution = config.open_filter_resolution and ffmpeg_available
    capture_screenshots = config.open_stream_screenshot and ffmpeg_available
    performance = config.performance_settings
    concurrency = performance.speed_test_concurrency
    http_semaphore = asyncio.Semaphore(concurrency)
    probe_semaphore = asyncio.Semaphore(performance.probe_concurrency)
    screenshot_semaphore = asyncio.Semaphore(min(2, performance.probe_concurrency))
    screenshot_speed_threshold = min(
        [min_speed, *resolution_speed_map.values()],
        default=min_speed,
    )
    speed_writer = ArtifactWriter(
        constants.speed_test_log_path,
        constants.speed_test_jsonl_path,
        format_speed_test_record,
        limit=10000,
    )
    result_writer = ArtifactWriter(
        constants.result_log_path,
        constants.result_jsonl_path,
        format_speed_test_record,
        limit=10000,
    )

    total_tasks = sum(len(info_list) for channel_obj in data.values() for info_list in channel_obj.values())
    total_tasks_by_channel = defaultdict(int)
    for cate, channel_obj in data.items():
        for name, info_list in channel_obj.items():
            total_tasks_by_channel[(cate, name)] += len(info_list)
    completed = 0
    grouped_results = {}
    completed_by_channel = defaultdict(int)
    # This target controls quick testing only. Candidate retention and file
    # export use separate limits.
    speed_test_target = config.speed_test_target
    valid_count_by_channel = defaultdict(int)
    stopped_channels = set()

    def handle_result(cate, name, info, result):
        nonlocal completed
        if cate not in grouped_results:
            grouped_results[cate] = {}
        if name not in grouped_results[cate]:
            grouped_results[cate][name] = []
        merged = {**info, **result}
        grouped_results[cate][name].append(merged)

        if check_channel_need_frozen(merged):
            mark_url_bad(merged.get("url"))
        else:
            mark_url_good(merged.get("url"))

        is_valid = is_valid_speed_result(merged)
        reached_limit = False
        if is_valid:
            valid_count_by_channel[(cate, name)] += 1
            if (
                not open_full_speed_test
                and valid_count_by_channel[(cate, name)] >= speed_test_target
            ):
                stopped_channels.add((cate, name))
                reached_limit = valid_count_by_channel[(cate, name)] == speed_test_target

        record = build_speed_test_record(
            cate,
            name,
            merged,
            is_valid,
            run_id=reporter.run_id if reporter else None,
        )
        speed_writer.write(record)
        if is_valid:
            result_writer.write({**record, "event": "speed_test.valid_result"})

        completed += 1
        completed_by_channel[(cate, name)] += 1

        is_channel_last = reached_limit or completed_by_channel[(cate, name)] >= total_tasks_by_channel.get((cate, name), 0)
        is_last = completed >= total_tasks

        if on_task_complete:
            try:
                on_task_complete(cate, name, merged, is_channel_last, is_last, is_valid)
            except Exception:
                pass

        if callback:
            try:
                callback()
            except Exception:
                pass

    def iter_items():
        for cate, channel_obj in data.items():
            for name, info_list in channel_obj.items():
                for info in info_list:
                    info['name'] = name
                    yield cate, name, info

    item_iterator = iter(iter_items())
    skipped = 0

    try:
        async with create_speed_test_session(concurrency) as session:
            async def worker():
                nonlocal skipped
                while True:
                    if pause_wait:
                        await pause_wait()
                    try:
                        cate, name, info = next(item_iterator)
                    except StopIteration:
                        return

                    if (cate, name) in stopped_channels:
                        skipped += 1
                        continue
                    result = {}
                    try:
                        async with asyncio.timeout(config.speed_test_timeout):
                            result = await get_speed(
                                info,
                                headers=info.get("headers") or None,
                                ipv6_proxy=ipv6_proxy_url,
                                filter_resolution=get_resolution,
                                timeout=config.speed_test_timeout,
                                session=session,
                                http_semaphore=http_semaphore,
                                probe_semaphore=probe_semaphore,
                            )
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        if task is not None and task.cancelling():
                            raise
                        result = {
                            "speed": 0,
                            "delay": -1,
                            "resolution": None,
                            "fps": None,
                            "video_codec": None,
                            "audio_codec": None,
                            "test_status": "request_error",
                            "error_type": "CancelledError",
                        }
                    except TimeoutError:
                        result = {
                            "speed": 0,
                            "delay": -1,
                            "resolution": None,
                            "fps": None,
                            "video_codec": None,
                            "audio_codec": None,
                            "test_status": "timeout",
                        }
                    except Exception as exc:
                        result = {
                            "speed": 0,
                            "delay": -1,
                            "resolution": None,
                            "fps": None,
                            "video_codec": None,
                            "audio_codec": None,
                            "test_status": "request_error",
                            "error_type": type(exc).__name__,
                        }
                    speed_value = result.get("speed") or 0
                    delay_value = result.get("delay")
                    if (
                        capture_screenshots
                        and delay_value not in {-1, None}
                        and isinstance(speed_value, (int, float))
                        and speed_value >= screenshot_speed_threshold
                        and not math.isinf(speed_value)
                    ):
                        result_key = info.get("id") or stable_result_id(
                            info.get("url", ""),
                            info.get("headers"),
                        )
                        try:
                            async with screenshot_semaphore:
                                screenshot = await capture_stream_screenshot(
                                    info.get("url", "").partition("$")[0],
                                    result_key,
                                    constants.screenshot_dir,
                                    headers={
                                        **request_headers,
                                        **(info.get("headers") or {}),
                                    },
                                    timeout=config.stream_screenshot_timeout,
                                    width=config.stream_screenshot_width,
                                )
                            await asyncio.to_thread(
                                upsert_stream_screenshot,
                                constants.channel_results_path,
                                screenshot,
                            )
                            if screenshot.get("status") == "success":
                                for key in (
                                    "resolution",
                                    "fps",
                                    "video_codec",
                                    "audio_codec",
                                ):
                                    if screenshot.get(key) is not None:
                                        result[key] = screenshot[key]
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass
                    if pause_wait:
                        await pause_wait()
                    handle_result(cate, name, info, result)

            workers = [
                asyncio.create_task(worker())
                for _ in range(min(concurrency, total_tasks))
            ]
            if workers:
                await asyncio.gather(*workers)

        if skipped and callback:
            callback(skipped)
    finally:
        speed_writer.close()
        result_writer.close()

    if reporter:
        reporter.info(
            "speed_test.artifacts_written",
            t("log.speed_artifacts_written").format(
                speed_log=constants.speed_test_log_path,
                result_log=constants.result_log_path,
            ),
            phase="speed_test",
            attempted=speed_writer.count,
            valid=result_writer.count,
            skipped=skipped,
            speed_log=constants.speed_test_log_path,
            result_log=constants.result_log_path,
        )
    return grouped_results


def sort_channel_result(channel_data, result=None, filter_host=False, ipv6_support=True, cate=None, name=None):
    """
    Sort channel result
    """
    channel_result = defaultdict(lambda: defaultdict(list))
    categories = [cate] if cate else list(channel_data.keys())
    retain = retain_origin
    speed_lookup = get_speed_result
    sorter = get_sort_result
    unmatch_category = t("content.unmatch_channel")

    for c in categories:
        obj = channel_data.get(c, {}) or {}
        names = [name] if name else list(obj.keys())
        for n in names:
            values = obj.get(n) or []
            whitelist_result = []
            result_list = (result.get(c, {}).get(n, []) if result else [])

            if c == unmatch_category:
                seen_results = set()
                for item in values:
                    result_id = stable_result_id(
                        item.get("url", ""),
                        item.get("headers"),
                    )
                    if item.get("url") and result_id not in seen_results:
                        channel_result[c][n].append(item)
                        seen_results.add(result_id)
                continue

            if filter_host:
                merged_items = []
                for value in values:
                    origin = value.get("origin")
                    if origin in retain or (not ipv6_support and result and value.get("ipv_type") == "ipv6"):
                        whitelist_result.append(value)
                    else:
                        host = value.get("host")
                        merged = {**value, **(speed_lookup(host) or {})}
                        merged_items.append(merged)

                sorter_input = chain(result_list, merged_items) if merged_items else result_list
                total_result = whitelist_result + sorter(sorter_input, ipv6_support=ipv6_support)
            else:
                for value in values:
                    origin = value.get("origin")
                    if origin in retain or (not ipv6_support and result and value.get("ipv_type") == "ipv6"):
                        whitelist_result.append(value)

                total_result = whitelist_result + sorter(result_list, ipv6_support=ipv6_support)

            seen_results = set()
            for item in total_result:
                result_id = stable_result_id(
                    item.get("url", ""),
                    item.get("headers"),
                )
                if item.get("url") and result_id not in seen_results:
                    channel_result[c][n].append(item)
                    seen_results.add(result_id)

    return channel_result


def build_channel_statistic(cate, name, values):
    """
    Generate channel statistic
    """
    total = len(values)
    valid_items = [
        v for v in values
        if is_valid_speed_result(v)
    ]
    valid = len(valid_items)
    valid_rate = (valid / total * 100) if total > 0 else 0
    ipv4_count = len([v for v in values if v.get("ipv_type") == "ipv4"])
    ipv6_count = len([v for v in values if v.get("ipv_type") == "ipv6"])
    min_delay = min((v.get("delay") for v in values if (v.get("delay") or -1) != -1), default=-1)
    max_speed = max(
        (v.get("speed") for v in values if (v.get("speed") or 0) > 0 and not math.isinf(v.get("speed"))),
        default=0
    )
    avg_speed = sum((v.get("speed") or 0) for v in valid_items) / valid if valid > 0 else 0
    max_resolution = max(
        (v.get("resolution") for v in values if v.get("resolution")),
        key=lambda r: get_resolution_value(r),
        default="None"
    )
    video_codecs = [v.get('video_codec') for v in values if v.get('video_codec')]
    audio_codecs = [v.get('audio_codec') for v in values if v.get('audio_codec')]
    fps_values = [float(v.get('fps')) for v in values if
                  v.get('fps') is not None and isinstance(v.get('fps'), (int, float, str)) and str(
                      v.get('fps')).replace('.', '').isdigit()]
    most_video = Counter(video_codecs).most_common(1)
    most_audio = Counter(audio_codecs).most_common(1)
    most_video_str = most_video[0][0] if most_video else t('name.unknown')
    most_audio_str = most_audio[0][0] if most_audio else t('name.unknown')
    avg_fps = (sum(fps_values) / len(fps_values)) if fps_values else None
    return {
        "event": "channel.statistic",
        "category": cate,
        "name": name,
        "tested": total,
        "valid": valid,
        "valid_percent": round(valid_rate, 2),
        "ipv4": ipv4_count,
        "ipv6": ipv6_count,
        "min_delay_ms": min_delay if min_delay != -1 else None,
        "max_speed_mib_s": round(max_speed, 4),
        "avg_valid_speed_mib_s": round(avg_speed, 4),
        "max_resolution": None if max_resolution == "None" else max_resolution,
        "avg_fps": round(avg_fps, 2) if avg_fps is not None else None,
        "video_codec": most_video_str,
        "audio_codec": most_audio_str,
    }


def format_channel_statistic(record):
    fields = [
        f"{t('name.category')}: {record.get('category')}",
        f"{t('name.name')}: {record.get('name')}",
    ]
    if config.open_full_speed_test:
        fields.extend([
            f"{t('name.total')}: {record.get('tested', 0)}",
            f"{t('name.valid_percent')}: {record.get('valid_percent', 0):.2f}%",
        ])
    fields.extend([
        f"{t('name.valid')}: {record.get('valid', 0)}",
        f"IPv4: {record.get('ipv4', 0)}",
        f"IPv6: {record.get('ipv6', 0)}",
        f"{t('name.min_delay')}: {record.get('min_delay_ms') if record.get('min_delay_ms') is not None else '—'} ms",
        f"{t('name.max_speed')}: {record.get('max_speed_mib_s', 0):.2f} MiB/s",
        f"{t('name.average_speed')}: {record.get('avg_valid_speed_mib_s', 0):.2f} MiB/s",
        f"{t('name.max_resolution')}: {record.get('max_resolution') or '—'}",
        f"{t('name.avg_fps')}: {record.get('avg_fps') if record.get('avg_fps') is not None else t('name.unknown')}",
        f"{t('name.video_codec')}: {record.get('video_codec') or t('name.unknown')}",
        f"{t('name.audio_codec')}: {record.get('audio_codec') or t('name.unknown')}",
    ])
    return ", ".join(fields)


def generate_channel_statistic(logger, cate, name, values):
    """Compatibility wrapper for callers that still provide a standard logger."""
    record = build_channel_statistic(cate, name, values)
    content = format_channel_statistic(record)
    logger.info(content)
    return record


_WRITTEN_CONTENT_DIGESTS = {}


def process_write_content(
        path: str,
        data: CategoryChannelData,
        hls_url: str = None,
        open_empty_category: bool = False,
        ipv_type_prefer: list[str] = None,
        origin_type_prefer: list[str] = None,
        first_channel_name: str = None,
        enable_log: bool = False,
        is_last: bool = False,
        reporter=None,
):
    """
    Get channel write content
    :param path: write into path
    :param data: channel data
    :param hls_url: hls url
    :param open_empty_category: show empty category
    :param ipv_type_prefer: ipv type prefer
    :param origin_type_prefer: origin type prefer
    :param first_channel_name: the first channel name
    :param enable_log: enable log
    :param is_last: is last write
    """
    content = ""
    no_result_name = []
    first_cate = True
    result_data = defaultdict(list)
    custom_print.disable = not enable_log
    rtmp_type = ["hls"] if hls_url else []
    open_url_info = config.open_url_info
    unmatch_category = t("content.unmatch_channel")
    for cate, channel_obj in data.items():
        content += f"{'\n\n' if not first_cate else ''}{cate},#genre#"
        first_cate = False
        channel_obj_keys = channel_obj.keys()
        for i, name in enumerate(channel_obj_keys):
            info_list = data.get(cate, {}).get(name, [])
            channel_urls = _get_total_urls_cached(
                info_list,
                ipv_type_prefer,
                origin_type_prefer,
                rtmp_type,
                apply_limit=cate != unmatch_category,
            )
            result_data[name].extend(channel_urls)
            if not channel_urls:
                if open_empty_category:
                    no_result_name.append(name)
                continue
            for item in channel_urls:
                item_url = item["url"]
                extra_info = item.get("extra_info", "")
                if open_url_info and extra_info:
                    item_url = add_url_info(item_url, extra_info)
                total_item_url = f"{hls_url}/{item['id']}.m3u8" if hls_url else item_url
                content += f"\n{name},{total_item_url}"
    if open_empty_category and no_result_name and is_last:
        custom_print(f"\n{t("msg.no_result_channel")}")
        content += f"\n\n{t("content.no_result_channel")},#genre#"
        for i, name in enumerate(no_result_name):
            end_char = ", " if i < len(no_result_name) - 1 else ""
            custom_print(name, end=end_char)
            content += f"\n{name},url"
    render_hasher = hashlib.sha256(content.encode("utf-8"))
    for name, items in result_data.items():
        render_hasher.update(b"\x1d")
        render_hasher.update(name.encode("utf-8", errors="ignore"))
        render_hasher.update(b"\x1f")
        render_hasher.update(_build_total_urls_signature(items).encode("ascii"))
    render_hasher.update(
        repr((
            is_last,
            first_channel_name,
            config.open_epg,
            config.open_update_time,
            config.update_time_position,
            config.logo_url,
            config.logo_type,
            config.open_subscribe_logo,
            config.user_agent,
            config.cdn_url,
            get_public_url(),
        )).encode("utf-8")
    )
    render_signature = render_hasher.digest()
    m3u_path = os.path.splitext(path)[0] + ".m3u"
    if _WRITTEN_CONTENT_DIGESTS.get(path) == render_signature and os.path.exists(path) and os.path.exists(m3u_path):
        return False
    if config.open_update_time:
        update_time_item = next(
            (urls[0] for channel_obj in data.values()
             for info_list in channel_obj.values()
             if (urls := _get_total_urls_cached(
                info_list,
                ipv_type_prefer,
                origin_type_prefer,
                rtmp_type,
                apply_limit=True,
            ))),
            {"id": "id", "url": "url", "extra_info": ""}
        )
        now = get_datetime_now()
        update_time_item_url = update_time_item["url"]
        update_title = t("content.update_time") if is_last else t("content.update_running")
        update_time_extra_info = update_time_item.get("extra_info", "")
        if open_url_info and update_time_extra_info:
            update_time_item_url = add_url_info(update_time_item_url, update_time_extra_info)
        value = f"{hls_url}/{update_time_item["id"]}.m3u8" if hls_url else update_time_item_url
        if config.update_time_position == "top":
            content = f"{update_title},#genre#\n{now},{value}\n\n{content}"
        else:
            content += f"\n\n{update_title},#genre#\n{now},{value}"
    try:
        target_dir = os.path.dirname(path) or "."
        os.makedirs(target_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=target_dir,
                                         prefix=os.path.basename(path) + ".tmp.") as tmpf:
            tmpf.write(content)
            tmp_path = tmpf.name
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o644)
        except Exception:
            pass
    except Exception:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            if reporter:
                reporter.error(
                    "output.file_write_failed",
                    t("msg.write_error").format(info=e),
                    phase="output",
                    path=path,
                    error_type=type(e).__name__,
                )
            else:
                print(t("msg.write_error").format(info=e), flush=True)
            return
    try:
        convert_to_m3u(path, first_channel_name, data=result_data, content=content)
        _WRITTEN_CONTENT_DIGESTS[path] = render_signature
    except Exception as e:
        message = t("msg.write_error").format(info=f"convert m3u error: {e}")
        if reporter:
            reporter.error(
                "output.m3u_conversion_failed",
                message,
                phase="output",
                path=path,
                error_type=type(e).__name__,
            )
        else:
            print(message, flush=True)
    return True


def write_channel_to_file(
        data,
        ipv6=False,
        first_channel_name=None,
        skip_print=False,
        is_last=False,
        reporter=None,
):
    """
    Write channel to file
    """
    try:
        if not skip_print:
            print(t("msg.writing_result"), flush=True)
        open_empty_category = config.open_empty_category
        ipv_type_prefer = list(config.ipv_type_prefer)
        if any(pref == "auto" for pref in ipv_type_prefer):
            ipv_type_prefer = ["ipv6", "ipv4"] if ipv6 else ["ipv4", "ipv6"]
        origin_type_prefer = config.origin_type_prefer
        hls_url = f"{get_public_url()}/hls"
        file_list = [
            {"path": config.final_file, "enable_log": True},
            {"path": constants.ipv4_result_path, "ipv_type_prefer": ["ipv4"]},
            {"path": constants.ipv6_result_path, "ipv_type_prefer": ["ipv6"]}
        ]
        if config.rtmp_available and not os.getenv("GITHUB_ACTIONS"):
            file_list += [
                {"path": constants.hls_result_path, "hls_url": hls_url},
                {
                    "path": constants.hls_ipv4_result_path,
                    "hls_url": hls_url,
                    "ipv_type_prefer": ["ipv4"]
                },
                {
                    "path": constants.hls_ipv6_result_path,
                    "hls_url": hls_url,
                    "ipv_type_prefer": ["ipv6"]
                },
            ]
            rtmp_rows = {}
            unmatch_category = t("content.unmatch_channel")
            for file in file_list:
                if not file.get("hls_url"):
                    continue
                file_ipv_type_prefer = file.get("ipv_type_prefer", ipv_type_prefer)
                for cate, channel_obj in data.items():
                    for info_list in channel_obj.values():
                        channel_urls = _get_total_urls_cached(
                            info_list,
                            file_ipv_type_prefer,
                            origin_type_prefer,
                            ["hls"],
                            apply_limit=cate != unmatch_category,
                        )
                        for item in channel_urls:
                            item_id = item.get("id")
                            if item_id is not None:
                                rtmp_rows[str(item_id)] = item
        hls_changed = False
        changed_paths = []
        for file in file_list:
            target_dir = os.path.dirname(file["path"])
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            changed = process_write_content(
                path=file["path"],
                data=data,
                hls_url=file.get("hls_url"),
                open_empty_category=open_empty_category,
                ipv_type_prefer=file.get("ipv_type_prefer", ipv_type_prefer),
                origin_type_prefer=origin_type_prefer,
                first_channel_name=first_channel_name,
                enable_log=file.get("enable_log", False),
                is_last=is_last,
                reporter=reporter,
            )
            if file.get("hls_url") and changed:
                hls_changed = True
            if changed:
                changed_paths.append(file["path"])
        if hls_changed:
            try:
                sync_result_data(constants.rtmp_data_path, rtmp_rows.values())
            except Exception as e:
                if reporter:
                    reporter.error(
                        "output.snapshot_sync_failed",
                        t("msg.write_error").format(info=e),
                        phase="output",
                        error_type=type(e).__name__,
                    )
                else:
                    print(t("msg.write_error").format(info=e), flush=True)
        if not skip_print:
            print(t("msg.write_success"), flush=True)
        if reporter and is_last:
            reporter.info(
                "output.written",
                t("log.output_written").format(count=len(changed_paths)),
                phase="output",
                changed_count=len(changed_paths),
                changed_paths=changed_paths,
                final_file=config.final_file,
            )
    except Exception as e:
        if reporter:
            reporter.error(
                "output.write_failed",
                t("msg.write_error").format(info=e),
                phase="output",
                error_type=type(e).__name__,
            )
        else:
            print(t("msg.write_error").format(info=e), flush=True)
