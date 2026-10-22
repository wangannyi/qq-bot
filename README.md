# QQ 技术支持机器人

通过 QQ 官方机器人接口提供基于文档的技术问答，包含检索、来源校验、短答、用户环境续问、FAQ 候选和日报。

推荐部署路径是 **Hermes Agent + 本项目 QQ 插件**。独立 `qq-support-bot` 模块保留为可选命令行与基础机器人实现，两条路径的能力和部署方式见 [架构说明](docs/architecture.md)。

## 1. 先运行离线示例

准备 Git、Linux 和 Python 3.11–3.13。以下步骤不需要 QQ 账号、模型密钥、知识库或 Hermes，不会发消息。

```bash
git clone https://github.com/wangannyi/qq-bot.git
cd qq-bot
python3 scripts/bootstrap.py --venv .venv --dev
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/offline_demo.py
.venv/bin/python -m pytest -q
```

离线示例使用合成文档验证检索和回复流程。它不代表已经接入 QQ，也不代表实际硬件上的操作结果已验证。

## 2. 搭建真实机器人

按 [标准搭建流程](docs/setup.md) 准备锁定版本的 Hermes、新 profile、模型和 QQ 开放平台凭据。安装工具默认不登录 QQ、不启动后台服务、不设置定时任务、不发布 FAQ。

| 目标 | 文档 |
| --- | --- |
| 从新目录搭建并验证机器人 | [标准搭建流程](docs/setup.md) |
| 在群里提问、补充环境、理解来源和限制 | [用户使用指南](docs/user-guide.md) |
| 查环境变量与示例配置 | [配置参考](docs/configuration.md) |
| 查看服务、备份、日报和连接恢复 | [运维说明](docs/operations.md) |
| 处理资料不足、登录和模型问题 | [故障排查](docs/troubleshooting.md) |
| 了解模块职责与数据流 | [架构说明](docs/architecture.md) |

## 3. 能力与边界

- 技术答复保留原文链接、适用板型和版本限制；命令从来源中完整提取。
- 显式环境和版本补充可继续原问题；新的完整问题切换话题。
- FAQ 与日报分别处理，真实测试应与正式消息隔离。公开安装不包含维护者的模拟用户账号和定时出题任务。
- ACK 监督可在心跳失联时重建连接并使用原 Resume 流程续接。它是恢复措施，不能根除外部网络故障或保证历史消息全部可恢复。
- 语义正确性、所有客户端显示效果和硬件兼容性仍需具体案例验收。资料不足或要求补充环境不等于问题已经解决。

本仓库不包含知识库快照、聊天记录、凭据、模型权重和私有赛事文件。来源和第三方组件的权利见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 4. 开发与许可

提交改进前阅读 [贡献指南](CONTRIBUTING.md) 和 [代码规范](docs/coding_standards.md)。安全问题按 [安全说明](SECURITY.md) 报告。

初始发布的验证范围与限制见 [验证记录](docs/validation.md)。

项目代码采用 [MIT 许可证](LICENSE)。
