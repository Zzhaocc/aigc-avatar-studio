# 架构与设计决策

> 一句话：把「数字人视频生产」拆成**可管理的模型服务 + 可观测的任务管线 + 可替换的推理引擎**。

## 1. 目标与非目标

**目标**

1. 服务端可部署：模型、队列、产物都在服务端，浏览器 / API 接入；
2. 生产可观测：每一步有进度、耗时、产物，失败可定位、可重试；
3. 引擎可替换：内置离线引擎保证零依赖演示，真实模型通过适配器接入；
4. 产物是真的：WAV / SRT / MP4 都能被播放器打开，而不是占位 JSON。

**非目标**

- 不做 Electron 桌面打包（参考项目 AIGCPanel 的形态）；
- 不下载真实模型权重（GB 级，且需要 GPU/许可证）；模型目录是**元数据 + 生命周期**；
- 不做直播推流、不做 27 个 FFmpeg 工具的全集。

---

## 2. 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ 接入层    FastAPI 路由 / Web 控制台 / Swagger / 静态产物下载  │
├──────────────────────────────────────────────────────────────┤
│ 编排层    JobRunner（单 worker、步骤、进度、取消、重试、恢复）│
├──────────────────────────────────────────────────────────────┤
│ 业务层    produce：切句 → TTS → 字幕 → 渲染 → 清单            │
│           ModelManager：安装/启动/停止/卸载/健康              │
├──────────────────────────────────────────────────────────────┤
│ 引擎层    LocalEngine（numpy + Pillow + ffmpeg）              │
│           RemoteHTTPEngine（CosyVoice / MuseTalk 模板）       │
├──────────────────────────────────────────────────────────────┤
│ 支撑层    SQLAlchemy 模型 / 配置 / 文件存储                   │
└──────────────────────────────────────────────────────────────┘
```

依赖方向自上而下；`pipeline` 只依赖 `TTSEngine` / `AvatarEngine` 两个 Protocol。

---

## 3. 模型服务状态机

```text
                 install                 start            stop
not_installed ──────────► downloading ──► installed ──► starting ──► running ──► stopped
      ▲                       │ 100%         ▲                          │            │
      │                       └──────────────┘                          │            │
      └──────────────────────── uninstall ◄─────────────────────────────┴────────────┘
```

- **安装**：后台线程写进度（5/15/…/90%），完成后在 `data/models/<key>/` 落下
  `manifest.json` 与占位说明；目录、`installed_at`、`progress` 都进数据库。
- **启动**：分配端口（19100 起，跳过占用）→ `starting` → 写 `runtime.json` → `running`。
- **健康检查**：内置引擎直接 healthy；其它模型按端口 `socket.connect` 探测，
  返回 `reachable / latency_ms`，并明确说明"演示环境没有真实推理进程"。
- **并发**：状态迁移走全局锁；重复安装/启动返回 409，不会产生双实例。

> 为什么把"下载"做成模拟：真实权重 1–5 GB、需要 GPU 与模型许可，
> 不适合放进一个可 clone 就跑的仓库。状态机、目录、端口、健康检查是真的，
> 只有"字节流"是假的 —— 这是本仓库最明确的一条边界。

---

## 4. 生产管线

```text
script ──► 切句(≤28字) ──► 逐句 TTS ──► 拼接(+0.15s 静音) ──► 时间轴字幕
                                              │
                                              ▼
                                     数字人逐帧渲染 + ffmpeg 封装
                                              │
                                              ▼
                                     MP4 / WAV / SRT / 封面 / 清单
```

时间轴计算与音频严格一致：

```python
cursor = 0
for i, (text, duration) in enumerate(zip(segments, durations)):
    if i: cursor += 0.15          # 与 concat_wavs 的句间静音一致
    start, end = cursor, cursor + duration
    cursor = end
```

这是本项目最容易出错的地方（字幕与语音漂移），所以 `tests/test_pipeline.py`
断言 `srt.duration ≈ audio.duration`，`tests/test_media.py` 断言
`video.duration ≈ audio.duration ±0.6s`。

---

## 5. 任务队列

- **单 worker**：`ThreadPoolExecutor(max_workers=1)`。对应真实场景"一张 GPU 顺序跑"，
  也避免多任务同时占用显存。
- **任务/步骤分离**：`Job` 存状态与产物，`JobStep` 存 5 个步骤的状态、耗时、说明。
- **取消**：协作式。`Reporter.check_cancel()` 在每句合成前、渲染前各检查一次，
  1 秒内生效；排队中的任务直接标记 cancelled。
- **重试**：复制原任务生成新 job，保留原任务用于对比。
- **恢复**：启动时把 `queued/running` 一律标 failed（"进程重启中断"），
  避免前端永远转圈。

---

## 6. 媒体实现

| 环节 | 做法 | 为什么这样做 |
|---|---|---|
| TTS | numpy 逐字合成：基频 + 2–3 次谐波 + 攻击/衰减包络 + md5 派生的确定性抖动 | 真实可听的语音，且无需模型与网络；确定性保证测试可复现 |
| 时长 | 每字 0.16s / speed，标点 0.12s，空白 0.05s | 时长可预测，方便前端估算与测试断言 |
| 字幕 | 按每句真实时长生成 SRT | 与音频严格对齐 |
| 画面 | Pillow 逐帧：渐变背景 + 头像圆盘 + 音频包络驱动的口型条 + 字幕 + 水印 | 不依赖 libass / 字体滤镜，跨平台稳定 |
| 编码 | `ffmpeg -framerate 2 -i frame_%04d.png -i audio.wav -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest` | 2 fps 足够演示，编码 1 秒内完成 |
| 封面 | `ffmpeg -ss t -frames:v 1` | 工具箱真实调用 |

---

## 7. 引擎适配器边界

```python
class TTSEngine(Protocol):
    def synthesize(text, out_path, *, pitch, speed, timbre) -> dict: ...
    def concat(paths, out_path) -> dict: ...

class AvatarEngine(Protocol):
    def render(audio, segments, out_path, *, title, style, width, height, fps) -> dict: ...
```

接入真实模型（以 CosyVoice 为例）：

1. 部署 CosyVoice 服务，暴露 `POST /tts`；
2. 设置 `AIGC_COSYVOICE_300M_URL=http://127.0.0.1:9880`；
3. 在控制台选中该模型 → 生产时 `engine_for()` 返回 `RemoteHTTPEngine`。

`app/providers/adapters.py` 里给出了 HTTP 适配器模板（返回 wav / mp4 字节流）。
**没有配置后端时，选择真实模型会返回 409 并说明原因**，而不是静默降级 —— 避免"以为在跑大模型"。

---

## 8. 数据模型

| 表 | 作用 | 关键字段 |
|---|---|---|
| `model_services` | 模型服务 | key / kind / status / progress / port / path |
| `voice_profiles` | 音色 | kind(preset/custom/cloned) / pitch / speed / timbre / reference_path |
| `jobs` | 生产任务 | status / progress / input / output / error / duration_ms |
| `job_steps` | 步骤 | seq / name / status / detail / latency_ms |
| `assets` | 产物 | kind / filename / path / size_bytes / duration / mime |

产物文件落在 `data/media/<job_id>/`，数据库只存路径；下载接口校验路径必须在
`data/` 下（防目录穿越），文件被清理时返回 410。

---

## 9. 关键取舍

| 决策 | 选择 | 理由 | 代价 |
|---|---|---|---|
| 形态 | 服务端而不是桌面端 | 队列、批量、多端接入是服务端的强项 | 没有"双击安装"的体验 |
| 模型权重 | 只做生命周期，不下载 | 可 clone 即跑；避免 GB 级依赖 | 真实推理需要自己接 |
| 渲染 | 自绘帧 + ffmpeg | 不依赖 libass / 特定字体 | 画面是抽象的，不是真人 |
| 帧率 | 2 fps | 编码快、仓库小 | 画面不流畅（数字人口型本来也不需要高帧率） |
| 并发 | 单 worker | 对应单卡 GPU | 吞吐靠队列，不靠并发 |
| 存储 | SQLite + 本地文件 | 零依赖演示 | 生产要换对象存储 + PG |
| 取消 | 协作式检查点 | 简单可靠 | 正在执行的 ffmpeg 不会被打断（秒级） |

---

## 10. 失败模式与对策

| 场景 | 现状 |
|---|---|
| 脚本为空 | API 422（Pydantic `min_length=1`） |
| 模型未装/未启动 | 409 + 明确提示 |
| 选了真实模型但没配后端 | 409 + 环境变量名 |
| ffmpeg 失败 | 抛出 `RuntimeError`，任务 failed，错误进 `job.error` |
| 任务卡住/进程崩 | 启动时 `recover()` 标 failed，可重试 |
| 磁盘文件被删 | 下载接口 410 |
| 并发安装同一模型 | 状态锁 + 409 |

---

## 11. 实测数据（本机，内置引擎）

| 指标 | 数值 |
|---|---|
| 一条 15.6 秒成片耗时 | ~5.6 s（含 5 段 TTS、32 帧渲染、H.264 编码） |
| 渲染帧率 | 2 fps，15.6s 约 32 帧 |
| 单条成片体积 | 1.5 MB（540×960，15.6s） |
| 测试 | 27 passed（媒体 14 + 管线 5 + API 8） |
| 模型安装模拟耗时 | ~0.6 s（可配 `AIGC_DOWNLOAD_STEP`） |
