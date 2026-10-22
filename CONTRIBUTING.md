# 贡献指南

## 1. 准备开发环境

使用 Linux 和 Python 3.11–3.13，在仓库根目录执行：

```bash
python3 scripts/bootstrap.py --venv .venv --dev
.venv/bin/python scripts/offline_demo.py
.venv/bin/python -m pytest -q
```

测试应在没有维护者凭据、知识库与本机目录的环境中运行。需要外部服务的检查单独说明，不在默认测试中发真实 QQ 消息。

## 2. 修改与验证

```bash
.venv/bin/python -m ruff check . --select E9,F,I
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_release.py
git diff --check
```

新增检索或提取规则时，同时覆盖适用问题、相近但不适用的问题、资料缺失和来源变化。修改连接恢复时，覆盖正常 ACK、丢 ACK、写入阻塞、取消与续接失败。安装工具必须在新目录验证，不修改用户既有 profile。

## 3. 提交改进

在独立分支提交聚焦的变更。PR 描述说明原问题、最终行为、验证命令和仍存在的限制；涉及公开接口时同步更新相应文档。

不要提交私有配置、用户问题、真实账号标识、日志、数据库、下载文档或模型权重。故障复现使用合成材料，真实原始证据由部署者私下保存。

Hermes 补丁锁定信息是兼容性边界。升级上游提交前，先在新的上游检出目录验证补丁和接入回归；避免直接在正在运行的实例中试验。
