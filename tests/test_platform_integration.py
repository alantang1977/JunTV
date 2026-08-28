import sys
import types
import unittest
from unittest.mock import patch

from desktop_ui.platform_integration import suspend_macos_window_flush


class MacOSWindowFlushTests(unittest.TestCase):
    def test_non_macos_context_does_not_access_native_window(self):
        class Window:
            def winId(self):
                raise AssertionError("winId should not be accessed")

        with patch.object(sys, "platform", "linux"):
            with suspend_macos_window_flush(Window()):
                pass

    def test_macos_context_flushes_once_after_layout_changes(self):
        calls = []

        class NativeWindow:
            def disableFlushWindow(self):
                calls.append("disable")

            def enableFlushWindow(self):
                calls.append("enable")

            def flushWindow(self):
                calls.append("flush")

        native_window = NativeWindow()
        native_view = types.SimpleNamespace(window=lambda: native_window)
        objc = types.SimpleNamespace(
            objc_object=lambda c_void_p: native_view,
        )
        window = types.SimpleNamespace(winId=lambda: 123)

        with patch.object(sys, "platform", "darwin"), patch.dict(
            sys.modules, {"objc": objc}
        ):
            with suspend_macos_window_flush(window):
                calls.append("layout")

        self.assertEqual(calls, ["disable", "layout", "enable", "flush"])

    def test_macos_context_restores_flushing_after_error(self):
        calls = []

        class NativeWindow:
            def disableFlushWindow(self):
                calls.append("disable")

            def enableFlushWindow(self):
                calls.append("enable")

            def flushWindow(self):
                calls.append("flush")

        native_window = NativeWindow()
        objc = types.SimpleNamespace(
            objc_object=lambda c_void_p: types.SimpleNamespace(
                window=lambda: native_window
            ),
        )
        window = types.SimpleNamespace(winId=lambda: 123)

        with patch.object(sys, "platform", "darwin"), patch.dict(
            sys.modules, {"objc": objc}
        ):
            with self.assertRaisesRegex(RuntimeError, "layout failed"):
                with suspend_macos_window_flush(window):
                    raise RuntimeError("layout failed")

        self.assertEqual(calls, ["disable", "enable", "flush"])


if __name__ == "__main__":
    unittest.main()
