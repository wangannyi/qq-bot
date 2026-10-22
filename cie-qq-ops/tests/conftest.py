"""Hermetic fixtures for the publicly redistributable test suite."""
import pytest


@pytest.fixture(autouse=True)
def synthetic_faq_link(monkeypatch):
    import daily_summary
    monkeypatch.setattr(daily_summary, "FAQ_REPOSITORY_URL", "https://example.invalid/faq")

@pytest.fixture(autouse=True)
def synthetic_event_policy(monkeypatch):
    import support_extracts
    monkeypatch.setattr(support_extracts, "event_policy", lambda: {'models': '演示需要同时检查 Qwen3.5-2B-Instruct 与 Qwen3.5-4B-Instruct。', 'inference': '本合成赛题设定：不依赖云端API，在本地推理。', 'deployment': '演示选项：K1 开发板或申请8G实例。'})

@pytest.fixture
def synthetic_index(tmp_path):
    """Competing tiny documents replace the developer machine's private corpus."""
    from datetime import datetime, timezone

    from retrieval import create_index
    docs = []
    def add(path, title, body):
        docs.append(dict(id=path, path=path, title=title, body=body,
                         source_kind='official', source_url='', page_url='https://example.invalid/'+path))
    add('software/SDK/bianbu/user_guide/LXQt/Camera_FAQ.md', '相机 FAQ',
        '# 合成显示测试\n该命令依赖桌面环境的 Wayland 显示服务才能弹出预览窗口。\n'
        '演示程序 ./workspace/jdk_cam /dev/video50 使用 WAYLAND_DISPLAY。\n'
        '因此画面无法输出到屏幕上，但采图和日志流程本身不受影响。\n**解决方法**\n此处无操作步骤。')
    add('software/SDK/ros/k1/06_Robot_development/6.4_Robot_Application_Module/6.4.2_Human-Robot_Interaction/2_LLM_Applications.md',
        'K1 LLM 应用合成指南', '''# K1 LLM
本模块基于本地部署的大语言模型（LLM, Large Language Model），以下是合成命令序列，只用于验证顺序和完整性。
```sh
sudo apt update
sudo apt install -y libopenblas-dev python3-spacemit-ort
```
```sh
bash /opt/bros/humble/share/rdk_chat/llm_setup.sh
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set global.extra-index-url https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple
python3 -m venv ~/ai_env
source ~/ai_env/bin/activate
pip install -r /opt/bros/humble/share/rdk_chat/requirements.txt
source /opt/bros/humble/setup.bash
export PYTHONPATH=/example/lib
ros2 launch rdk_chat chat_service.launch.py
```
''')
    add('ai/application_tools/llmsdk.md', 'LLM SDK', '# LLM SDK\n合成对照文档：K1 LLM 开发工具。')
    add('ecohardware/k3_series/k3_pico/root_overview.md','K3 产品合成片段',
        '# K3\nK3 Pico-ITX 为 60 TOPS 算力的单板计算机；本测试关注提高科学计算、人工智能等应用处理效率。\nSpacemiT K3，8 核 2.4GHz，融合 60 TOPS AI 算力')
    add('ecohardware/k1_series/k1_muse_pi_pro/root_overview.md','K1 产品合成片段',
        '# K1\nK1 MUSE Pi Pro 为 1.8 寸单板；本测试关注低功耗、空间紧凑情况下的算力需求。\nSpacemiT M1，融合 2 TOPS AI 算力')
    add('software/SDK/bianbu/development/python.md','Python 使用指南',
        '# 合成 Python 指南\nBianbu OS 示例包含 Python 3。使用apt管理系统包，开发依赖放在虚拟环境。\n'
        'RISC-V 预编译包可配置 extra-index-url 后用 pip config list 检查；不表示所有包都有 wheel。')
    add('software/irrelevant.md','无关章节','# 无关\nTimer uboot_key_prv 不应进入产品定位短答。')
    path = tmp_path / 'synthetic.sqlite'
    create_index(path, docs, datetime.now(timezone.utc).isoformat())
    return path
