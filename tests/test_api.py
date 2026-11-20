"""HTTP 接口测试（真实 ASGI + 后台任务线程）。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait_job(client: TestClient, job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.2)
    raise AssertionError(f"任务未结束：{job_id}")


def test_health_and_catalog(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["engine"] == "local-engine"

    catalog = client.get("/api/catalog").json()
    assert len(catalog["models"]) >= 8
    assert len(catalog["voices"]) >= 4
    assert {s["key"] for s in catalog["styles"]} == {"studio", "news", "warm"}
    assert catalog["demo_scripts"]


def test_model_lifecycle(client: TestClient) -> None:
    models = client.get("/api/models").json()["items"]
    target = next(m for m in models if not m["builtin"] and m["status"] == "not_installed")

    assert client.post(f"/api/models/{target['id']}/install").status_code == 200
    deadline = time.time() + 20
    while time.time() < deadline:
        model = client.get(f"/api/models/{target['id']}").json()
        if model["status"] == "installed":
            break
        time.sleep(0.1)
    assert model["status"] == "installed"
    assert model["progress"] == 100

    started = client.post(f"/api/models/{target['id']}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    health = client.get(f"/api/models/{target['id']}/health").json()
    assert health["status"] in {"healthy", "unreachable"}  # 演示环境无真实进程，端口探测即可

    assert client.post(f"/api/models/{target['id']}/uninstall").status_code == 409  # 运行中不允许卸载
    assert client.post(f"/api/models/{target['id']}/stop").status_code == 200
    assert client.post(f"/api/models/{target['id']}/uninstall").status_code == 200

    final = client.get(f"/api/models/{target['id']}").json()
    assert final["status"] == "not_installed"


def test_produce_endpoint_and_assets(client: TestClient) -> None:
    voices = client.get("/api/voices").json()["items"]
    payload = {
        "script": "欢迎使用数字人工作台。这条视频由接口生成，包含语音、字幕和画面。",
        "title": "接口测试",
        "voice_id": voices[0]["id"],
        "style": "news",
        "width": 360,
        "height": 640,
        "fps": 2,
    }
    created = client.post("/api/produce", json=payload)
    assert created.status_code == 202, created.text
    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    kinds = {a["kind"] for a in job["assets"]}
    assert {"audio", "subtitle", "video", "manifest"} <= kinds

    video = next(a for a in job["assets"] if a["kind"] == "video")
    resp = client.get(f"/api/assets/{video['id']}?inline=1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("video/mp4")
    assert len(resp.content) > 20_000

    listing = client.get("/api/assets?limit=10").json()["items"]
    assert listing


def test_produce_rejects_empty_script(client: TestClient) -> None:
    resp = client.post("/api/produce", json={"script": ""})
    assert resp.status_code == 422


def test_demo_run(client: TestClient) -> None:
    created = client.post("/api/demo/run")
    assert created.status_code == 202
    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["output"]["duration"] > 2


def test_clone_voice_without_upload(client: TestClient) -> None:
    resp = client.post("/api/voices/clone", json={"name": "接口克隆音色"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["kind"] == "cloned"
    assert data["analysis"]["f0"] > 50
    assert data["pitch"] > 0

    deleted = client.delete(f"/api/voices/{data['id']}")
    assert deleted.status_code == 204


def test_toolbox_probe_and_thumbnail(client: TestClient) -> None:
    assets = client.get("/api/assets?limit=50").json()["items"]
    video = next(a for a in assets if a["kind"] == "video")

    probe = client.post("/api/tools/probe", json={"asset_id": video["id"], "args": {}})
    assert probe.status_code == 200
    assert probe.json()["result"]["duration"] > 0

    thumb = client.post("/api/tools/thumbnail", json={"asset_id": video["id"], "args": {"at": 0.5}})
    assert thumb.status_code == 200
    assert thumb.json()["asset"]["kind"] == "thumbnail"


def test_metrics(client: TestClient) -> None:
    data = client.get("/api/metrics").json()
    assert data["jobs"]["succeeded"] >= 2
    assert data["assets"]["video_count"] >= 1
    assert data["assets"]["total_bytes"] > 0
    assert data["models"]["running"] >= 1
