"""`streamlit run analytics.py` должен поднять FastAPI, а не пустую страницу."""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from streamlit.web.server.app_discovery import discover_asgi_app


ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_discovers_fastapi_app():
    found = discover_asgi_app(ROOT / "analytics.py")
    assert found.is_asgi_app
    assert found.import_string == "analytics:app"


def test_streamlit_run_serves_fastapi():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///:memory:"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "analytics.py",
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 25
        health = None
        last_error = None
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                    ctype = resp.headers.get("content-type") or ""
                    body = resp.read().decode()
                    if resp.status == 200 and "application/json" in ctype:
                        health = body
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
            time.sleep(0.25)
        output = ""
        if health is None and proc.stdout is not None:
            proc.terminate()
            try:
                output = proc.stdout.read().decode(errors="replace")
            except Exception:
                output = ""
        assert health == '{"ok":true}', (health, last_error, output, proc.poll())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            html = resp.read().decode()
        assert "Аналитика списаний" in html
        assert 'id="root"' not in html
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
