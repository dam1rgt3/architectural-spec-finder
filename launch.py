"""Open the existing full local library, starting its loopback server when needed."""

import json
import subprocess
import sys
import time
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

from sync import ROOT

URL = "http://127.0.0.1:8769"


def running():
    try:
        with urlopen(URL + "/api/stats", timeout=1) as r:
            result = json.load(r)
        return result.get("demo") is False and "passages" in result
    except (URLError, ValueError, TimeoutError, OSError):
        return False


if __name__ == "__main__":
    if not running():
        log = ROOT / "data" / "library" / "server.log"
        with log.open("ab") as output:
            child = subprocess.Popen(
                [sys.executable, str(ROOT / "app.py")],
                cwd=ROOT,
                stdout=output,
                stderr=output,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        for _ in range(100):
            if running():
                break
            if child.poll() is not None:
                break
            time.sleep(0.2)
        if not running():
            sys.exit("Could not start Spec Finder. See " + str(log))
    webbrowser.open(URL)
