# 搭建指南

本指南使用独立 Linux 主机上的 Hermes 接入 QQ 官方机器人。先完成不联网调用模型的离线验证，再配置自己的模型和 QQ 应用。安装不会启动后台服务、创建定时任务或发送消息。

## 1. 准备环境

需要 Git、Python 3.11、3.12 或 3.13，以及 Python 的 venv、pip 和 SQLite FTS5 支持。下面的 `python3` 必须指向这个版本范围；系统默认版本不符合时，改用 `python3.11` 等命令。

```bash
git clone https://github.com/wangannyi/qq-bot.git
cd qq-bot
python3 scripts/bootstrap.py --venv .venv --dev
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/offline_demo.py
.venv/bin/python -m pytest -q
```

首次安装依赖需要访问 Python 包仓库。离线演示使用合成资料，不连接模型、QQ 或正式资料库。`doctor.py` 的检查应全部为 `true`；演示和测试应成功退出。

## 2. 安装固定版本的 Hermes

请选择新的专用目录。不要把生产中的 Hermes 目录交给安装脚本。

```bash
.venv/bin/python scripts/prepare_hermes.py \
  --checkout "$HOME/work/hermes-qq" --install-deps
.venv/bin/python scripts/prepare_hermes.py \
  --checkout "$HOME/work/hermes-qq" --check-only
```

脚本下载 [Hermes](https://github.com/NousResearch/hermes-agent)，检出 [锁定的提交](../patches/hermes-lock.json)，校验原始文件后应用 [QQ 集成补丁](../patches/hermes-qq.patch)，并安装消息接入依赖。输出中的 `patch_state` 应为 `patched`。这一步需要访问 GitHub 和 Python 包仓库。

重复执行会检查补丁状态。遇到其他提交、部分补丁或无关改动时，脚本停止；请保留旧目录并使用新的专用目录。不要用忽略错误或强行覆盖来继续安装。

## 3. 创建独立配置

```bash
.venv/bin/python scripts/install_profile.py \
  --profile-dir "$HOME/.config/qq-bot/profile" \
  --hermes-checkout "$HOME/work/hermes-qq"
export HERMES_HOME="$HOME/.config/qq-bot/profile"
export QQ_BOT_DATA_DIR="$HOME/.local/share/qq-bot"
export QQ_BOT_HERMES_BIN="$HOME/work/hermes-qq/venv/bin/hermes"
```

这会生成禁用 QQ 的空白配置、空密钥环境文件和问答插件，并写入当前代码路径。请保持代码目录位置固定。安装器拒绝覆盖已经定制的文件；完成定制后不要用安装器覆盖升级。

编辑 `$HERMES_HOME/config.yaml`：

- 将 `model.default` 改为服务商提供的准确模型名称。
- 将 `custom_providers` 中的 `base_url` 改为自己的服务地址。
- 保持 `model.provider: custom:example` 与配置中的服务商名称 `example` 对应，或同步修改两者。
- 按服务商接口设置 `cie_support.summary_transport`。兼容 OpenAI 的接口使用 `chat_completions`；Anthropic Messages 接口使用 `anthropic_messages`；同时按协议修改 `custom_providers[].api_mode`。保持两处密钥环境变量名一致。

将 `$HERMES_HOME/.env` 中的 `QQ_BOT_MODEL_API_KEY` 填为自己的密钥。不要把它写入代码或提交 Git。配置细节见 [配置参考](configuration.md)。独立运行日报等命令时也需要把私有环境文件加载到当前进程；仅加载自己创建且检查过的文件：

```bash
set -a
. "$HERMES_HOME/.env"
set +a
```

```bash
.venv/bin/python scripts/doctor.py \
  --profile-dir "$HERMES_HOME" \
  --hermes-checkout "$HOME/work/hermes-qq"
```

这些检查只验证本地依赖和结构，不验证模型账户余额、QQ 权限或远端连接。

## 4. 建立资料索引

```bash
.venv/bin/python cie-qq-ops/sync_docs.py
```

这一步会联网获取程序配置的官方文档并建立本地索引。只同步你有权访问、存储和使用的资料；本项目的 MIT 许可证不替代资料本身的授权。首次同步可能需要较长时间，请等待结束并查看失败统计。没有可用索引时不要跳过资料保护去强行生成答案。

赛事规则 DOCX 和 FAQ 是可选输入，需要你自行提供并明确配置路径。仓库不附带真实赛事文档、用户问答记录或生产数据库。

## 5. 接入自己的 QQ 应用

在 [QQ 开放平台](https://q.qq.com/) 创建和配置自己的官方机器人，按控制台要求完成测试成员、测试群和权限设置。平台功能与审核要求以控制台为准。本方案使用官方 QQ 接口，不需要 NapCat 或个人 QQ 扫码登录。

确认当前 shell 未继承其他 QQ 应用的凭据。锁定版本的 Hermes 可能因非空凭据环境变量自动启用 QQ，因此只在准备连接自己的测试应用时加载这些凭据。

在 `$HERMES_HOME/config.yaml` 的 `platforms.qqbot.extra` 中填写自己的 `app_id` 和 `client_secret`。也可以在私有环境文件中设置 `QQ_APP_ID`、`QQ_CLIENT_SECRET`，配置文件保持空值。不要混用可选独立机器人使用的 `QQ_APP_SECRET`。

保持 `dm_policy: disabled`，将需要接入的群 OpenID 加入 `group_allow_from`，并保持 `group_policy: allowlist`。这里需要平台事件中的 OpenID，不是界面显示的 QQ 群号。仅在受控测试群核实身份后配置；不要为排查问题开放所有来源。最后把 `platforms.qqbot.enabled` 改为 `true`。

```bash
export HERMES_HOME="$HOME/.config/qq-bot/profile"
export QQ_BOT_DATA_DIR="$HOME/.local/share/qq-bot"
"$HOME/work/hermes-qq/venv/bin/hermes" gateway run
```

这条命令在前台连接 QQ，按 `Ctrl+C` 停止。确认日志显示 QQ 连接成功、问答插件已加载，且没有认证或索引错误。在允许的测试群中手动 `@机器人` 提一个包含板型、系统版本和完整错误的问题，核对答案与引用原文，再测试明确的版本纠正和追问。测试会实际发送消息并调用模型，请自行控制范围和费用。

## 6. 验收后再安排后台运行

满足以下条件后，再由运维人员配置自己的服务管理器或调度器：

- 离线检查、测试和索引同步通过。
- QQ 应用身份、群允许名单和模型账户符合预期。
- 在测试群完成真实收发，答案引用能支持结论，板型和版本没有混淆。
- 确认停止方法、备份位置和密钥管理方式。

本仓库没有自动启用的询问、检修、日报或 FAQ 发布任务。后续管理见 [运维指南](operations.md)，群成员提问方式见 [用户指南](user-guide.md)。
