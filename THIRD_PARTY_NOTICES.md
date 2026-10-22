# 第三方组件与资料

## Hermes Agent

推荐接入路径使用 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)，MIT 许可证。兼容提交和补丁元数据见 [Hermes 锁定信息](patches/hermes-lock.json)。安装工具检出锁定提交，再检查并应用本项目需要的 QQ 接入补丁。

涉及 Hermes 的补丁保留原项目版权与许可，完整文本见 [Hermes 许可证](patches/HERMES-LICENSE)。本项目不重新分发完整 Hermes 仓库，也不将其后续版本兼容性视为已验证。

## QQ SDK 和 Python 依赖

可选独立模块通过 [tencent-connect/botpy](https://github.com/tencent-connect/botpy) 接入 QQ 官方机器人接口，SDK 使用 MIT 许可证。HTTP、模型接口、YAML、测试等 Python 依赖由安装工具按依赖文件获取；各自许可证继续适用。

## 文档来源

知识来源包括 [进迭时空开发者文档](https://www.spacemit.com/community/document)。源码许可证不授予第三方文档、品牌、模型权重、赛事文件或用户消息的再分发权。部署者自行同步所需资料，并遵守来源许可和访问规则。

公开仓库不包含运行中的知识库、聊天数据库、完整抓取页面、私有赛题文件和真实用户失败记录。测试资料应使用合成内容；引用的技术名称、接口字段和命令用于行为测试，不代表对设备运行结果的保证。

## NapCat 与自动化平台

正式机器人使用 QQ 官方机器人接口。NapCat 曾用于独立模拟用户测试，不是公开版本的默认运行依赖；不分发其登录数据、客户端或测试账号。Codex 定时任务和本机检修状态也不作为安装内容分发。
