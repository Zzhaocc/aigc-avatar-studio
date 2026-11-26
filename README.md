# AIGC Avatar Studio · 数字人内容生产平台

> 服务端的数字人视频生产：文案进来，带语音、字幕、数字人画面的 MP4 出去。
> 同时把模型服务当成一等资源来管理：安装、启动、端口、健康检查、状态收敛。

[![Version](https://img.shields.io/badge/version-0.6.0-blue?style=flat-square)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.13-3776AB?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square)](https://fastapi.tiangolo.com/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-7.1-007808?style=flat-square)](https://ffmpeg.org/)
[![CI](https://github.com/Zzhaocc/aigc-avatar-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/Zzhaocc/aigc-avatar-studio/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-yellow?style=flat-square)](LICENSE)

---

## 产品定位

面向**内容生产团队**的数字人视频后端。它解决两件在本地工具里很难做好的事：

1. **生产可编排**：文案 → 切句 → 语音 → 字幕 → 画面 → 封装，五步都有进度、耗时与产物，
   失败可重试，进程重启可恢复；
2. **模型可运营**：8 个模型服务各自有安装目录、下载进度、端口与健康状态，
   状态机覆盖安装、启动、停止、卸载与异常，推理后端按需切换。

| 维度 | 说明 |
|---|---|
| 目标用户 | 需要批量产出数字人口播视频的内容团队与工具产品 |
| 核心价值 | 把「一条视频」变成可排队、可观测、可替换模型的生产任务 |
| 交付形态 | 服务端应用 + Web 控制台 + REST API；Docker / Compose / 裸机 |
| 引擎策略 | 内置离线引擎开箱可用；真实模型通过 `AIGC_<KEY>_URL` 接入 |
| 不覆盖 | 模型权重分发、直播推流、桌面端打包（见 [能力边界](#能力边界)） |

---

## 能力矩阵

| 能力 | 说明 | 产物 |
|---|---|---|
| 生产管线 | 切句（≤28 字）→ 逐句 TTS → 拼接 → 时间轴字幕 → 逐帧渲染 → H.264/AAC 封装 | MP4 + WAV + SRT + 封面 + 清单 |
| 模型服务管理 | 安装（带进度）/ 启动（分配端口）/ 停止 / 卸载 / 健康探测 / 启动收敛 | `models/<key>/` 目录 + 状态与端口 |
| 音色 | 预设音色、手填参数、参考音频克隆（基频与音色倾向分析） | 音色记录 + 试听音频 |
| 任务队列 | 单 worker、步骤级进度、协作式取消、失败重试、崩溃恢复、ffmpeg 超时 | 任务与步骤记录 |
| FFmpeg 工具箱 | 探测 / 抽取音轨 / 截封面 / 调整倍速，结果落库为新产物 | 派生产物 |
| 可观测 | 结构化日志 + request_id、存活/就绪探针、运行指标 | 日志、指标、任务详情 |

---

## 快速开始

唯一的外部依赖是 **ffmpeg**（`ffmpeg -version` 能跑即可）；容器镜像已内置 ffmpeg 与中文字体。

### Docker（推荐）

```bash
git clone https://github.com/Zzhaocc/aigc-avatar-studio.git
cd aigc-avatar-studio
docker compose up -d --build
```

打开 <http://127.0.0.1:8000>，点「一键示例」，等 3–6 秒就能看到成片。

### 本地

```bash
git clone https://github.com/Zzhaocc/aigc-avatar-studio.git
cd aigc-avatar-studio
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

Windows 也可以直接双击 `start.bat`，或运行 `.\scripts\run.ps1 -Port 8000`。

### 命令行验证

```bash
python scripts/demo.py      # 跑一条完整生产链路，打印每步耗时与产物路径
```

---

## 控制台

![控制台](docs/screenshots/console-studio.jpg)

工作台四块：脚本输入 → 音色/风格/分辨率 → 当前任务（进度、步骤、视频播放）→ 最近任务。

| 模型服务 | 音色 |
|---|---|
| ![模型](docs/screenshots/console-models.jpg) | ![音色](docs/screenshots/console-voices.jpg) |

更多截图见 [`docs/screenshots/`](docs/screenshots/)：任务列表、指标、工具箱。

---

## 模型服务生命周期

8 个模型服务（CosyVoice-300M / IndexTTS / FishSpeech / FunASR / MuseTalk / Wav2Lip / LatentSync / 内置引擎），
每个都有真实的安装目录、进度、端口与健康检查：

```text
not_installed --install--> downloading --100%--> installed
installed     --start-->   starting    --ok-->   running --stop--> stopped
stopped       --start-->   starting
any reversible state --uninstall--> not_installed
downloading   --失败-->    error       --install-->   downloading
```

- 安装会真的在 `data/models/<key>/` 下落目录与清单；启动会分配端口并写 `runtime.json`；
  健康检查按端口探测；
- 推理后端由适配器决定：内置引擎直接可用，其它模型需要把 `AIGC_<KEY>_URL` 指向真实服务；
- 未配置后端时选择真实模型会返回 409 并提示变量名，不会静默降级。

---

## 媒体管线

```text
脚本 → 切句（≤28 字）→ 逐句 TTS → 拼接 + 句间静音 → 时间轴字幕 → 数字人视频 → 缩略图 + 清单
```

- **TTS**：numpy 逐字合成（基频 + 谐波 + 包络 + 确定性抖动），输出 24kHz 单声道 WAV；
- **字幕**：按每句真实时长生成 SRT，时间轴与音频一致；
- **视频**：Pillow 逐帧渲染（渐变背景 + 数字人头像 + 音频包络驱动的口型条 + 字幕），
  ffmpeg 编码 H.264 + AAC，2 fps 保证速度；
- **音色**：pitch / speed / timbre 三个参数直接作用在合成器上，克隆音色会分析参考音频的基频。

---

## 架构

```text
              ┌──────────────── 接入层 ────────────────┐
              │   REST /api  ·  Web 控制台  ·  Swagger  │
              │   CORS → X-Request-ID → 安全响应头      │
              └───────────────────┬────────────────────┘
                                  │
        ┌─────────────────────────▼──────────────────────────┐
        │                    任务队列 JobRunner               │
        │  单 worker · 步骤进度 · 协作式取消 · 重试 · 崩溃恢复 │
        └───────────┬──────────────────────────┬────────────┘
                    │                          │
        ┌───────────▼──────────┐    ┌──────────▼───────────┐
        │   生产管线 produce    │    │  模型服务 Manager     │
        │ 切句→TTS→字幕→渲染    │    │ 安装/启动/停止/健康   │
        └───────────┬──────────┘    └──────────┬───────────┘
                    │                          │
        ┌───────────▼──────────────────────────▼───────────┐
        │              推理引擎适配器 Engine                │
        │   LocalEngine（numpy + Pillow + ffmpeg）          │
        │   RemoteHTTPEngine（CosyVoice / MuseTalk 模板）   │
        └───────────────────────────────────────────────────┘
```

管线只依赖 `TTSEngine` / `AvatarEngine` 两个 Protocol，
换真实模型只改适配器，任务队列、API、前端都不用动。细节见 [架构与设计决策](docs/ARCHITECTURE.md)。

---

## 配置

全部通过环境变量注入，完整清单见 [`.env.example`](.env.example) 与 [运维手册](docs/OPERATIONS.md)。

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIGC_DATA_DIR` | `./data` | 数据根目录，派生 `media/` `models/` `uploads/` |
| `DATABASE_URL` | `sqlite:///./data/studio.db` | 换 PostgreSQL 只改这一行 |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | 可执行文件路径 |
| `AIGC_FFMPEG_TIMEOUT` | `300` | 单条 ffmpeg 命令超时（秒） |
| `AIGC_SAMPLE_RATE` | `24000` | 输出采样率，贯穿 TTS / 拼接 / 抽音轨 |
| `AIGC_WIDTH` / `AIGC_HEIGHT` / `AIGC_FPS` | `720` / `1280` / `2` | 默认分辨率与帧率 |
| `AIGC_MAX_WORKERS` | `1` | 队列并发 |
| `AIGC_REMOTE_TIMEOUT` | `120` | 远程推理请求超时（秒） |
| `CORS_ORIGINS` | `*` | 生产填具体域名 |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `text` | `json` 用于日志采集 |
| `DOCS_ENABLED` | `true` | 生产建议关闭 `/docs` |

---

## API 与集成

完整接口见 [`docs/API.md`](docs/API.md)，`examples/` 下是可直接执行的请求。

```bash
# 探针
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/ready

# 一键示例
curl -X POST http://127.0.0.1:8000/api/demo/run

# 自定义生产
curl -X POST http://127.0.0.1:8000/api/produce \
  -H "Content-Type: application/json" \
  -d '{"script":"大家好，这是一条测试。","voice_id":"voi_xxx","style":"news"}'

# 查任务（步骤 + 产物）
curl http://127.0.0.1:8000/api/jobs/{job_id}

# 下载成片
curl -OJ http://127.0.0.1:8000/api/assets/{asset_id}

# 安装并启动一个模型服务
curl -X POST http://127.0.0.1:8000/api/models/{model_id}/install
curl -X POST http://127.0.0.1:8000/api/models/{model_id}/start
```

所有错误响应统一为 `{"detail": ..., "request_id": ...}`，排查时按 `request_id` 检索日志。

### 接入真实模型

```bash
# 以 CosyVoice 为例：先部署推理服务，再把它指给平台
export AIGC_COSYVOICE_300M_URL=http://127.0.0.1:9880
```

模型服务需要暴露 `POST /tts`（返回 wav）或 `POST /lip-sync`（返回 mp4）；
`app/providers/adapters.py` 里有 HTTP 适配器模板，请求超时取 `AIGC_REMOTE_TIMEOUT`。

---

## 部署与运维

```bash
make install-dev     # 安装开发依赖（含测试与 ruff）
make test            # 测试 + 覆盖率（需要 ffmpeg）
make lint            # ruff 静态检查
make smoke           # 对已启动的服务做端到端冒烟
make docker-build    # 构建镜像（内含 ffmpeg 与中文字体）
```

- **探针**：`/api/health`（存活，不查库）、`/api/ready`（就绪：数据库 / ffmpeg / ffprobe / 数据目录可写）；
- **日志**：`LOG_FORMAT=json` 接采集平台，按 `request_id` 关联一次请求；
- **备份**：`sqlite3 data/studio.db ".backup ..."` + 打包 `data/media`、`data/uploads`、`data/models`；
- **扩容**：队列是进程内线程池，多机需要换外部队列 + 对象存储；内置接口不带鉴权，对外必须置于网关之后。

部署形态、故障排查表与上线检查清单见 [运维手册](docs/OPERATIONS.md)，
安全边界见 [SECURITY.md](SECURITY.md)，版本策略见 [版本与发布](docs/RELEASE.md)。

---

## 运行指标

本机实测（内置引擎，8–15 秒成片）：

| 指标 | 数值 |
|---|---|
| 一条 15.6 秒成片 | ~5 s（含 5 段 TTS、32 帧渲染、H.264 编码） |
| 渲染帧率 | 2 fps（15.6s 约 32 帧） |
| 单条成片体积 | ~1.5 MB（720×1280，15.6s） |
| 测试 | 38 passed（媒体 14 + 管线 5 + API 8 + 平台 11） |
| 模型安装模拟耗时 | ~0.5 s（可配 `AIGC_DOWNLOAD_STEP`） |

数字随机器、ffmpeg 版本与脚本长度波动，可用 `python scripts/demo.py` 复现。

---

## 版本与支持

- **版本号**：语义化版本，当前 `0.6.0`，变更记录见 [CHANGELOG](CHANGELOG.md)；
- **发布流程**：分支策略、检查清单与回滚见 [版本与发布](docs/RELEASE.md)；
- **质量门禁**：CI 跑 ruff + pytest（3.11 / 3.13，含真实 ffmpeg 编码与中文字体）+ HTTP 冒烟；
- **支持渠道**：缺陷与需求走 [Issues](https://github.com/Zzhaocc/aigc-avatar-studio/issues)，
  安全问题走 [Security Advisory](SECURITY.md)。

---

## 能力边界

| 项目 | 现状 |
|---|---|
| 模型权重 | 只管理服务元数据与生命周期，不下载权重；推理需外部服务或内置引擎 |
| 数字人画面 | 内置渲染器画的是抽象形象（圆盘头像 + 口型条），不是真人；接真实模型后画面来自模型 |
| FFmpeg 工具 | 4 个高频工具（探测 / 抽音轨 / 截封面 / 倍速），都是真实调用 |
| 形态 | 服务端 + Web 控制台，不做桌面端打包 |
| 直播 | 不做推流 |

接真实模型后，队列、管线、字幕、产物链路完全一致，只有推理后端和画面来源变化。

---

## Roadmap

| 版本 | 内容 | 状态 |
|---|---|---|
| v0.6 | 生产化加固：结构化日志与 request_id、探针、统一错误契约、容器化、运维与安全文档 | 已交付 |
| v0.7 | 真实 TTS 适配器（CosyVoice / IndexTTS），保留 `synthesize` 契约 | 规划中 |
| v0.8 | 对象存储与产物签名 URL；任务中间帧目录自动清理 | 规划中 |
| v0.9 | 外部队列 + 多副本；长脚本分片与断点续跑 | 规划中 |
| v1.0 | 数字人形象库与多模板；字幕样式可配置 | 规划中 |

---

## 文档

| 文档 | 内容 |
|---|---|
| [架构与设计决策](docs/ARCHITECTURE.md) | 分层、模型状态机、生产管线、可观测、安全边界、取舍 |
| [API 参考](docs/API.md) | 全部端点、探针、错误契约与示例 |
| [运维手册](docs/OPERATIONS.md) | 部署形态、探针、日志排障、备份、扩容、上线清单 |
| [版本与发布](docs/RELEASE.md) | 版本策略、分支与合并、发布清单、回滚 |
| [安全策略](SECURITY.md) | 安全边界与漏洞报告流程 |
| [贡献指南](CONTRIBUTING.md) | 本地开发、PR 前检查、如何接真实模型 |

---

## 许可

MIT License，见 [LICENSE](LICENSE)。示例脚本、音色与画面均为程序生成，不含真实人物素材；
产品形态来源见文末致谢。

<details>
<summary>产品形态来源</summary>

模型服务管理与数字人生产的产品形态参考开源桌面客户端
[AIGCPanel](https://github.com/modstart-lib/aigcpanel)。
</details>
