"""模型服务生命周期管理。

状态机：
    not_installed →(install)→ downloading → installed
    installed →(start)→ starting → running →(stop)→ stopped
    任意可逆状态 →(uninstall)→ not_installed

下载用后台线程模拟（真实实现会换成 aria2/断点续传），
但状态、进度、磁盘目录、端口分配、健康检查都是真的：
前端看到的就是一个"正在装模型"的服务。
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.logging import get_logger
from app.models import ModelService
from app.providers.catalog import MODEL_CATALOG

log = get_logger("app.models")

_LOCK = threading.Lock()
_ACTIVE_DOWNLOADS: set[str] = set()
PORT_BASE = 19100


def _now() -> datetime:
    return datetime.now(UTC)


class ModelManager:
    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def seed(self, db: Session) -> None:
        """把目录写进库：不存在则建，内置引擎默认已安装并运行。"""
        existing = {m.key: m for m in db.scalars(select(ModelService)).all()}
        for item in MODEL_CATALOG:
            if item["key"] in existing:
                continue
            builtin = bool(item.get("builtin"))
            db.add(
                ModelService(
                    key=item["key"],
                    name=item["name"],
                    kind=item["kind"],
                    version=item["version"],
                    size_mb=item["size_mb"],
                    status="running" if builtin else "not_installed",
                    progress=100 if builtin else 0,
                    description=item["description"],
                    tags=item["tags"],
                    builtin=builtin,
                    path=str(get_settings().model_dir / item["key"]) if builtin else "",
                    installed_at=_now() if builtin else None,
                    started_at=_now() if builtin else None,
                )
            )
        db.flush()

    def reset_stale(self) -> None:
        """进程重启后把中间态收敛，避免永远停在 downloading。"""
        with session_scope() as db:
            for model in db.scalars(select(ModelService)).all():
                if model.status in {"downloading"}:
                    model.status, model.progress = "not_installed", 0
                elif model.status in {"starting", "stopping"}:
                    model.status = "installed" if model.path else "not_installed"
                elif model.status == "running" and not model.path:
                    model.status = "not_installed"

    # ------------------------------------------------------------------ #
    # 安装 / 启动 / 停止 / 卸载
    # ------------------------------------------------------------------ #
    def install(self, model_id: str) -> dict:
        with _LOCK, session_scope() as db:
            model = db.get(ModelService, model_id)
            if model is None:
                raise KeyError(model_id)
            if model.status in {"downloading", "running"}:
                return {"ok": False, "reason": f"当前状态 {model.status} 不能安装"}
            model.status, model.progress, model.last_error = "downloading", 0, None
            key = model.key

        log.info("model_install_started", extra={"model_id": model_id, "key": key})
        _ACTIVE_DOWNLOADS.add(key)
        threading.Thread(target=self._download, args=(model_id, key), name=f"install-{key}", daemon=True).start()
        return {"ok": True, "status": "downloading"}

    def _download(self, model_id: str, key: str) -> None:
        settings = get_settings()
        target = settings.model_dir / key
        try:
            for pct in (5, 15, 30, 45, 60, 75, 90):
                if key not in _ACTIVE_DOWNLOADS:
                    return  # 被取消
                time.sleep(max(0.0, settings.download_step_seconds))
                with session_scope() as db:
                    model = db.get(ModelService, model_id)
                    if model is None or model.status != "downloading":
                        return
                    model.progress = pct

            target.mkdir(parents=True, exist_ok=True)
            manifest = {
                "key": key,
                "installed_at": _now().isoformat(),
                "note": "演示环境只写入元数据清单；真实部署在此处落权重与配置",
            }
            (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            (target / "README.txt").write_text(
                f"{key}\n\n这是模型目录占位文件。接入真实模型时，把权重与本服务的启动脚本放在这里，\n"
                "并把 AIGC_<KEY>_URL 指向已启动的推理服务。\n",
                encoding="utf-8",
            )

            with session_scope() as db:
                model = db.get(ModelService, model_id)
                if model is None:
                    return
                model.status = "installed"
                model.progress = 100
                model.path = str(target)
                model.installed_at = _now()
            log.info("model_installed", extra={"model_id": model_id, "key": key, "path": str(target)})
        except Exception as exc:  # noqa: BLE001 —— 安装失败要落到 last_error，不能让状态卡在 downloading
            log.exception("model_install_failed", extra={"model_id": model_id, "key": key})
            with session_scope() as db:
                model = db.get(ModelService, model_id)
                if model is not None:
                    model.status = "error"
                    model.last_error = str(exc)
        finally:
            _ACTIVE_DOWNLOADS.discard(key)

    def start(self, model_id: str) -> dict:
        with _LOCK, session_scope() as db:
            model = db.get(ModelService, model_id)
            if model is None:
                raise KeyError(model_id)
            if model.status not in {"installed", "stopped"}:
                return {"ok": False, "reason": f"当前状态 {model.status} 不能启动"}
            port = model.port or self._allocate_port(db, model.id)
            model.status, model.port, model.started_at, model.progress = "starting", port, _now(), 100

        time.sleep(0.25)  # 模拟进程拉起

        with session_scope() as db:
            model = db.get(ModelService, model_id)
            if model is None:
                return {"ok": False, "reason": "模型已删除"}
            if model.status != "starting":
                return {"ok": False, "reason": f"启动被中断：{model.status}"}
            model.status = "running"
            model.last_error = None
            if model.path:
                (Path(model.path) / "runtime.json").write_text(
                    json.dumps({"port": model.port, "started_at": _now().isoformat()}, ensure_ascii=False),
                    encoding="utf-8",
                )
        log.info("model_started", extra={"model_id": model_id, "port": port})
        return {"ok": True, "status": "running", "port": port}

    def stop(self, model_id: str) -> dict:
        with _LOCK, session_scope() as db:
            model = db.get(ModelService, model_id)
            if model is None:
                raise KeyError(model_id)
            if model.builtin:
                return {"ok": False, "reason": "内置引擎常驻，不能停止"}
            if model.status not in {"running", "starting"}:
                return {"ok": False, "reason": f"当前状态 {model.status} 不能停止"}
            model.status = "stopped"
            model.started_at = None
        return {"ok": True, "status": "stopped"}

    def uninstall(self, model_id: str) -> dict:
        with _LOCK, session_scope() as db:
            model = db.get(ModelService, model_id)
            if model is None:
                raise KeyError(model_id)
            if model.builtin:
                return {"ok": False, "reason": "内置引擎不支持卸载"}
            if model.status in {"running", "starting", "downloading"}:
                return {"ok": False, "reason": f"请先停止（当前 {model.status}）"}
            path = model.path
            model.status, model.progress, model.port = "not_installed", 0, None
            model.path, model.installed_at, model.started_at = "", None, None
            model.last_error = None
        if path and Path(path).exists():
            shutil.rmtree(path, ignore_errors=True)
        return {"ok": True, "status": "not_installed"}

    # ------------------------------------------------------------------ #
    # 健康检查
    # ------------------------------------------------------------------ #
    def health(self, model: ModelService) -> dict:
        if model.key == "local-engine":
            return {"status": "healthy", "reachable": True, "latency_ms": 0, "engine": "local-engine"}
        if model.status != "running" or not model.port:
            return {"status": "offline", "reachable": False, "latency_ms": None, "engine": model.key}

        started = time.perf_counter()
        reachable = False
        try:
            with socket.create_connection(("127.0.0.1", model.port), timeout=0.35):
                reachable = True
        except OSError:
            reachable = False
        latency = int((time.perf_counter() - started) * 1000)
        return {
            "status": "healthy" if reachable else "unreachable",
            "reachable": reachable,
            "latency_ms": latency,
            "engine": model.key,
            "note": None if reachable else "演示环境没有真实推理进程；健康检查按端口探测",
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _allocate_port(db: Session, exclude_id: str) -> int:
        used = {
            m.port
            for m in db.scalars(select(ModelService)).all()
            if m.id != exclude_id and m.port and m.status in {"running", "starting"}
        }
        port = PORT_BASE
        while port in used:
            port += 1
        return port


manager = ModelManager()
