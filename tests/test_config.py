import configparser
import os
import tempfile
import unittest
from unittest.mock import patch

from utils.config import CONFIG_SCHEMA, ConfigManager, ConfigValidationError
from utils.tools import get_public_url


class ConfigValidationTests(unittest.TestCase):
    def _write(self, path, content):
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)

    def _manager(self, default_content, user_content=None, environ=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        default_path = os.path.join(temp_dir.name, "config.ini")
        user_path = os.path.join(temp_dir.name, "user_config.ini")
        self._write(default_path, default_content)
        if user_content is not None:
            self._write(user_path, user_content)
        manager = ConfigManager(
            default_config_path=default_path,
            user_config_path=user_path,
            environ={} if environ is None else environ,
        )
        return manager, default_path, user_path

    def test_shipped_default_config_is_covered_by_schema(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read("config/config.ini", encoding="utf-8")

        unknown = set(parser["Settings"]) - set(CONFIG_SCHEMA)

        self.assertEqual(unknown, set())
        ConfigManager(
            default_config_path="config/config.ini",
            user_config_path=os.path.join(
                tempfile.gettempdir(),
                "iptv-api-missing-user-config.ini",
            ),
            environ={},
        )

    def test_invalid_values_and_unknown_key_are_reported_together(self):
        default = """\
[Settings]
open_update = True
app_port = 5180
update_mode = interval
"""
        user = """\
[Settings]
open_update = Ture
app_port = 70000
update_mode = timer
open_udpate = False
"""
        with self.assertRaises(ConfigValidationError) as raised:
            self._manager(default, user)

        message = str(raised.exception)
        self.assertEqual(len(raised.exception.issues), 4)
        self.assertIn("open_update = 'Ture'", message)
        self.assertIn('是否想填写 "True"', message)
        self.assertIn("app_port = '70000'", message)
        self.assertIn("不能大于 65535", message)
        self.assertIn("update_mode = 'timer'", message)
        self.assertIn("open_udpate", message)
        self.assertIn('是否想填写 "open_update"', message)
        self.assertIn("user_config.ini", message)

    def test_environment_override_is_validated_and_identified(self):
        default = """\
[Settings]
open_update = True
"""
        with self.assertRaises(ConfigValidationError) as raised:
            self._manager(default, environ={"OPEN_UPDATE": "Ture"})

        message = str(raised.exception)
        self.assertIn("环境变量 OPEN_UPDATE", message)
        self.assertIn("open_update = 'Ture'", message)

    def test_public_port_environment_variable_is_validated(self):
        default = """\
[Settings]
app_port = 5180
"""
        with self.assertRaises(ConfigValidationError) as raised:
            self._manager(default, environ={"PUBLIC_PORT": "outside"})

        message = str(raised.exception)
        self.assertIn("环境变量 PUBLIC_PORT", message)
        self.assertIn("应为 1～65535 的整数", message)

    def test_legacy_nginx_http_port_remains_effective_for_old_user_config(self):
        manager, _, _ = self._manager(
            """\
[Settings]
service_port = 8080
nginx_http_port = 8080
""",
            """\
[Settings]
nginx_http_port = 9090
""",
        )

        self.assertEqual(manager.service_port, 9090)
        self.assertEqual(manager.nginx_http_port, 9090)

    def test_service_port_takes_precedence_when_explicitly_configured(self):
        manager, _, _ = self._manager(
            """\
[Settings]
service_port = 8080
nginx_http_port = 8080
""",
            """\
[Settings]
service_port = 7070
nginx_http_port = 9090
""",
        )

        self.assertEqual(manager.service_port, 7070)
        self.assertEqual(manager.nginx_http_port, 7070)

    def test_legacy_nginx_http_environment_override_is_compatible(self):
        manager, _, _ = self._manager(
            """\
[Settings]
service_port = 8080
nginx_http_port = 8080
""",
            environ={"NGINX_HTTP_PORT": "9090"},
        )

        self.assertEqual(manager.service_port, 9090)
        self.assertEqual(
            manager.environment_override_name("service_port"),
            "NGINX_HTTP_PORT",
        )

    def test_service_port_environment_override_takes_precedence(self):
        manager, _, _ = self._manager(
            """\
[Settings]
service_port = 8080
nginx_http_port = 8080
""",
            environ={
                "SERVICE_PORT": "7070",
                "NGINX_HTTP_PORT": "9090",
            },
        )

        self.assertEqual(manager.service_port, 7070)
        self.assertEqual(
            manager.environment_override_name("service_port"),
            "SERVICE_PORT",
        )

    def test_public_url_overrides_legacy_generated_address(self):
        manager, _, _ = self._manager(
            """\
[Settings]
public_url = https://iptv.example.com/base/
public_scheme = http
public_domain = legacy.example
service_port = 8080
nginx_http_port = 8080
"""
        )

        self.assertEqual(manager.public_url, "https://iptv.example.com/base")
        with patch("utils.tools.config", manager):
            self.assertEqual(get_public_url(), "https://iptv.example.com/base")
            self.assertEqual(get_public_url(5180), "http://legacy.example:5180")

    def test_empty_public_url_environment_keeps_configured_address(self):
        manager, _, _ = self._manager(
            """\
[Settings]
public_url = https://iptv.example.com
""",
            environ={"PUBLIC_URL": ""},
        )

        self.assertEqual(manager.public_url, "https://iptv.example.com")
        self.assertIsNone(manager.environment_override_name("public_url"))

    def test_nonempty_public_url_environment_overrides_configured_address(self):
        manager, _, _ = self._manager(
            """\
[Settings]
public_url = https://config.example.com
""",
            environ={"PUBLIC_URL": "https://env.example.com/base/"},
        )

        self.assertEqual(manager.public_url, "https://env.example.com/base")
        self.assertEqual(
            manager.environment_override_name("public_url"),
            "PUBLIC_URL",
        )

    def test_invalid_public_url_is_rejected(self):
        with self.assertRaises(ConfigValidationError) as raised:
            self._manager(
                """\
[Settings]
public_url = iptv.example.com?token=secret
"""
            )

        self.assertIn("完整的 HTTP(S) 地址", str(raised.exception))

    def test_rtmp_listener_ports_must_be_distinct(self):
        with self.assertRaises(ConfigValidationError) as raised:
            self._manager(
                """\
[Settings]
open_rtmp = True
app_port = 8080
service_port = 8080
nginx_http_port = 8080
nginx_rtmp_port = 1935
"""
            )

        self.assertIn("端口不能相同", str(raised.exception))

    def test_special_formats_and_cross_field_constraints_are_validated(self):
        default = """\
[Settings]
update_times = 25:00
time_zone = Mars/Olympus
min_resolution = 3840x2160
max_resolution = 1280x720
resolution_speed_map = 1920x1080:fast
sort_by = speed,quality
"""
        with self.assertRaises(ConfigValidationError) as raised:
            self._manager(default)

        message = str(raised.exception)
        self.assertIn("应为 HH:MM", message)
        self.assertIn("不是有效的 IANA 时区", message)
        self.assertIn("不能小于 min_resolution", message)
        self.assertIn("速率应为不小于 0 的数字", message)
        self.assertIn('"quality" 无效', message)

    def test_output_limit_supersedes_legacy_setting_and_target_follows_it(self):
        manager, _, _ = self._manager(
            """\
[Settings]
urls_limit = 5
output_urls_limit = 8
speed_test_target = 0
"""
        )

        self.assertEqual(manager.output_urls_limit, 8)
        self.assertEqual(manager.urls_limit, 8)
        self.assertEqual(manager.speed_test_target, 8)

    def test_legacy_user_output_limit_is_preserved_during_migration(self):
        manager, _, _ = self._manager(
            """\
[Settings]
output_urls_limit = 5
""",
            """\
[Settings]
urls_limit = 3
""",
        )

        self.assertEqual(manager.output_urls_limit, 3)
        self.assertEqual(manager.speed_test_target, 3)

    def test_speed_test_mode_migrates_legacy_switches_and_accepts_explicit_mode(self):
        manager, _, _ = self._manager(
            """\
[Settings]
speed_test_mode = quick
open_speed_test = True
open_full_speed_test = False
""",
            """\
[Settings]
open_speed_test = False
""",
        )
        self.assertEqual(manager.speed_test_mode, "manual")

        explicit, _, _ = self._manager(
            """\
[Settings]
speed_test_mode = manual
open_speed_test = True
"""
        )
        self.assertEqual(explicit.speed_test_mode, "manual")

    def test_save_validates_before_overwriting_user_config(self):
        manager, _, user_path = self._manager(
            """\
[Settings]
open_update = True
""",
            """\
[Settings]
open_update = True
""",
        )
        manager.set("Settings", "open_update", "Ture")

        with self.assertRaises(ConfigValidationError):
            manager.save()

        with open(user_path, encoding="utf-8") as file:
            self.assertIn("open_update = True", file.read())


if __name__ == "__main__":
    unittest.main()
