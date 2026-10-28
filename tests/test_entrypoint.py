"""Боевой `streamlit run analytics.py` должен отдать порт FastAPI, а не белую страницу."""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def test_held_port_hands_off_to_fastapi():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["DATABASE_URL"] = "sqlite:///:memory:"
    code = (
        "import os, socket, threading, analytics\n"
        "sock = socket.socket()\n"
        "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "sock.bind(('0.0.0.0', int(os.environ['PORT'])))\n"
        "sock.listen(8)\n"
        "threading.Thread(target=analytics.handoff_to_fastapi, daemon=True).start()\n"
        "threading.Event().wait(40)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 20
        health = None
        last_error = None
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                    health = resp.read().decode()
                    if resp.status == 200 and "application/json" in (resp.headers.get("content-type") or ""):
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
            time.sleep(0.25)
        output = ""
        if health is None:
            proc.terminate()
            output = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
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
