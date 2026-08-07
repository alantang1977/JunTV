import os
import sys
import time
import ipaddress
import socket
import threading

sys.path.append(os.path.dirname(sys.path[0]))
from flask import Flask, cli as flask_cli, send_from_directory, make_response, request, jsonify, Response
from utils.tools import get_result_file_content, resource_path, get_public_url, get_version_info
from utils.config import config
import utils.constants as constants
import atexit
from service.rtmp import start_rtmp_service, stop_rtmp_service, app_rtmp_url, hls_temp_path, STREAMS_LOCK, \
    hls_running_streams, start_hls_to_rtmp, start_hls_to_rtmp_async, hls_last_access, HLS_IDLE_TIMEOUT, \
    HLS_WAIT_TIMEOUT, HLS_WAIT_INTERVAL, stop_all_streams, stop_stream, stream_capacity_snapshot
import logging
from utils.i18n import t
from utils.rtmp_runtime import install_rtmp_runtime, rtmp_runtime_status
from utils.run_state import read_run_state
from utils.version_check import log_new_version_if_available, start_version_log_monitor
from werkzeug.utils import secure_filename
import mimetypes

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


def _start_version_check():
    if os.getenv("IPTV_API_SKIP_VERSION_CHECK"):
        return
    version = str(get_version_info().get("version") or "0")

    def check_and_monitor():
        log_new_version_if_available(version)
        start_version_log_monitor(version)

    threading.Thread(target=check_and_monitor, name="service-version-check", daemon=True).start()


_start_version_check()


def _service_port_is_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.2):
            return True
    except OSError:
        return False


@app.route("/")
def show_index():
    return get_result_file_content(
        path=config.final_file,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(resource_path(''), 'favicon.ico',
                               mimetype='image/vnd.microsoft.icon')


@app.route('/logo/<path:filename>')
def show_logo(filename):
    if not filename:
        return jsonify({"error": "filename required"}), 400

    try:
        safe_name = secure_filename(filename, allow_unicode=True)
    except TypeError:
        safe_name = os.path.basename(filename)
        safe_name = safe_name.replace('/', '').replace('\\', '')
        safe_name = safe_name.lstrip('.')

    if not safe_name:
        return jsonify({"error": "filename required"}), 400

    logo_dir = resource_path(constants.channel_logo_path)
    file_path = os.path.join(logo_dir, safe_name)

    if not os.path.exists(file_path):
        return jsonify({"error": "logo not found"}), 404

    mime_type, _ = mimetypes.guess_type(safe_name)
    return send_from_directory(logo_dir, safe_name, mimetype=mime_type or 'application/octet-stream')


@app.route("/txt")
def show_txt():
    return get_result_file_content(path=config.final_file, file_type="txt")


@app.route("/ipv4/txt")
def show_ipv4_txt():
    return get_result_file_content(path=constants.ipv4_result_path, file_type="txt")


@app.route("/ipv6/txt")
def show_ipv6_txt():
    return get_result_file_content(path=constants.ipv6_result_path, file_type="txt")


@app.route("/hls")
def show_hls():
    return get_result_file_content(path=constants.hls_result_path,
                                   file_type="m3u" if config.open_m3u_result else "txt")


@app.route("/hls/txt")
def show_hls_txt():
    return get_result_file_content(path=constants.hls_result_path, file_type="txt")


@app.route("/hls/ipv4/txt")
def show_hls_ipv4_txt():
    return get_result_file_content(path=constants.hls_ipv4_result_path, file_type="txt")


@app.route("/hls/ipv6/txt")
def show_hls_ipv6_txt():
    return get_result_file_content(path=constants.hls_ipv6_result_path, file_type="txt")


@app.route("/m3u")
def show_m3u():
    return get_result_file_content(path=config.final_file, file_type="m3u")


@app.route("/hls/m3u")
def show_hls_m3u():
    return get_result_file_content(path=constants.hls_result_path, file_type="m3u")


@app.route("/ipv4/m3u")
def show_ipv4_m3u():
    return get_result_file_content(path=constants.ipv4_result_path, file_type="m3u")


@app.route("/ipv4")
def show_ipv4_result():
    return get_result_file_content(
        path=constants.ipv4_result_path,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/hls/ipv4")
def show_hls_ipv4():
    return get_result_file_content(
        path=constants.hls_ipv4_result_path,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/ipv6/m3u")
def show_ipv6_m3u():
    return get_result_file_content(path=constants.ipv6_result_path, file_type="m3u")


@app.route("/ipv6")
def show_ipv6_result():
    return get_result_file_content(
        path=constants.ipv6_result_path,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/hls/ipv6")
def show_hls_ipv6():
    return get_result_file_content(
        path=constants.hls_ipv6_result_path,
        file_type="m3u" if config.open_m3u_result else "txt"
    )


@app.route("/hls/ipv4/m3u")
def show_hls_ipv4_m3u():
    return get_result_file_content(path=constants.hls_ipv4_result_path, file_type="m3u")


@app.route("/hls/ipv6/m3u")
def show_hls_ipv6_m3u():
    return get_result_file_content(path=constants.hls_ipv6_result_path, file_type="m3u")


@app.route("/content")
def show_content():
    return get_result_file_content(
        path=config.final_file,
        file_type="m3u" if config.open_m3u_result else "txt",
        show_content=True
    )


@app.route("/epg/epg.xml")
def show_epg():
    return get_result_file_content(path=constants.epg_result_path, file_type="xml", show_content=False)


@app.route("/epg/epg.gz")
def show_epg_gz():
    return get_result_file_content(path=constants.epg_gz_result_path, file_type="gz", show_content=False)


@app.route("/log/result")
def show_result_log():
    return _show_log_file(constants.result_log_path, constants.result_jsonl_path)


@app.route("/log/speed-test")
def show_speed_log():
    return _show_log_file(constants.speed_test_log_path, constants.speed_test_jsonl_path)


@app.route("/log/statistic")
def show_statistic_log():
    return _show_log_file(constants.statistic_log_path, constants.statistic_jsonl_path)


@app.route("/log/unmatch")
def show_unmatch_log():
    return _show_log_file(constants.unmatch_log_path, constants.unmatch_jsonl_path)


def _show_log_file(text_path, jsonl_path):
    use_jsonl = request.args.get("format", "").lower() in {"json", "jsonl", "ndjson"}
    path = jsonl_path if use_jsonl else text_path
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read()
    else:
        state = read_run_state()
        status = state.get("status", "never_run")
        response = jsonify({
            "status": status,
            "message": t({
                "never_run": "msg.log_empty_never",
                "running": "msg.log_empty_running",
                "completed_empty": "msg.log_empty_after_run",
                "failed": "msg.log_empty_failed",
                "cancelled": "msg.log_empty_cancelled",
            }.get(status, "msg.log_empty")),
        })
        response.status_code = {
            "never_run": 404,
            "running": 202,
            "completed_empty": 404,
            "failed": 503,
            "cancelled": 409,
        }.get(status, 404)
        return response
    response = make_response(content)
    response.mimetype = "application/x-ndjson" if use_jsonl else "text/plain"
    return response


@app.route('/hls_proxy/<channel_id>', methods=['GET'])
def hls_proxy(channel_id):
    if not channel_id:
        return jsonify({t("name.error"): t("msg.error_channel_id_required")}), 400

    channel_file = f'{channel_id}.m3u8'
    m3u8_path = os.path.join(hls_temp_path, channel_file)

    need_start = False
    with STREAMS_LOCK:
        proc = hls_running_streams.get(channel_id)
        if not proc or proc.poll() is not None:
            need_start = True
            if channel_id in hls_running_streams:
                hls_running_streams.pop(channel_id, None)

    if need_start:
        host = f"{app_rtmp_url}/hls"
        client_ua = request.headers.get('User-Agent') if request and hasattr(request, 'headers') else None
        print(f"▶️ {client_ua}")
        process = start_hls_to_rtmp(host, channel_id, client_user_agent=client_ua)
        if process is None:
            capacity = stream_capacity_snapshot()
            occupied = set(capacity["active_streams"]) | set(capacity["starting_streams"])
            if channel_id not in occupied and capacity["available_slots"] == 0:
                return jsonify({
                    t("name.error"): t("msg.rtmp_capacity_reached").format(
                        limit=capacity["max_streams"],
                        active=capacity["active_count"],
                        starting=capacity["starting_count"],
                    ),
                    "error_code": "capacity_reached",
                    **capacity,
                }), 409

    hls_min_segments = 3
    waited = 0.0
    while waited < HLS_WAIT_TIMEOUT:
        if os.path.exists(m3u8_path):
            try:
                with open(m3u8_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                segment_count = content.count('#EXTINF')
                ends_with_discont = content.rstrip().endswith('#EXT-X-DISCONTINUITY')
                if segment_count >= hls_min_segments and not ends_with_discont:
                    break
            except Exception as e:
                print(t("msg.error_channel_id_m3u8_read_info").format(channel_id=channel_id, info=e))
        time.sleep(HLS_WAIT_INTERVAL)
        waited += HLS_WAIT_INTERVAL

    if not os.path.exists(m3u8_path):
        return jsonify({t("name.error"): t("msg.m3u8_hls_not_ready")}), 503

    try:
        with open(m3u8_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        print(t("msg.error_channel_id_m3u8_read_info").format(channel_id=channel_id, info=e))
        return jsonify({t("name.error"): t("msg.error_m3u8_read")}), 500

    now = time.time()
    with STREAMS_LOCK:
        hls_last_access[channel_id] = now

    return Response(data, mimetype='application/vnd.apple.mpegurl')


@app.post('/on_done')
def on_done():
    form = request.form
    channel_id = form.get('name', '')

    print(t("msg.rtmp_on_done").format(channel_id=channel_id))
    return ''


def _local_request():
    remote = request.remote_addr or ""
    forwarded = request.headers.get("X-Real-IP") if remote in {"127.0.0.1", "::1"} else None
    try:
        return ipaddress.ip_address(forwarded or remote).is_loopback
    except ValueError:
        return False


@app.post('/api/rtmp/streams/<channel_id>/<action>')
def control_rtmp_stream(channel_id, action):
    if not _local_request():
        return jsonify({t("name.error"): t("msg.api_local_only")}), 403
    if action not in {"start", "stop", "restart"}:
        return jsonify({t("name.error"): t("msg.invalid_stream_action")}), 400
    if action in {"stop", "restart"}:
        stop_stream(channel_id)
    if action in {"start", "restart"}:
        host = f"{app_rtmp_url}/hls"
        result = start_hls_to_rtmp_async(host, channel_id)
        if not result["accepted"]:
            result.update({
                "channel_id": channel_id,
                "action": action,
                "error": t("msg.rtmp_capacity_reached").format(
                    limit=result["max_streams"],
                    active=result["active_count"],
                    starting=result["starting_count"],
                ),
                "error_code": "capacity_reached",
            })
            return jsonify(result), 409
        return jsonify({"channel_id": channel_id, "action": action, **result}), 202
    return jsonify({"channel_id": channel_id, "action": action, "accepted": True}), 202


@app.get('/api/rtmp/runtime')
def rtmp_runtime():
    if not _local_request():
        return jsonify({t("name.error"): t("msg.api_local_only")}), 403
    now = time.time()
    with STREAMS_LOCK:
        streams = {
            channel_id: {
                "last_access": last_access,
                "idle_timeout": HLS_IDLE_TIMEOUT,
                "idle_remaining": max(0, HLS_IDLE_TIMEOUT - (now - last_access)),
            }
            for channel_id, last_access in hls_last_access.items()
        }
    return jsonify({
        "sampled_at": now,
        "idle_timeout": HLS_IDLE_TIMEOUT,
        "streams": streams,
        **stream_capacity_snapshot(),
    })


@app.post('/api/rtmp/shutdown')
def shutdown_rtmp_runtime():
    if not _local_request():
        return jsonify({t("name.error"): t("msg.api_local_only")}), 403
    stop_all_streams()
    stop_rtmp_service()
    return jsonify({"stopped": True})


def _prompt_rtmp_install():
    status = rtmp_runtime_status()
    if sys.platform != "darwin" or not config.open_rtmp or status.get("available") or not sys.stdin.isatty():
        return
    try:
        answer = input(t("msg.rtmp_install_prompt")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in {"y", "yes"}:
        print(t("msg.rtmp_install_cancelled"))
        return
    print(t("msg.rtmp_installing"))
    result = install_rtmp_runtime(lambda content: print(content, end="", flush=True))
    if result.get("available"):
        print(t("msg.rtmp_install_success"))
    else:
        print(t("msg.rtmp_install_failed").format(
            info=result.get("output") or t(f"msg.rtmp_{result.get('error_code')}", result.get("error_code"))
        ))


def run_service(prompt_for_install=True):
    try:
        if not os.getenv("GITHUB_ACTIONS"):
            if _service_port_is_open(config.app_port):
                return
            if prompt_for_install:
                _prompt_rtmp_install()
            rtmp_started = False
            if config.rtmp_available and sys.platform in {"win32", "darwin"}:
                rtmp_started = start_rtmp_service()
                atexit.register(stop_rtmp_service)
            public_url = (
                get_public_url()
                if config.public_url
                else get_public_url(
                    config.service_port if rtmp_started else config.app_port
                )
            )
            mode = [t("name.direct_connection")]
            if rtmp_started:
                mode.append(t("name.push_streaming"))
            for m in mode:
                if m == t("name.push_streaming"):
                    print(t("msg.rtmp_full_api").format(mode=m, api=f"{public_url}/hls"))
                else:
                    print(t("msg.full_api").format(mode=m, api=public_url))
            flask_cli.show_server_banner = lambda *args, **kwargs: None
            app.run(host="0.0.0.0", port=config.app_port, use_reloader=False)
    except Exception as e:
        print(t("msg.error_service_start_failed").format(info=e))


if __name__ == "__main__":
    run_service()
