---
name: 缺陷报告
about: 报告可复现的问题（安全漏洞请走 SECURITY.md，不要在这里公开细节）
title: "[bug] "
labels: bug
---

## 环境

- 版本（`GET /api/health` 的 `version`）：
- 部署方式：Docker / Docker Compose / 本地 uvicorn
- 操作系统、Python、ffmpeg 版本（`ffmpeg -version`）：
- 是否使用了真实模型后端（`AIGC_<KEY>_URL`）：

## 复现步骤

1.
2.

## 期望行为

## 实际行为

## 报错信息

<!--
接口报错请附响应体里的 request_id（服务端日志按该 ID 可查到完整链路）；
任务失败请附 job_id、失败的步骤名与 GET /api/jobs/{id} 里的 error；
模型服务异常请附模型 key 与 GET /api/models/{id} 的 status / last_error。
-->

```text

```
