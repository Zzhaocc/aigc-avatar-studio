# AIGC Avatar Studio · AI 数字人内容生产平台

> 一段文案 → 一条带语音、字幕、数字人画面的视频。
> 服务端可运行：模型服务生命周期 + 真实媒体管线 + 任务队列 + FFmpeg 工具箱。

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square)]()
[![FFmpeg](https://img.shields.io/badge/FFmpeg-7.1-007808?style=flat-square)]()
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen?style=flat-square)]()
[![License](https://img.shields.io/badge/license-MIT-yellow?style=flat-square)]()

---

## 这是什么

参考项目 [AIGCPanel](https://github.com/modstart-lib/aigcpanel) 是一个 Electron 桌面端：
把 CosyVoice / MuseTalk / Wav2Lip 这类本地模型装进客户端，拼成"数字人生产工具"。

本项目做的是它的**服务端版本**，解决三件桌面端不好做的事：

| 桌面端的问题 | 本项目的做法 |
|---|---|
| 只能在有显卡的机器上单机跑 | 服务端部署：模型服务、任务队列、产物存储都在服务端，浏览器/API 接入 |
| 模型装了但状态不可观测 | 模型服务状态机（未安装→下载→已装→启动→运行→停止）+ 进度 + 端口 + 健康检查 |
| 生成一次就结束 | 任务队列 + 步骤级进度 + 取消 + 重试 + 产物清单，可批量、可恢复 |

**不需要 GPU、不需要模型权重、不需要 API key** —— 内置离线引擎用 numpy 合成语音、
用 Pillow + ffmpeg 渲染数字人视频，产出的是**真实可播放的 WAV / MP4 / SRT**。

---

## 30 秒跑起来

```bash
cd AIGC-Avatar-Studio
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000>，点「一键示例」，等 3–6 秒就能看到成片。

```bash
# 命令行跑完整链路（不需要起服务）
python scripts/demo.py
```

依赖只有 `ffmpeg`（已在 PATH 即可）。

---

## 控制台

![控制台](docs/screenshots/console-studio.jpg)

工作台四块：脚本输入 → 音色/风格/分辨率 → 当前任务（进度、步骤、视频播放）→ 最近任务。

| 模型服务 | 音色 |
|---|---|
| ![模型](docs/screenshots/console-models.jpg) | ![音色](docs/screenshots/console-voices.jpg) |

---

## 它能做什么

### 1. 模型服务生命周期

8 个模型服务（CosyVoice-300M / IndexTTS / FishSpeech / FunASR / MuseTalk / Wav2Lip / LatentSync / 内置引擎），
每个都有真实的安装目录、进度、端口与健康检查：

```text
not_installed →(install)→ downloading 0%…100% → installed
installed →(start)→ starting → running →(stop)→ stopped
任意可逆状态 →(uninstall)→ not_installed
```

安装会真的在 `data/models/<key>/` 下落目录与清单；启动会分配端口、写 `runtime.json`；
健康检查按端口探测。**推理后端由适配器决定**：内置引擎直接可用，其它模型需要把
`AIGC_<KEY>_URL` 指向真实服务（`app/providers/adapters.py` 里有 HTTP 适配器模板）。

### 2. 真实媒体管线

```text
脚本 → 切句（≤28 字）→ 逐句 TTS → 拼接 + 句间静音 → 时间轴字幕 → 数字人视频 → 缩略图 + 清单
```

- **TTS**：numpy 逐字合成（基频 + 谐波 + 包络 + 确定性抖动），输出 24kHz 单声道 WAV；
- **字幕**：按每句真实时长生成 SRT，时间轴与音频一致；
- **视频**：Pillow 逐帧渲染（渐变背景 + 数字人头像 + 音频包络驱动的口型条 + 字幕），
  ffmpeg 编码 H.264 + AAC，2 fps 保证速度，成片可直接播放；
- **音色**：pitch / speed / timbre 三个参数直接作用在合成器上，克隆音色会分析参考音频的基频。

### 3. 任务队列

单 worker 顺序执行（对应单卡 GPU 的真实约束），任务与步骤分离：

- 5 个步骤各自报进度与耗时：script / tts / subtitle / avatar / manifest
- 取消是协作式的（每个句子前检查），1 秒内生效
- 失败可一键重试；进程重启会把中断任务标为 failed，不会永远 running

### 4. 工具箱（真实 ffmpeg）

对任意产物做 probe / 提取音轨 / 截封面 / 调倍速，结果作为新产物落库。

### 5. 可观测

每个任务有步骤耗时、产物大小、时长；`/api/metrics` 给出成功率、平均耗时、产物体积、模型磁盘占用。

---

## 架构

```text
              ┌──────────────── 接入层 ────────────────┐
              │   REST /api  ·  Web 控制台  ·  Swagger  │
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
        └──────────────────────────────────────────────────┘
```

**边界**：管线只依赖 `TTSEngine` / `AvatarEngine` 两个 Protocol，
换真实模型只改适配器，任务队列、API、前端都不用动。

---

## API 示例

完整接口见 [`docs/API.md`](docs/API.md)，`examples/` 下是可直接执行的请求。

```bash
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

---

## 测试

```bash
python -m pytest -q     # 27 passed
```

覆盖：媒体引擎（WAV 合成 / 确定性 / SRT 时间轴 / MP4 编码 / ffmpeg 工具）、
生产管线端到端、任务取消与重试、模型状态机、API 契约。

---

## 与参考项目的差异（如实说明）

| 参考项目 AIGCPanel | 本项目 |
|---|---|
| Electron 桌面客户端（TypeScript + Vue） | FastAPI 服务端 + 零依赖 Web 控制台 |
| 管理本地模型进程（真实权重下载） | 管理模型服务**元数据与生命周期**；权重下载是模拟，推理走适配器 |
| 内置 27 个 FFmpeg 工具 | 4 个高频工具（探测/抽音轨/截封面/倍速），代码更少但都是真调用 |
| 数字人依赖 MuseTalk 等模型 | 内置离线渲染器，输出真实 MP4；接真实模型改 `AIGC_<KEY>_URL` |
| 面向个人创作者 | 面向服务端批量生产（队列、取消、重试、指标） |

**没有做的事**：不下载真实模型权重、不做直播推流、不做桌面打包。
这些是产品功能，不是这个项目的重点；本项目证明的是"数字人生产可以工程化"。

---

## 目录

```text
AIGC-Avatar-Studio/
├── app/
│   ├── main.py                 # 应用装配、启动恢复
│   ├── config.py / db.py / models.py
│   ├── api/                    # REST 路由 + 请求模型
│   ├── providers/
│   │   ├── catalog.py          # 8 个模型服务 + 预设音色
│   │   ├── manager.py          # 安装/启动/停止/卸载/健康状态机
│   │   ├── adapters.py         # LocalEngine + RemoteHTTPEngine 模板
│   │   └── base.py             # TTS/Avatar/ASR Protocol
│   ├── pipeline/
│   │   ├── script.py           # 切句与时长估算
│   │   ├── produce.py          # 五步生产管线
│   │   └── jobs.py             # 队列、进度、取消、重试
│   └── media/                  # TTS(WAV) / 字幕(SRT) / 视频(MP4) 引擎
├── web/                        # 单页控制台
├── tests/                      # 媒体 + 管线 + API 测试
├── examples/                   # curl / .http 示例
├── scripts/                    # demo / 冒烟 / 截图
└── docs/                       # 架构、API、截图
```

## License

MIT
