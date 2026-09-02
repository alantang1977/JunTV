import ctypes
import sys
from contextlib import contextmanager


@contextmanager
def suspend_macos_window_flush(window):
    """Publish a group of Qt layout changes as one macOS window frame."""
    if sys.platform != "darwin":
        yield
        return
    try:
        import objc

        view = objc.objc_object(c_void_p=int(window.winId()))
        native_window = view.window()
        native_window.disableFlushWindow()
    except (AttributeError, ImportError, OSError, TypeError):
        yield
        return
    try:
        yield
    finally:
        native_window.enableFlushWindow()
        native_window.flushWindow()


def set_macos_activation_policy(accessory: bool):
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send_object = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(("objc_msgSend", objc))
        send_integer = ctypes.CFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long
        )(("objc_msgSend", objc))
        application_class = objc.objc_getClass(b"NSApplication")
        shared_application = objc.sel_registerName(b"sharedApplication")
        set_policy = objc.sel_registerName(b"setActivationPolicy:")
        application = send_object(application_class, shared_application)
        return bool(send_integer(application, set_policy, 1 if accessory else 0))
    except (AttributeError, OSError):
        return False
