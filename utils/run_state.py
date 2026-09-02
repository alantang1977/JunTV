import json
import os
import tempfile
import time

import utils.constants as constants
from utils.config import resource_path


def _path():
    return resource_path(constants.run_state_path, persistent=True)


def read_run_state():
    path = _path()
    try:
        with open(path, "r", encoding="utf-8") as file:
            state = json.load(file)
        return state if isinstance(state, dict) else {"status": "never_run"}
    except (OSError, ValueError, TypeError):
        return {"status": "never_run"}


def write_run_state(status, **data):
    path = _path()
    directory = os.path.dirname(path) or "."
    state = {"status": status, "updated_at": time.time(), **data}
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="run_state.", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
