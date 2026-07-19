import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "deploy_modal.py"


def _load_deploy_module(monkeypatch):
    assert DEPLOY_SCRIPT.is_file()
    captured = {}

    class FakeImage:
        @classmethod
        def debian_slim(cls, **kwargs):
            captured["image"] = [("debian_slim", kwargs)]
            return cls()

        def uv_sync(self, **kwargs):
            captured["image"].append(("uv_sync", kwargs))
            return self

        def add_local_dir(self, local_path, remote_path, **kwargs):
            captured["image"].append(
                (
                    "add_local_dir",
                    {"local_path": local_path, "remote_path": remote_path, **kwargs},
                )
            )
            return self

    class FakeApp:
        def __init__(self, name):
            captured["app_name"] = name

        def function(self, **kwargs):
            captured["function"] = kwargs

            def decorator(function):
                captured.setdefault("decorator_order", []).append("function")
                return function

            return decorator

    def concurrent(**kwargs):
        captured["concurrent"] = kwargs

        def decorator(function):
            captured.setdefault("decorator_order", []).append("concurrent")
            return function

        return decorator

    def web_server(port, **kwargs):
        captured["web_server"] = {"port": port, **kwargs}

        def decorator(function):
            captured.setdefault("decorator_order", []).append("web_server")
            return function

        return decorator

    fake_modal = SimpleNamespace(
        App=FakeApp,
        Image=FakeImage,
        concurrent=concurrent,
        web_server=web_server,
    )
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    spec = importlib.util.spec_from_file_location("papersqueeze_modal_deploy", DEPLOY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, captured


def test_modal_deploy_uses_bounded_scale_to_zero_resources(monkeypatch):
    _, captured = _load_deploy_module(monkeypatch)

    assert captured["app_name"] == "papersqueeze"
    assert captured["image"] == [
        ("debian_slim", {"python_version": "3.12"}),
        (
            "uv_sync",
            {
                "uv_project_dir": ROOT,
                "frozen": True,
                "uv_version": "0.9.28",
            },
        ),
        (
            "add_local_dir",
            {
                "local_path": ROOT / "src",
                "remote_path": "/app/src",
                "copy": True,
            },
        ),
    ]
    assert captured["function"]["cpu"] == 2.0
    assert captured["function"]["memory"] == 2048
    assert "ephemeral_disk" not in captured["function"]
    assert captured["function"]["min_containers"] == 0
    assert captured["function"]["max_containers"] == 1
    assert captured["function"]["scaledown_window"] == 60
    assert captured["function"]["timeout"] == 600
    assert captured["concurrent"] == {"max_inputs": 8}
    assert captured["web_server"] == {"port": 8080, "startup_timeout": 120}
    assert captured["decorator_order"] == ["web_server", "concurrent", "function"]


def test_modal_deploy_starts_public_server(monkeypatch):
    module, captured = _load_deploy_module(monkeypatch)
    commands = []

    def capture_command(*args, **kwargs):
        commands.append((args, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(module.subprocess, "Popen", capture_command)

    module.serve()

    assert commands == [
        (
            (
                [
                    sys.executable,
                    "-m",
                    "file_compressor.cli",
                    "web",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                ],
            ),
            {},
        )
    ]
    assert captured["function"]["env"] == {
        "PORT": "8080",
        "PDF_COMPRESSOR_PUBLIC_MODE": "1",
        "PDF_COMPRESSOR_MAX_UPLOAD_MB": "30",
        "PDF_COMPRESSOR_MAX_PAGES": "100",
        "PDF_COMPRESSOR_UPLOAD_TIMEOUT_SECONDS": "120",
        "PDF_COMPRESSOR_PROCESSING_TIMEOUT_SECONDS": "300",
        "PDF_COMPRESSOR_DOWNLOAD_TIMEOUT_SECONDS": "120",
        "PDF_COMPRESSOR_RATE_LIMIT_PER_MINUTE": "12",
        "PDF_COMPRESSOR_CONCURRENCY": "1",
        "PYTHONPATH": "/app/src",
    }
