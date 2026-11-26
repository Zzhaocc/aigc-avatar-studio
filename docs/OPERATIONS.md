# 运维手册

面向部署与值班：怎么起、怎么探活、怎么看日志、怎么备份、出问题先看哪里。

---

## 1. 部署形态

### 1.1 本地（最快）

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

前置条件只有一个：**`ffmpeg` / `ffprobe` 在 PATH 里**（或通过 `FFMPEG_BIN` / `FFPROBE_BIN` 指定）。
中文标题与字幕需要系统里有中文字体，容器镜像已内置 `fonts-noto-cjk`。

数据全部落在 `AIGC_DATA_DIR`（默认 `./data`）：`studio.db`、`media/`、`models/`、`uploads/`。

### 1.2 容器

```bash
docker build -t aigc-avatar-studio:0.6.0 .
docker run -d --name aigc-avatar \
  -p 8000:8000 \
  -v aigc-data:/app/data \
  -e LOG_FORMAT=json \
  aigc-avatar-studio:0.6.0
```

镜像里已装 ffmpeg 与中文字体，以非 root 用户（uid 10001）运行，内置 `HEALTHCHECK` 指向 `/api/health`。

### 1.3 Compose

```bash
docker compose up -d --build
docker compose logs -f studio
docker compose down
```

`docker-compose.yml` 把 `/app/data` 挂到具名卷 `studio-data`，容器重建不丢库、产物与模型目录。

### 1.4 Kubernetes 要点

- **单副本 + RWO 卷**：队列是进程内线程池，多副本会各跑各的；SQLite 也不适合共享写；
- `livenessProbe` → `GET /api/health`（不查库）；`readinessProbe` → `GET /api/ready`（查库/ffmpeg/数据目录）；
- ffmpeg 是硬依赖，基础镜像必须带它，否则 `/api/ready` 会一直 503；
- 内置接口不带鉴权，对外必须置于网关之后。

> 真实模型服务（CosyVoice / MuseTalk）是**外部进程**：本服务只管理它们的元数据与生命周期，
> 推理走 `AIGC_<KEY>_URL` 指向的 HTTP 服务，部署时请把模型服务与本服务分开编排。

---

## 2. 环境变量

完整清单见 [`.env.example`](../.env.example)，关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 环境标识，写入启动日志与 `/api/health` |
| `AIGC_DATA_DIR` | `./data` | 数据根目录，派生 `media/` `models/` `uploads/` |
| `DATABASE_URL` | `sqlite:///./data/studio.db` | 换 PG 时改成 `postgresql+psycopg://...` |
| `FFMPEG_BIN` `FFPROBE_BIN` | `ffmpeg` `ffprobe` | 可执行文件路径 |
| `AIGC_FFMPEG_TIMEOUT` | `300` | 单条 ffmpeg/ffprobe 超时（秒），超时任务置 failed |
| `AIGC_SAMPLE_RATE` | `24000` | 输出采样率，贯穿 TTS / 拼接 / 抽音轨 |
| `AIGC_WIDTH` `AIGC_HEIGHT` `AIGC_FPS` | `720` `1280` `2` | 默认分辨率与帧率（请求可覆盖） |
| `AIGC_MAX_WORKERS` | `1` | 队列并发；对应单卡 GPU 约束 |
| `AIGC_DOWNLOAD_STEP` | `0.08` | 模拟下载每步耗时（秒），演示时调小 |
| `AIGC_REMOTE_TIMEOUT` | `120` | 远程推理请求超时（秒） |
| `CORS_ORIGINS` | `*` | 逗号分隔；生产填具体域名 |
| `LOG_LEVEL` `LOG_FORMAT` | `INFO` `text` | `LOG_FORMAT=json` 用于日志采集 |
| `REQUEST_ID_HEADER` | `X-Request-ID` | 关联 ID 的头部名 |
| `DOCS_ENABLED` | `true` | 生产建议设为 `false`，关闭 `/docs` |

配置在进程启动时解析一次（`get_settings` 带缓存），**改完必须重启**。

---

## 3. 探针

| 探针 | 端点 | 期望 | 用途 |
|---|---|---|---|
| 存活 | `GET /api/health` | `200` + `{"status":"ok"}` | 进程是否还能响应；不查库、不调 ffmpeg |
| 就绪 | `GET /api/ready` | `200` + `{"status":"ready"}` | 数据库、ffmpeg、ffprobe、数据目录可写；`503` 时先摘流量 |

```bash
curl -s localhost:8000/api/ready | jq
# {"status":"ready","checks":{"database":true,"ffmpeg":true,"ffprobe":true,"data_dir":true},"version":"0.6.0"}
```

`data_dir` 是**真实写一次探针文件**，比看权限位可靠：磁盘满或卷只读会立刻暴露。

模型服务的健康检查是另一个端点：`GET /api/models/{id}/health`，
内置引擎恒为 `healthy`，其它模型按分配端口做 TCP 探测（演示环境没有真实推理进程，
返回 `unreachable` 属于预期）。

---

## 4. 日志与排障

### 4.1 结构

- `LOG_FORMAT=text`：`时间 级别 [request_id] logger: 事件 key=value`；
- `LOG_FORMAT=json`：一行一个 JSON，固定字段 `ts` / `level` / `logger` / `msg` / `request_id` + 业务字段。

### 4.2 关键事件

| 事件 | 级别 | 关键字段 |
|---|---|---|
| `startup` | INFO | 配置摘要（不含敏感信息） |
| `request` | INFO | `method` `path` `status` `duration_ms` `request_id` |
| `model_install_started` | INFO | `model_id` `key` |
| `model_installed` | INFO | `model_id` `path` |
| `model_install_failed` | ERROR | 带堆栈；同时写 `last_error` |
| `model_started` | INFO | `model_id` `port` |
| `job_submitted` / `job_started` | INFO | `job_id` |
| `job_succeeded` | INFO | `job_id` `assets` `duration_ms` |
| `job_cancelled` | INFO | `job_id` `stage` |
| `job_failed` | ERROR/WARNING | 带堆栈；`job.error` 同时保存原因 |
| `unhandled_error` | ERROR | 带堆栈 + `request_id` |

### 4.3 定位一次报错

错误响应里带 `request_id`，与响应头 `X-Request-ID` 相同：

```bash
# 前端或用户反馈 {"detail": "...", "request_id": "9f2c1a7b3d10"}
grep '"request_id": "9f2c1a7b3d10"' /var/log/aigc-avatar.log | jq
```

任务失败再按 `job_id` 查 `GET /api/jobs/{id}`：`steps` 标出失败步骤与 `detail`，
`error` 保留 ffmpeg stderr 尾部。

---

## 5. 数据与备份

### 5.1 SQLite + 文件

```bash
# 一致性备份（不要直接 cp 正在写入的库）
sqlite3 data/studio.db ".backup '/backup/studio-$(date +%F).db'"
tar czf /backup/media-$(date +%F).tgz data/media data/uploads
```

- 已开启 WAL：`data/` 下有 `-wal` / `-shm`，用 `.backup` 或停机拷贝；
- `media/` 是产物、`uploads/` 是克隆参考音频、`models/` 是模型目录元数据，三者一起备份才能完整恢复；
- 保留策略按合规要求设定；本仓库不内置自动备份。

### 5.2 迁移到 PostgreSQL

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/aigcavatar"
```

表由 SQLAlchemy `create_all` 自动创建，适合全新库；已有数据需自行搬迁。
生产建议引入 Alembic 管理变更。

---

## 6. 升级与回滚

1. 打镜像标签：`docker build -t aigc-avatar-studio:<版本> .`；
2. 备份数据库与 `media/`；
3. 滚动更新，观察 `/api/ready` 与失败率；
4. 出问题回滚镜像标签 + 备份的数据。

启动时会做两件收敛：
- 把上次未结束的 `queued` / `running` 任务标 `failed`（可重试）；
- 把停在 `downloading` / `starting` / `stopping` 的模型状态收敛回稳定态。

这是刻意设计：宁可让用户点"重试"，也不让任务永远转圈。

---

## 7. 容量与扩容

| 瓶颈 | 现状 | 扩容方向 |
|---|---|---|
| 生产吞吐 | 进程内线程池，默认 1 并发（逐帧渲染 + 编码是 CPU 密集） | 调 `AIGC_MAX_WORKERS`（受 CPU 限制），或换外部队列 + 多副本 |
| 渲染耗时 | 2 fps 逐帧 Pillow 绘制，15 秒成片约 5–6 秒 | 提高 fps 会线性增加耗时；批量任务建议错峰 |
| 磁盘 | 每个任务中间 WAV/PNG/MP4 都在 `media/<job_id>/` | 定期清理已完成任务；产物换对象存储 |
| 模型目录 | `models/<key>/` 只存元数据 | 真实权重建议外挂大容量卷 |
| 数据库 | SQLite 单写者 | 换 PostgreSQL；队列外置后可多副本 |

压测建议：先用 `python scripts/smoke_http.py` 验证功能，再用 `hey` / `k6` 打
`POST /api/demo/run`，观察 `/api/metrics` 的成功率与 `avg_duration_ms`。

---

## 8. 常见故障

| 现象 | 先看什么 | 处理 |
|---|---|---|
| `/api/ready` 返回 503 | `checks` 里哪一项为 `false` | `ffmpeg=false` → 镜像/主机没装 ffmpeg 或 `FFMPEG_BIN` 写错；`data_dir=false` → 卷只读或磁盘满 |
| 视频里中文变方框 | 容器字体 | 镜像已装 `fonts-noto-cjk`；自建镜像要补中文字体 |
| 任务 `failed` 且 error 含"超时" | `AIGC_FFMPEG_TIMEOUT` | 素材过大或机器太慢，调大超时或缩短脚本 |
| 任务 `failed` 且 error 是 ffmpeg stderr | 编码器是否可用 | 精简版 ffmpeg 可能缺 `libmp3lame`，代码会退化到 `mp3_mf`；仍失败就换完整 ffmpeg |
| 选了非内置模型提示 409 | `AIGC_<KEY>_URL` | 该模型只做了服务管理；推理需要真实后端地址，或改用 `local-engine` |
| 模型一直 `downloading` | 进程是否重启过 | 重启会把中间态收敛回 `not_installed`；重新安装即可 |
| 模型 `unreachable` | 端口探测 | 演示环境没有真实推理进程，属于预期；接真实服务后才会 healthy |
| 任务一直 `queued` | 进程是否重启过 | 启动恢复会把 stale 任务标 failed，重试即可 |
| 控制台接口 CORS 报错 | `CORS_ORIGINS` | 生产填具体域名，不要既留 `*` 又想带凭证 |
| 磁盘持续增长 | `data/media`、`data/models` | 删除已完成任务（会清理目录）、卸载不用的模型 |
| 产物下载 403 | 路径校验 | 产物必须落在 `AIGC_DATA_DIR` 内；换数据目录后旧产物会拒绝下载 |
| 产物下载 410 | 文件已清理 | 记录还在但文件被删；重新生产即可 |

---

## 9. 上线前检查清单

- [ ] `APP_ENV=production`，`DOCS_ENABLED=false`
- [ ] 基础镜像含 ffmpeg 与中文字体，`/api/ready` 返回 200
- [ ] `AIGC_DATA_DIR` 指向持久卷，且已做备份
- [ ] `CORS_ORIGINS` 为具体域名，未使用 `*`
- [ ] 网关已终止 TLS 并接入鉴权
- [ ] `liveness` / `readiness` 探针已配置，告警接到人
- [ ] 日志已接入采集平台（`LOG_FORMAT=json`），保留期符合合规要求
- [ ] `python -m pytest` 与 `ruff check` 在发布分支全绿
- [ ] 参考音频与素材已取得授权，且支持删除 / 撤回
