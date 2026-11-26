"""对已启动的服务做端到端冒烟验证。

python scripts/smoke_http.py            # 默认 http://127.0.0.1:8000
python scripts/smoke_http.py 8080
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def wait_job(c: httpx.Client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = c.get(f"/api/jobs/{job_id}").json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.2)
    raise TimeoutError(job_id)


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=60.0, trust_env=False) as c:
        print(f"目标：{BASE}")

        health_resp = c.get("/api/health")
        health = health_resp.json()
        check("健康检查", health["status"] == "ok", health["engine"])
        check("请求关联 ID", bool(health_resp.headers.get("X-Request-ID")), health_resp.headers.get("X-Request-ID", ""))
        check("安全响应头", health_resp.headers.get("X-Content-Type-Options") == "nosniff")
        ready = c.get("/api/ready")
        check("就绪探针", ready.status_code == 200 and ready.json()["status"] == "ready", str(ready.status_code))

        catalog = c.get("/api/catalog").json()
        check("模型目录", len(catalog["models"]) >= 8, f"{len(catalog['models'])} 个")
        check("预设音色", len(catalog["voices"]) >= 4, f"{len(catalog['voices'])} 个")

        # 模型生命周期
        target = next(m for m in catalog["models"] if not m["builtin"] and m["status"] == "not_installed")
        check("安装模型", c.post(f"/api/models/{target['id']}/install").status_code == 200)
        deadline = time.time() + 20
        model = {}
        while time.time() < deadline:
            model = c.get(f"/api/models/{target['id']}").json()
            if model["status"] == "installed":
                break
            time.sleep(0.1)
        check("安装完成", model.get("status") == "installed", str(model.get("progress")))
        check("启动模型", c.post(f"/api/models/{target['id']}/start").status_code == 200)
        check("健康检查接口", "reachable" in c.get(f"/api/models/{target['id']}/health").json())
        c.post(f"/api/models/{target['id']}/stop")
        check("卸载模型", c.post(f"/api/models/{target['id']}/uninstall").status_code == 200)

        # 生产
        voices = c.get("/api/voices").json()["items"]
        created = c.post(
            "/api/produce",
            json={
                "script": "欢迎使用数字人工作台。这条视频由冒烟脚本生成，包含语音、字幕和画面。",
                "title": "冒烟验证",
                "voice_id": voices[0]["id"],
                "style": "news",
                "width": 360,
                "height": 640,
                "fps": 2,
            },
        )
        check("创建任务", created.status_code == 202, created.text[:80])
        job = wait_job(c, created.json()["job_id"])
        check("任务成功", job["status"] == "succeeded", job.get("error") or "")
        kinds = {a["kind"] for a in job.get("assets", [])}
        check("产物齐全", {"audio", "subtitle", "video", "manifest"} <= kinds, str(sorted(kinds)))
        check("步骤完整", len(job.get("steps", [])) == 5, f"{len(job.get('steps', []))} 步")

        video = next(a for a in job["assets"] if a["kind"] == "video")
        download = c.get(f"/api/assets/{video['id']}")
        check(
            "下载成片", download.status_code == 200 and len(download.content) > 20_000, f"{len(download.content):,} B"
        )
        check("视频可预览", download.headers["content-type"].startswith("video/mp4"))

        probe = c.post("/api/tools/probe", json={"asset_id": video["id"], "args": {}})
        check("工具箱 probe", probe.status_code == 200 and probe.json()["result"]["duration"] > 0)
        thumb = c.post("/api/tools/thumbnail", json={"asset_id": video["id"], "args": {"at": 0.5}})
        check("工具箱截封面", thumb.status_code == 200 and thumb.json()["asset"]["size_bytes"] > 1000)

        cloned = c.post("/api/voices/clone", json={"name": "冒烟克隆音色"})
        check("克隆音色", cloned.status_code == 201 and cloned.json()["analysis"]["f0"] > 50)

        bad = c.post("/api/produce", json={"script": ""})
        check("空脚本返回 422", bad.status_code == 422, str(bad.status_code))

        metrics = c.get("/api/metrics").json()
        check("指标可读", metrics["jobs"]["total"] >= 1 and metrics["assets"]["video_count"] >= 1)

    print()
    if failures:
        print(f"✗ 失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("✓ 全部冒烟检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
