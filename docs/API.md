# API 参考

Base URL：`http://127.0.0.1:8000`　交互文档：`/docs`（`DOCS_ENABLED=false` 时关闭）

## 约定

- 全部前缀 `/api`，JSON 收发；产物下载是二进制流。
- 生产任务是**异步**的：`POST /api/produce` 返回 `202 + job_id`，轮询 `GET /api/jobs/{id}`。
- **请求关联 ID**：每个响应都带 `X-Request-ID`（可用同名请求头透传上游值，最长 128 字符），
  同一条 ID 会出现在服务端所有相关日志里。
- **统一错误体**：`{"detail": <字符串或对象>, "request_id": "<id>"}`。
  `detail` 为对象时表示字段级校验原因（见第 6 节）。报障请提供 `request_id`。

| 状态码 | 含义 |
|---|---|
| `403` | 产物路径不在数据目录内 |
| `404` | 资源不存在（模型 / 音色 / 任务 / 产物 / 工具） |
| `409` | 状态冲突（模型未启动、任务未结束、预设音色不可删） |
| `410` | 产物文件已被清理 |
| `422` | 参数 / 契约错误，或请求体不合法 |
| `503` | `/api/ready` 探测到依赖不可用 |

## 探针

`GET /api/health`（存活，不查库、不调 ffmpeg）：

```json
{
  "status": "ok", "version": "0.6.0", "environment": "production",
  "engine": "local-engine", "ffmpeg": "ffmpeg", "workers": 1,
  "sample_rate": 24000, "database": "sqlite", "docs_enabled": false, "uptime_s": 1284.5
}
```

`GET /api/ready`（就绪，检查数据库 / ffmpeg / ffprobe / 数据目录）：

```json
{"status": "ready", "checks": {"database": true, "ffmpeg": true, "ffprobe": true, "data_dir": true}, "version": "0.6.0"}
```

任一检查失败返回 `503` + `{"status": "degraded", ...}`。

---

## 端点总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活探针 |
| GET | `/api/ready` | 就绪探针（含 ffmpeg 与数据目录可写） |
| GET | `/api/catalog` | 模型、音色、风格、分辨率预设、示例脚本 |
| GET | `/api/models` | 模型服务列表（含进度/端口/健康） |
| GET | `/api/models/{id}` | 单个模型 |
| POST | `/api/models/{id}/install` | 安装（异步下载，进度可轮询） |
| POST | `/api/models/{id}/start` | 启动（分配端口） |
| POST | `/api/models/{id}/stop` | 停止 |
| POST | `/api/models/{id}/uninstall` | 卸载（需先停止） |
| GET | `/api/models/{id}/health` | 端口探测 |
| GET | `/api/voices` | 音色列表 |
| POST | `/api/voices` | 新建自定义音色 |
| POST | `/api/voices/clone` | 克隆音色（参考音频 base64，可选） |
| DELETE | `/api/voices/{id}` | 删除非预设音色（同时清理参考音频） |
| POST | `/api/produce` | 提交生产任务 |
| POST | `/api/demo/run` | 一键示例 |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/{id}` | 任务详情（步骤 + 产物） |
| POST | `/api/jobs/{id}/cancel` | 取消（协作式） |
| POST | `/api/jobs/{id}/retry` | 重试（生成新任务） |
| DELETE | `/api/jobs/{id}` | 删除任务与产物文件 |
| GET | `/api/assets` | 产物列表 |
| GET | `/api/assets/{id}` | 下载；`?inline=1` 预览 |
| GET | `/api/tools` | 工具箱列表 |
| POST | `/api/tools/{tool}` | probe / extract_audio / thumbnail / change_speed |
| GET | `/api/metrics` | 任务、产物、模型指标 |

---

## 1. 生产一条视频

```http
POST /api/produce
Content-Type: application/json

{
  "script": "大家好，欢迎来到本期产品速览。今天介绍的是新款智能音箱。",
  "title": "新品介绍",
  "voice_id": "voi_xxx",
  "model_id": null,
  "style": "studio",
  "width": 720,
  "height": 1280,
  "fps": 2
}
```

```json
{"job_id": "job_9f2c1a7b3d10", "status": "queued", "poll": "/api/jobs/job_9f2c1a7b3d10"}
```

轮询任务：

```http
GET /api/jobs/job_9f2c1a7b3d10
```

```json
{
  "id": "job_9f2c1a7b3d10",
  "title": "新品介绍",
  "status": "succeeded",
  "progress": 100,
  "duration_ms": 2417,
  "output": {
    "duration": 12.4,
    "segment_count": 3,
    "engine": "local-engine",
    "resolution": [720, 1280],
    "style": "studio"
  },
  "steps": [
    {"seq": 1, "name": "切分脚本（script）", "status": "succeeded", "detail": "切成 3 段，最长 24 字", "latency_ms": 2},
    {"seq": 2, "name": "语音合成（tts）", "status": "succeeded", "detail": "音频 12.4s，3 段", "latency_ms": 310},
    {"seq": 3, "name": "生成字幕（subtitle）", "status": "succeeded", "detail": "3 条字幕", "latency_ms": 3},
    {"seq": 4, "name": "数字人渲染（avatar）", "status": "succeeded", "detail": "12.4s · 720×1280 · 25 帧", "latency_ms": 1900},
    {"seq": 5, "name": "生成清单（manifest）", "status": "succeeded", "detail": "清单已生成", "latency_ms": 120}
  ],
  "assets": [
    {"id": "ast_a", "kind": "audio", "filename": "voice.wav", "size_bytes": 595244, "duration": 12.4,
     "url": "/api/assets/ast_a", "preview_url": "/api/assets/ast_a?inline=1"}
  ]
}
```

下载 / 预览：

```bash
curl -OJ http://127.0.0.1:8000/api/assets/ast_video
```

---

## 2. 模型服务生命周期

```http
POST /api/models/mdl_cosyvoice/install
```

```json
{"ok": true, "status": "downloading", "model": {"status": "downloading", "progress": 0}}
```

轮询 `GET /api/models/mdl_cosyvoice` 看 `progress` 从 0 到 100、`status` 变 `installed`。

```http
POST /api/models/mdl_cosyvoice/start
```

```json
{"ok": true, "status": "running", "port": 19100, "model": {"status": "running", "port": 19100}}
```

状态冲突会返回 409：

```json
{"detail": "当前状态 running 不能安装"}
```

> 真实模型需要额外配置推理后端：`AIGC_COSYVOICE_300M_URL=http://127.0.0.1:9880`；
> 未配置时 `POST /api/produce` 会返回 409 并提示变量名，不会静默降级。

---

## 3. 音色克隆

```http
POST /api/voices/clone
Content-Type: application/json

{"name": "客户 A · 女声", "audio_b64": null}
```

`audio_b64` 为空时使用内置样本（便于演示分析链路）。返回：

```json
{
  "id": "voi_xxx",
  "name": "客户 A · 女声",
  "kind": "cloned",
  "pitch": 1.083,
  "speed": 1.0,
  "timbre": "warm",
  "reference_duration": 4.0,
  "analysis": {"duration": 4.0, "f0": 178.7, "zcr": 0.074, "pitch": 1.083, "timbre": "warm"}
}
```

---

## 4. 工具箱

```http
POST /api/tools/probe
{"asset_id": "ast_video", "args": {}}
```

```json
{
  "tool": "probe",
  "result": {
    "duration": 12.4,
    "size_bytes": 642311,
    "bit_rate": 414034,
    "streams": [
      {"type": "video", "codec": "h264", "width": 720, "height": 1280},
      {"type": "audio", "codec": "aac", "width": null, "height": null}
    ]
  },
  "asset": null
}
```

`thumbnail` / `extract_audio` / `change_speed` 会返回新产物：

```json
{"tool": "thumbnail", "result": {"path": "...", "width": 720, "height": 1280}, "asset": {"id": "ast_x", "kind": "thumbnail"}}
```

---

## 5. 指标

```http
GET /api/metrics
```

```json
{
  "jobs": {"total": 12, "succeeded": 11, "failed": 1, "success_rate": 0.917, "avg_duration_ms": 2417},
  "assets": {"count": 58, "total_bytes": 18432000, "video_count": 11},
  "models": {"total": 8, "installed": 3, "running": 2, "disk_mb": 1280}
}
```

## 6. 错误响应示例

业务校验失败（脚本为空、模型未启动）：

```json
{"detail": "MiniMax 未运行，请先安装并启动", "request_id": "9f2c1a7b3d10"}
```

请求体本身不合法（缺必填字段 / 多传字段）：

```json
{
  "detail": {
    "message": "请求参数校验失败",
    "errors": [
      {"loc": ["body", "script"], "msg": "Field required", "type": "missing"},
      {"loc": ["body", "nope"], "msg": "Extra inputs are not permitted", "type": "extra_forbidden"}
    ]
  },
  "request_id": "9f2c1a7b3d10"
}
```

产物路径越界（不在 `AIGC_DATA_DIR` 内）：

```json
{"detail": "非法的产物路径", "request_id": "9f2c1a7b3d10"}
```

未预期异常（不回显堆栈）：

```json
{"detail": "服务内部错误，请携带 request_id 反馈", "request_id": "9f2c1a7b3d10"}
```

任务失败不是 HTTP 错误：接口返回 `200`，任务体里 `status=failed`，原因在 `error` 与失败步骤的 `detail` 中。
