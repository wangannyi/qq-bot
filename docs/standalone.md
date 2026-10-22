# 可选独立机器人

`qq-support-bot` 是不依赖 Hermes 的基础实现，提供命令行检索、群消息问答和日报。它与推荐的 Hermes 集成能力不同，不包含全部上下文、来源校验和连接恢复增强。首次部署优先使用 [标准搭建流程](setup.md)。

## 1. 安装与本地验证

从仓库根目录执行：

```bash
.venv/bin/python -m pip install -r qq-support-bot/requirements.txt
.venv/bin/python scripts/standalone.py --help
.venv/bin/python scripts/standalone.py status
```

`status` 可能检查配置的本地模型服务；它不是 QQ 连接证明。这个模块使用自己的数据库，不与 Hermes 索引自动互换。

## 2. 配置与同步

在自己的私有 shell 环境设置：

```bash
export LLM_BASE_URL="http://127.0.0.1:8080/v1"
export LLM_MODEL="YOUR_MODEL_ID"
export KB_DB_PATH="$HOME/.local/share/qq-bot-standalone/support.db"
.venv/bin/python scripts/standalone.py sync
.venv/bin/python scripts/standalone.py ask "K1 示例问题：如何查找适用系统的官方文档？"
```

请自行启动兼容 OpenAI 的本地模型服务，并替换真实模型名。此模块的模型调用没有密钥鉴权配置，适合受控本机服务；不要直接接到需要密钥的公共端点。同步会联网下载官方资料，`ask` 会调用模型；它们不是离线演示。

## 3. 手动接入 QQ

仅在自己的官方测试应用中设置 `QQ_APP_ID` 和 `QQ_APP_SECRET` 后，手动执行：

```bash
.venv/bin/python scripts/standalone.py bot
```

命令会连接 QQ 并回复应用收到的群 @ 消息和好友消息。它不使用 Hermes 的允许名单配置；请通过 QQ 平台限制测试范围，不要把此入口直接用于不受控的公开接入。按 `Ctrl+C` 停止。

独立入口启动后会周期同步文档，间隔由 `DOC_SYNC_INTERVAL_HOURS` 控制，默认 24 小时。只有明确设置 `DAILY_REPORT_GROUP_OPENID` 才启用定时群日报，时间由 `DAILY_REPORT_TIME` 控制，默认 `21:00`。不要在不了解这些行为时填入正式群目标。

## 4. 用户命令

独立机器人的 `/帮助`、`/状态`、`/日报` 由快捷问答处理。普通问题通过本模块的检索和模型答复。它们不能代替 Hermes 集成的验收；也不要让两种入口同时登录同一正式 QQ 应用。
