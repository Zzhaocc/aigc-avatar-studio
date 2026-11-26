# 贡献指南

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                  # 运行时依赖 + pytest/ruff

python -m pytest -q                    # 38 个测试（媒体测试会真的编码 MP4，约 30 秒）
python -m ruff check app tests scripts
python -m uvicorn app.main:app --port 8000
```

**ffmpeg / ffprobe 必须在 PATH 里**（或用 `FFMPEG_BIN` / `FFPROBE_BIN` 指定绝对路径）。
零配置即可跑：内置引擎用 numpy + Pillow + ffmpeg 生成真实音视频，不需要 GPU、权重或 API key。
部署与排障见 [运维手册](docs/OPERATIONS.md)。

## 提 PR 前

1. `python -m pytest -q` 全绿；
2. `python -m ruff check app tests scripts` 无告警；
3. 新增引擎实现 `app/providers/base.py` 里的 Protocol，不要绕过 `produce` 直接写路由逻辑；
4. 新增工具走 `POST /api/tools/{tool}` 的约定：输入 `asset_id`，输出 `result` + 可选新 `asset`；
5. 模型目录只加元数据，不要在仓库里放权重；下载逻辑保持"可取消、可观测"；
6. 改了对外接口，同步更新 `docs/API.md`；改了配置项，同步更新 `.env.example` 与 `docs/OPERATIONS.md`。

## 接入真实模型

参考 `app/providers/adapters.py` 的 `RemoteHTTPEngine`：

1. 模型服务暴露 `POST /tts`（返回 wav）或 `POST /lip-sync`（返回 mp4）；
2. 设置环境变量 `AIGC_<MODEL_KEY>_URL`；
3. 在控制台选该模型生产 —— 未配置时会明确报 409，而不是静默降级。

适配器里不要写死超时与可执行路径：请求超时取 `settings.remote_timeout`，
ffmpeg 路径取 `avatar.ffmpeg_bin()`，采样率取 `settings.sample_rate`。

## 数据合规

- 示例文案、音色、头像均为程序生成，不含真人肖像与声音；
- 不要提交真实客户素材、未授权音色或品牌内容；
- 删除克隆音色时应同时清理 `uploads/` 下的参考音频（现有实现已这样做，改动时请保持）。

## 提交信息

用 `feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `chore:` 前缀，一句话说清改了什么、为什么。
