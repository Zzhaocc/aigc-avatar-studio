# 变更记录

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.6.0] - 2026-11-26

生产化加固版本：补齐可观测性、探针、容器化与运维文档，并修掉配置项"写了但没生效"的问题。
生产管线的产出与 0.5.0 保持一致。

### 修复

- **`FFMPEG_BIN` / `FFPROBE_BIN` 之前只写在 `.env.example`，渲染器的 6 处命令里全是
  硬编码的 `"ffmpeg"` / `"ffprobe"`** —— 现在每次调用按配置取路径。
- **`AIGC_SAMPLE_RATE` 之前是死配置** —— 现在贯穿 TTS、音频拼接、抽取音轨。
- **`AIGC_DATA_DIR` 之前只影响库文件** —— 现在由它派生 `media/`、`models/`、`uploads/`，
  容器里只挂一个卷即可；原来这三个目录写死在源码目录下，容器化写不进去。
- **产物路径校验用字符串前缀比较**，`/data-evil` 这类同前缀目录可以绕过 ——
  改为 `Path.is_relative_to`，越界返回 403（原来是误报 410）。
- `AIGC_REMOTE_TIMEOUT` 之前绕过配置直接读环境变量，现在统一走 `Settings`。
- 时间字段统一输出 UTC ISO8601（`Z` 结尾），不再依赖数据库返回的 naive datetime。
- `JobStep` 耗时在 PostgreSQL（aware datetime）下会算错，已做兼容。
- 任务取消集合会无限增长（每个被取消过的 job_id 永久驻留）—— 任务结束即移除。
- 删除任务改为整目录清理（`shutil.rmtree`），不再残留空目录 / 漏计数。
- 删除克隆音色时不清理参考音频，`uploads/` 会持续增长 —— 现在一并删除。
- ffmpeg/ffprobe 失败信息缺少退出码：被外部杀掉（OOM / 杀软 / 容器驱逐）时 stderr
  里没有错误行，现场只看到截断的编码进度。现在消息里带上退出码（Windows 的负退出码
  会转成可读值），能区分"编码失败"和"进程被外部终止"。

### 新增

- **可观测性**：结构化日志（`text` / `json`），每条日志带 `request_id`，可在一次任务的全部步骤间串联；
  日志级别与格式由环境变量控制；stdout 关闭时不会反过来打断业务线程。
- **请求中间件**：关联 ID（可透传上游 `X-Request-ID`）、统一访问日志、基础安全响应头。
- **统一错误契约**：所有错误响应带 `request_id`；`422` 结构化返回逐字段校验原因；未预期异常不回显堆栈。
- **探针分离**：`/api/health` 为存活探针（不查库、不调 ffmpeg），`/api/ready` 为就绪探针
  （数据库 / ffmpeg / ffprobe / 数据目录可写），失败返回 `503`。
- **ffmpeg 命令超时**（`AIGC_FFMPEG_TIMEOUT`）：异常素材不会把 worker 永久卡住。
- **部署资产**：多阶段 `Dockerfile`（内置 ffmpeg 与中文字体、非 root、HEALTHCHECK）、
  `docker-compose.yml`、`.dockerignore`、`Makefile`、`.editorconfig`。
- **工程规范**：`pyproject.toml` 补全元数据与 ruff / pytest / coverage 配置，
  拆分 `requirements-dev.txt`，CI 增加 ruff 与覆盖率并安装中文字体，
  新增 `SECURITY.md`、`docs/OPERATIONS.md`。
- **测试**：新增 11 条平台级用例，覆盖探针、request_id、安全响应头、错误契约、产物路径校验、
  配置解析，以及 ffmpeg 路径与数据目录两条回归用例。

### 变更

- CORS 来源改为可配置（`CORS_ORIGINS`），并修正通配来源与凭证同时开启的不兼容配置。
- `/api/catalog` 增加 `version` 字段。
- 模型服务安装/启动与任务生命周期补充结构化日志，替代 `traceback.print_exc()`。
- 文档中补齐可观测、安全与部署章节，并把探针、数据目录、超时等新增配置写进运维手册。

## [0.5.0] - 2026-01-18

首个可运行版本。

### 新增

- 8 个模型服务 + 生命周期状态机（安装/启动/停止/卸载、进度、端口分配、健康检查、崩溃收敛）。
- 五步生产管线：切句 → 逐句 TTS → 拼接 → 时间轴字幕 → 数字人视频 → 缩略图与清单。
- 内置离线引擎：numpy 确定性语音合成、Pillow 逐帧渲染 + ffmpeg H.264/AAC 封装。
- 引擎适配器：`LocalEngine` 与 `RemoteHTTPEngine` 模板，按 `AIGC_<KEY>_URL` 切换。
- 任务队列：单 worker、步骤级进度、协作式取消、重试、启动崩溃恢复。
- FFmpeg 工具箱：探测 / 抽音轨 / 截封面 / 变速，产物落库。
- 零依赖 Web 控制台、CLI 端到端脚本、HTTP 冒烟脚本。
