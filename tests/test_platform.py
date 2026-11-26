"""平台级测试：探针、请求关联 ID、安全响应头、错误契约、配置解析。

这些是网关 / K8s / 日志采集依赖的接口约定，改动中间件或配置解析会立刻红。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings, get_settings, reset_settings_cache
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_reports_runtime(client: TestClient) -> None:
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["engine"] == "local-engine"
    assert body["workers"] >= 1
    assert body["sample_rate"] > 0
    assert body["uptime_s"] >= 0


def test_readiness_checks_dependencies(client: TestClient) -> None:
    resp = client.get("/api/ready")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"database", "ffmpeg", "ffprobe", "data_dir"}
    assert all(body["checks"].values())


def test_request_id_generated_and_propagated(client: TestClient) -> None:
    generated = client.get("/api/health")
    assert len(generated.headers["X-Request-ID"]) >= 8

    propagated = client.get("/api/health", headers={"X-Request-ID": "trace-0001"})
    assert propagated.headers["X-Request-ID"] == "trace-0001"

    missing = client.get("/api/jobs/job_not_exist", headers={"X-Request-ID": "trace-404"})
    assert missing.json()["request_id"] == "trace-404"


def test_security_headers_present(client: TestClient) -> None:
    headers = client.get("/api/health").headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in headers


def test_error_envelope_carries_request_id(client: TestClient) -> None:
    resp = client.get("/api/jobs/job_not_exist")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "任务不存在"
    assert body["request_id"] == resp.headers["X-Request-ID"]


def test_validation_error_lists_offending_fields(client: TestClient) -> None:
    resp = client.post("/api/produce", json={"unexpected": 1})

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["message"]
    assert any(item["loc"] == ["body", "script"] and item["type"] == "missing" for item in detail["errors"])
    assert any(item["type"] == "extra_forbidden" for item in detail["errors"])


def test_timestamps_are_utc_iso8601(client: TestClient) -> None:
    voices = client.get("/api/voices").json()["items"]

    assert voices
    created_at = voices[0]["created_at"]
    assert created_at.endswith("Z")
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_asset_path_outside_data_dir_is_rejected(client: TestClient) -> None:
    """产物路径必须落在数据目录内；同前缀的兄弟目录也不能绕过。"""
    from app.db import session_scope
    from app.models import Asset, Job, new_id

    with session_scope() as db:
        job = Job(kind="produce", title="path-check", status="succeeded", script="x")
        db.add(job)
        db.flush()
        asset = Asset(
            id=new_id("ast"),
            job_id=job.id,
            kind="video",
            filename="evil.mp4",
            path=str((Path.cwd().parent / "evil.mp4").resolve()),
            mime="video/mp4",
        )
        db.add(asset)
        db.flush()
        asset_id = asset.id
        job_id = job.id

    try:
        resp = client.get(f"/api/assets/{asset_id}")
        assert resp.status_code == 403, resp.text
    finally:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                db.delete(job)


def test_settings_describe_is_safe_to_log() -> None:
    settings = Settings()
    described = settings.describe()

    assert described["version"] == __version__
    assert described["database"] in {"sqlite", "external"}
    assert "database_url" not in described


def test_ffmpeg_binaries_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：FFMPEG_BIN / FFPROBE_BIN 曾经写在 .env.example 里但渲染器没读。"""
    from app.media import avatar as avatar_module

    monkeypatch.setenv("FFMPEG_BIN", "/opt/custom/ffmpeg")
    monkeypatch.setenv("FFPROBE_BIN", "/opt/custom/ffprobe")
    reset_settings_cache()
    try:
        assert avatar_module.ffmpeg_bin() == "/opt/custom/ffmpeg"
        assert avatar_module.ffprobe_bin() == "/opt/custom/ffprobe"
    finally:
        reset_settings_cache()


def test_data_dirs_derive_from_data_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """单一根目录：容器里只挂一个卷，四个子目录都要跟着走。"""
    import shutil
    import uuid

    root = Path("data") / f"dataroot_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("AIGC_DATA_DIR", str(root))
    reset_settings_cache()
    try:
        settings = get_settings()
        assert settings.data_dir == root
        assert settings.media_dir == root / "media"
        assert settings.model_dir == root / "models"
        assert settings.upload_dir == root / "uploads"
        assert settings.model_dir.is_dir()  # get_settings 负责创建
    finally:
        reset_settings_cache()
        shutil.rmtree(root, ignore_errors=True)
