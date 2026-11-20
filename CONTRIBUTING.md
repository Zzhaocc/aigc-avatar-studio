# 贡献指南

## 本地开发

```bash
pip install -r requirements.txt          # 需要 ffmpeg 在 PATH
python -m pytest -q
python -m uvicorn app.main:app --port 8000
```

零配置即可跑：内置引擎用 numpy + Pillow + ffmpeg 生成真实音视频，不需要 GPU、权重或 API key。

## 提 PR 前

1. `python -m pytest -q` 全绿（媒体测试会真的编码 MP4，约 20 秒）；
2. 新增引擎实现 `app/providers/base.py` 里的 Protocol，不要绕过 `produce` 直接写路由逻辑；
3. 新增工具走 `POST /api/tools/{tool}` 的约定：输入 `asset_id`，输出 `result` + 可选新 `asset`；
4. 模型目录只加元数据，不要在仓库里放权重；下载逻辑保持"可取消、可观测"。

## 接入真实模型

参考 `app/providers/adapters.py` 的 `RemoteHTTPEngine`：

1. 模型服务暴露 `POST /tts`（返回 wav）或 `POST /lip-sync`（返回 mp4）；
2. 设置环境变量 `AIGC_<MODEL_KEY>_URL`；
3. 在控制台选该模型生产 —— 未配置时会明确报 409，而不是静默降级。

## 数据合规

- 示例文案、音色、头像均为程序生成，不含真人肖像与声音；
- 不要提交真实客户素材、未授权音色或品牌内容。
