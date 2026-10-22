# 配置参考

推荐路径使用 Hermes profile 中的 `config.yaml` 和 `.env`。起点是 [空白配置](../examples/config.yaml) 与 [环境变量示例](../examples/env.example)；安装器会生成资料检索和插件的位置配置。

## 1. 目录与数据

| 变量 | 用途与默认行为 |
| --- | --- |
| `HERMES_HOME` | 当前独立 profile 的绝对路径；凭据操作必须明确设置，不读取其他 profile |
| `QQ_BOT_DATA_DIR` | 可写数据目录；默认使用 `XDG_DATA_HOME/qq-bot`，未设置 XDG 时为用户数据目录 |
| `QQ_BOT_INDEX` | 可选的 SQLite 文档索引路径覆盖 |
| `QQ_BOT_HERMES_BIN` | 日报等功能调用的 Hermes 命令；默认在 PATH 中找 `hermes` |
| `QQ_BOT_FAQ_REPO` | 可选、自行维护的 FAQ Git 工作目录；没有默认公共仓库 |
| `QQ_BOT_FAQ_URL` | 可选 FAQ 对外地址 |
| `QQ_BOT_RULES_DOCX` | 可选、自行提供的赛事规则 DOCX |
| `QQ_BOT_RULES_POLICY` | 可选 DOCX 提取规则，结构参考 [示例](../examples/organizer-policy.example.json) |
| `QQ_BOT_ROOT` | 手动安装插件时定位本仓库；标准安装使用插件的 `location.json` |

环境变量应在启动命令及你自行创建的服务环境中保持一致。改用另一个数据目录会得到不同的索引，不会迁移旧数据。

## 2. 模型

| 配置项 | 说明 |
| --- | --- |
| `model.provider` | 当前服务商标识；示例为 `custom:example` |
| `model.default` | 服务商支持的模型 ID，必须替换示例值 |
| `custom_providers[].name` | 自定义服务商名称，应与 provider 后缀对应 |
| `custom_providers[].base_url` | 远程模型接口地址，必须替换 `example.invalid` |
| `custom_providers[].key_env` | Hermes 使用的密钥环境变量名；示例与 `model_api_key_env` 保持一致 |
| `custom_providers[].api_mode` | Hermes 的接口协议；与服务商和 `summary_transport` 保持一致 |
| `cie_support.model_api_key_env` | 当前进程中密钥环境变量名；示例为 `QQ_BOT_MODEL_API_KEY` |
| `cie_support.summary_transport` | `chat_completions` 或 `anthropic_messages`；根据接口协议选择 |
| `cie_support.answer_backend` | 默认 `independent`；`website` 是显式选择的网站问答路径 |
| `cie_support.supplemental_search` | 默认 `false`；独立问答不会默认附加外部搜索 |

设置 `model_api_key_env` 后，该变量为空会报错，不会借用别的 profile 的凭据。没有设置这个配置项时，代码可读取当前 profile 的 `auth.json` 中与 `model.provider` 对应的 `credential_pool`。首次搭建建议直接使用环境变量方式。

模型请求可能包含问题和检索到的资料片段。只接入你认可的数据处理方；不要把私有日志或密钥放进问题。

## 3. QQ 官方接入

| 配置项 | 默认与用途 |
| --- | --- |
| `platforms.qqbot.enabled` | `false`；运维人员配置完成后手动启用 |
| `extra.app_id` / `extra.client_secret` | 默认空值；自己的 QQ 应用凭据 |
| `QQ_APP_ID` / `QQ_CLIENT_SECRET` | 上述字段为空时的环境变量来源 |
| `extra.dm_policy` | 示例为 `disabled`，禁用私聊 |
| `extra.group_policy` | 示例为 `allowlist` |
| `extra.group_allow_from` | 默认空数组；填写允许的群 OpenID |
| `extra.allow_from` | 默认空数组；允许的用户 OpenID，配合相应私聊策略使用 |
| `extra.markdown_support` | 示例为 `true` |
| `extra.compact_mobile` | 示例为 `false` |
| `group_sessions_per_user` | `true`，群内不同用户分开保留上下文 |
| `max_concurrent_sessions` | 示例为 `3`；需结合模型配额调整 |

锁定版本的 Hermes 在显式启动网关时，可能根据非空 QQ 凭据环境变量启用接入，即使 YAML 中仍为 `enabled: false`。因此首次安装与离线验证应使用没有 QQ 凭据的环境；配置文件的开关不能替代凭据隔离。安装器本身不会启动网关。

默认空允许名单不会授权测试群。OpenID 与展示群号不同。不要为了快速验证将访问策略放开。

## 4. 安装器生成的集成

`plugins.enabled` 包含 `cie-answer-policy`。`mcp_servers.spacemit_docs` 指向本仓库的 `rag_server.py`。`quick_commands.daily` 执行群日报预览命令，不自动发送日报或发布 FAQ。`platform_toolsets.qqbot: []` 与插件的工具拦截共同限制 QQ 会话执行通用 agent 工具；不要将它改为开放终端执行。

安装器会记录本仓库和 Hermes 的绝对路径。移动代码或环境后，应在停机维护时检查这些位置。不要用空白模板覆盖已配置 profile；先备份并逐项更新。

## 5. 可选独立机器人

`qq-support-bot` 提供另一个入口，和 Hermes 方案的配置、命令及能力不同。它使用 `QQ_APP_ID`、`QQ_APP_SECRET`、`LLM_BASE_URL`、`LLM_MODEL`、`KB_DB_PATH` 等环境变量。其默认模型接口是本机兼容 OpenAI 的服务，不能据此认为 Hermes 已配置成功。

不要同时用两条路径接入同一个正式 QQ 应用，以免重复答复或连接互相影响。使用步骤见 [独立模块指南](standalone.md)。标准搭建和验收以 [Hermes 搭建指南](setup.md) 为准。
