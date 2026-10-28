"""Точка входа — uvicorn server:app. Streamlit к запуску не нужен."""

import ast
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_analytics_is_not_an_asgi_app():
    source = (ROOT / "analytics.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = set()
    for node in tree.body:
        targets = getattr(node, "targets", None)
        if targets is None and isinstance(getattr(node, "target", None), ast.Name):
            targets = [node.target]
        if not targets:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assigned.add(target.id)
    assert "app" not in assigned
    assert "FastAPI" not in source
    assert "streamlit" not in source.lower()


def test_uvicorn_serves_site(tmp_path):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'losses.db'}"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
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
                    health = resp.read().decode()
                    if resp.status == 200:
                        break
                    health = None
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
