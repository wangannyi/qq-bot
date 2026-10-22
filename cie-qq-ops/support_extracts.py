"""Literal, narrowly scoped answers where paraphrasing previously added claims.

These routes require the actual supporting spans in verified primary sources.
Missing or changed clauses and other questions retain the normal QA path.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def event_policy():
    """Optional operator-supplied exact clauses; organizer text is never bundled."""
    value = os.environ.get("QQ_BOT_RULES_POLICY", "").strip()
    if not value:
        return {}
    data = json.loads(Path(value).expanduser().read_text(encoding="utf-8"))
    return {key: data[key] for key in ("models", "inference", "deployment")
            if isinstance(data.get(key), str) and 8 <= len(data[key]) <= 300}

from support_manuals import pip_manual_answer
from support_reply import validate_answer
from support_ros_camera import ros_camera_diagnostic_answer
from support_sources import headless_camera_question


def _statement(text, sid, quote):
    return {"text": text, "evidence": [{"source_id": sid, "quote": quote}]}


def source_extract_answer(query, sources):
    question = query.split("\n引用内容（仅供理解问题，不是用户环境声明）：", 1)[0]
    if headless_camera_question(question):
        quote = "该命令依赖桌面环境的 Wayland 显示服务才能弹出预览窗口。"
        distinction = "因此画面无法输出到屏幕上，但采图和日志流程本身不受影响。"
        for sid, source in enumerate(sources):
            content = source.get("content", "")
            if (
                source.get("path")
                == "software/SDK/bianbu/user_guide/LXQt/Camera_FAQ.md"
                and source.get("source_kind") == "official"
                and source.get("verified")
                and all(
                    x in content
                    for x in (
                        quote,
                        distinction,
                        "./workspace/jdk_cam /dev/video50",
                        "WAYLAND_DISPLAY",
                    )
                )
            ):
                return validate_answer(
                    {
                        "status": "partial",
                        "conclusion": _statement(
                            "无预览窗口不能单凭这一现象判定采集失败；官方FAQ仅给出了jdk_cam在SSH下的显示问题示例。",
                            sid,
                            distinction,
                        ),
                        "points": [
                            _statement(
                                "该示例使用./workspace/jdk_cam /dev/video50，预览依赖Wayland；SSH终端未携带桌面显示环境变量时，可能没有屏幕画面。",
                                sid,
                                content[
                                    content.find("该命令依赖桌面环境") : content.find(
                                        "**解决方法**"
                                    )
                                ].strip(),
                            ),
                            _statement(
                                "FAQ描述的前提是日志正常，且此时采图和日志流程不受显示失败影响；不能外推为你的相机采集已经成功。",
                                sid,
                                distinction,
                            ),
                        ],
                        "source_ids": [sid],
                        "follow_up": "请补充系统版本、相机接口、采集程序和日志；该FAQ没有给出所有无桌面环境下的通用分层检测方法。",
                    },
                    sources,
                )
    if (
        re.search(r"x86", question, re.I)
        and re.search(r"K1", question, re.I)
        and "虚拟环境" in question
        and "复制" in question
        and "直接运行" in question
        and not re.search(r"原理|只.*解释|不要.*命令|ROS|OpenHarmony", question, re.I)
        and len(sources) == 1
    ):
        source = sources[0]
        commands = [
            "sudo apt install python3-virtualenv",
            "virtualenv myenv",
            "source myenv/bin/activate",
        ]
        quote = "创建虚拟环境，使用pip包管理工具安装软件包。"
        if (
            source.get("path") == "software/SDK/bianbu/development/python.md"
            and source.get("source_kind") == "official"
            and source.get("verified")
            and quote in source.get("content", "")
            and all(c in source.get("content", "") for c in commands)
        ):
            return validate_answer(
                {
                    "status": "partial",
                    "conclusion": _statement(
                        "Bianbu 指南提供在目标系统创建环境的方法；未给出 x86 环境复制到 K1 后能否直接运行的判定规则。",
                        0,
                        quote,
                    ),
                    "points": [
                        {
                            "text": text,
                            "command": command,
                            "evidence": [{"source_id": 0, "quote": command}],
                        }
                        for text, command in zip(
                            [
                                "若目标为 Bianbu，安装指南使用的 virtualenv 工具。",
                                "在目标系统创建 myenv 环境。",
                                "激活同一个 myenv 环境。",
                            ],
                            commands,
                        )
                    ],
                    "source_ids": [0],
                    "follow_up": "请确认 K1 的系统版本、Python 版本及依赖清单；上述是 Bianbu 重建步骤，不是复制环境兼容性或板上运行验收。",
                },
                sources,
            )
    camera = ros_camera_diagnostic_answer(query, sources)
    if camera is not None:
        return camera
    platform_comparison = (
        re.search(r"(?<![a-z0-9])k1(?![a-z0-9])", question, re.I)
        and re.search(r"(?<![a-z0-9])k3(?![a-z0-9])", question, re.I)
        and re.search(r"区别|对比|比较|定位", question)
        and re.search(r"平台|定位", question)
        and not re.search(
            r"SDK|系统|内核|CPU|接口|相机|部署|兼容|程序|模型|LLM|Python|网络|功耗|跑分|性能",
            question,
            re.I,
        )
    )
    comparison_paths = {
        "ecohardware/k3_series/k3_pico/root_overview.md": 0,
        "ecohardware/k1_series/k1_muse_pi_pro/root_overview.md": 1,
    }
    if (
        platform_comparison
        and len(sources) == 2
        and all(
            s.get("path") in comparison_paths
            and s.get("source_kind") == "official"
            and s.get("verified")
            for s in sources
        )
    ):
        k3_id = next(
            i
            for i, s in enumerate(sources)
            if s["path"].startswith("ecohardware/k3_series/")
        )
        k1_id = next(
            i
            for i, s in enumerate(sources)
            if s["path"].startswith("ecohardware/k1_series/")
        )
        k3_text = sources[k3_id].get("content", "")
        k1_text = sources[k1_id].get("content", "")
        k3_quote = "K3 Pico-ITX 为 60 TOPS 算力的单板计算机"
        k3_use = "提高科学计算、人工智能等应用处理效率"
        k1_quote = "K1 MUSE Pi Pro 为 1.8 寸单板"
        k1_use = "低功耗、空间紧凑情况下的算力需求"
        k3_ai = "SpacemiT K3，8 核 2.4GHz，融合 60 TOPS AI 算力"
        k1_ai = "SpacemiT M1，融合 2 TOPS AI 算力"
        if all(x in k3_text for x in (k3_quote, k3_use, k3_ai)) and all(
            x in k1_text for x in (k1_quote, k1_use, k1_ai)
        ):
            answer = {
                "status": "partial",
                "conclusion": {
                    "text": (
                        "可用产品简介作有限比较：K3 Pico-ITX侧重高算力AI与科学计算；"
                        "K1 MUSE Pi Pro强调紧凑、低功耗场景。这里比较的是两个具体板卡简介，"
                        "不能据此概括所有K3/K1产品或单独推导芯片级适用性。"
                    ),
                    "evidence": [
                        {"source_id": k3_id, "quote": k3_quote},
                        {"source_id": k1_id, "quote": k1_quote},
                    ],
                },
                "points": [
                    {
                        "text": "K3 Pico-ITX简介给出60 TOPS算力，并面向科学计算、人工智能等应用。",
                        "evidence": [
                            {"source_id": k3_id, "quote": k3_quote},
                            {"source_id": k3_id, "quote": k3_use},
                            {"source_id": k3_id, "quote": k3_ai},
                        ],
                    },
                    {
                        "text": "K1 MUSE Pi Pro简介突出1.8寸紧凑单板和低功耗、空间紧凑的应用场景。",
                        "evidence": [
                            {"source_id": k1_id, "quote": k1_quote},
                            {"source_id": k1_id, "quote": k1_use},
                            {"source_id": k1_id, "quote": k1_ai},
                        ],
                    },
                ],
                "source_ids": [k3_id, k1_id],
                "follow_up": "若要选型，请补充具体K1/K3板卡、系统版本和工作负载；系列内不同产品不能仅按此例概括。",
            }
            return validate_answer(answer, sources)
    asr_weight_device_question = (
        re.search(r"(?<![a-z0-9])k3(?![a-z0-9])", question, re.I)
        and re.search(r"识别引擎", question)
        and re.search(r"模型权重", question)
        and re.search(r"音频采集设备", question)
        and re.search(r"区分|分别|对应", question)
        and not re.search(r"命令|安装|下载|性能|benchmark|兼容|版本", question, re.I)
    )
    linksee_path = "software/SDK/ros/k3/03-参考方案/3.2-轮式机器人Linksee.md"
    if asr_weight_device_question:
        for sid, source in enumerate(sources):
            content = source.get("content", "")
            backend = "`asr.backend`：ASR 后端，可选 `sensevoice` 或 `qwen3_asr`。"
            device_name = "`asr.device_name`：采集设备名称。非空时优先按名称解析当前设备编号；未配置时使用 `asr.device`。"
            device = "`asr.device`：采集设备编号。"
            if (
                source.get("path") != linksee_path
                or source.get("source_kind") != "official"
                or not source.get("verified")
                or "面向 `k3-com260` 板卡的 `linksee` 机器人整机方案" not in content
                or backend not in content
                or device_name not in content
                or device not in content
            ):
                continue
            return validate_answer(
                {
                    "status": "partial",
                    "conclusion": {
                        "text": (
                            "K3 Linksee 指南中，asr.backend 选择识别后端，"
                            "asr.device_name/asr.device 指定采集设备；该参数段未说明模型权重的配置或加载方式。"
                        ),
                        "evidence": [
                            {"source_id": sid, "quote": backend},
                            {"source_id": sid, "quote": device_name},
                            {"source_id": sid, "quote": device},
                        ],
                    },
                    "points": [
                        _statement(
                            "识别引擎由 asr.backend 选择，指南列出 sensevoice 和 qwen3_asr。",
                            sid,
                            backend,
                        ),
                        _statement(
                            "音频输入设备由 asr.device_name 指定名称；未配置名称时使用 asr.device 编号。",
                            sid,
                            device_name,
                        ),
                        _statement(
                            "该指南的 asr.backend 说明未给出权重文件或加载规则，不能据此断言权重随后端自动加载。",
                            sid,
                            backend,
                        ),
                    ],
                    "source_ids": [sid],
                    "follow_up": (
                        "若要确认具体权重文件及加载方式，请提供实际 Linksee 配置或模型版本；"
                        "这份指南只支持上述引擎选项与采集设备字段。"
                    ),
                },
                sources,
            )
    manual = pip_manual_answer(query, sources)
    if manual is not None:
        return manual
    llm_deployment = (
        re.search(r"(?<![a-z0-9])k3(?![a-z0-9])", question, re.I)
        and re.search(r"bianbu", question, re.I)
        and re.search(r"(?:llm|大模型)", question, re.I)
        and re.search(r"部署|安装", question)
        and not re.search(r"性能|benchmark|兼容|版本清单|模型选型|SDK", question, re.I)
    )
    ollama_path = "ai/application_tools/ollama.md"
    llm_path = "software/SDK/ros/k3/04-AI与算法/4.4-LLM.md"
    if llm_deployment and all(
        s.get("path") in {ollama_path, llm_path} for s in sources
    ):
        for sid, source in enumerate(sources):
            content = source.get("content", "")
            platform = "K3 Bianbu LXQT/GNOME"
            accel_table = re.search(r"K3 Bianbu LXQT/GNOME.{0,80}不支持加速", content)
            accel_claim = re.search(r"q4_K_M.{0,180}(?:享受加速|加速)", content, re.S)
            install = "sudo apt update\nsudo apt install spacemit-ollama-toolkit"
            install_check = re.search(
                r"验证安装：.*?ollama list.*?表示安装成功。", content, re.S
            )
            version_check = re.search(
                r"验证版本（确保为 0\.0\.8 或以上版本）：.*?"
                r"sudo apt show spacemit-ollama-toolkit.*?确认版本为 0\.0\.8 或以上，"
                r"以支持新的模型格式和直接拉取功能。",
                content,
                re.S,
            )
            pull = "ollama pull qwen3:0.6b"
            run = "ollama run qwen3:0.6b"
            if (
                source.get("path") != ollama_path
                or source.get("source_kind") != "official"
                or not source.get("verified")
                or platform not in content
                or not accel_table
                or not accel_claim
                or install not in content
                or not install_check
                or not version_check
                or pull not in content
                or run not in content
            ):
                continue
            return validate_answer(
                {
                    "status": "partial",
                    "conclusion": {
                        "text": (
                            "Ollama 指南列出 K3 Bianbu LXQT/GNOME，但未注明 Bianbu 4.0.1 的适用性；"
                            "下载段提到加速但未限定平台，不能据此确认 K3 加速。"
                        ),
                        "evidence": [
                            {"source_id": sid, "quote": accel_table[0]},
                            {"source_id": sid, "quote": accel_claim[0]},
                        ],
                    },
                    "points": [
                        _statement(
                            "更新软件源：sudo apt update；安装工具包：sudo apt install spacemit-ollama-toolkit。",
                            sid,
                            install,
                        ),
                        {
                            "text": (
                                "安装后运行 ollama list 验证；再用 sudo apt show spacemit-ollama-toolkit 核对版本至少为 0.0.8。"
                            ),
                            "evidence": [
                                {"source_id": sid, "quote": install_check[0]},
                                {"source_id": sid, "quote": version_check[0]},
                            ],
                        },
                        {
                            "text": (
                                "执行 ollama pull qwen3:0.6b 下载模型，再运行 "
                                "ollama run qwen3:0.6b 启动模型。"
                            ),
                            "evidence": [
                                {"source_id": sid, "quote": pull},
                                {"source_id": sid, "quote": run},
                            ],
                        },
                    ],
                    "source_ids": [sid],
                    "follow_up": "请向技术支持确认该工具包对 Bianbu 4.0.1 的适用性及 K3 加速能力。",
                },
                sources,
            )
    k1_llm_path = (
        "software/SDK/ros/k1/06_Robot_development/6.4_Robot_Application_Module/"
        "6.4.2_Human-Robot_Interaction/2_LLM_Applications.md"
    )
    k1_llm_deployment = (
        re.search(r"(?<![a-z0-9])k1(?![a-z0-9])", question, re.I)
        and re.search(r"bianbu", question, re.I)
        and re.search(r"2\.3\.5", question)
        and re.search(r"(?:llm|大模型)", question, re.I)
        and re.search(r"部署|安装|步骤", question)
        and not re.search(r"性能|benchmark|兼容|模型选型|SDK", question, re.I)
    )
    if (
        k1_llm_deployment
        and all(
            s.get("source_kind") == "official" and s.get("verified") for s in sources
        )
        and len(sources) <= 2
    ):
        for sid, source in enumerate(sources):
            content = source.get("content", "")
            apt = re.search(
                r"sudo apt update\s+sudo apt install -y .*?(?=\n```)", content, re.S
            )
            setup = "bash /opt/bros/humble/share/rdk_chat/llm_setup.sh"
            index_url = "pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/"
            extra_index = "pip config set global.extra-index-url https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple"
            venv = "python3 -m venv ~/ai_env"
            activate = "source ~/ai_env/bin/activate"
            requirements = (
                "pip install -r /opt/bros/humble/share/rdk_chat/requirements.txt"
            )
            ros_env = "source /opt/bros/humble/setup.bash"
            launch = "ros2 launch rdk_chat chat_service.launch.py"
            pip_start = content.find(index_url)
            pip_end = content.find(requirements, pip_start) + len(requirements)
            env_start = content.find(ros_env)
            env_end = content.find(launch, env_start) + len(launch)
            pip_block = content[pip_start:pip_end] if pip_start >= 0 else ""
            service_environment = content[env_start:env_end] if env_start >= 0 else ""
            if (
                source.get("path") != k1_llm_path
                or not apt
                or not all(
                    x in content
                    for x in (
                        setup,
                        index_url,
                        extra_index,
                        venv,
                        activate,
                        requirements,
                        ros_env,
                        launch,
                    )
                )
            ):
                continue
            return validate_answer(
                {
                    "status": "partial",
                    "conclusion": {
                        "text": "该K1 ROS2 LLM指南未说明Bianbu 2.3.5适用性。",
                        "evidence": [
                            {
                                "source_id": sid,
                                "quote": "本模块基于本地部署的大语言模型（LLM, Large Language Model）",
                            }
                        ],
                    },
                    "points": [
                        {
                            "text": "sudo apt update；安装文档列出的系统依赖；运行bash /opt/bros/humble/share/rdk_chat/llm_setup.sh。",
                            "evidence": [
                                {"source_id": sid, "quote": apt[0].strip()},
                                {"source_id": sid, "quote": setup},
                            ],
                        },
                        {
                            "text": f"配置文档中的pip源；{venv}；{activate}；{requirements}。",
                            "evidence": [{"source_id": sid, "quote": pip_block}],
                        },
                        {
                            "text": f"{ros_env}；设置PYTHONPATH；{launch}。",
                            "evidence": [
                                {"source_id": sid, "quote": service_environment}
                            ],
                        },
                    ],
                    "source_ids": [sid],
                    "follow_up": ("请确认该ROS2应用指南是否适用于Bianbu 2.3.5。"),
                },
                sources,
            )
    question = query.split("\n引用内容（仅供理解问题，不是用户环境声明）：", 1)[0]
    if (
        re.search(r"bianbu", question, re.I)
        and "内核版本" in question
        and re.search(r"查看|查询|确认|核对", question)
        and not re.search(
            r"安装|切换|升级|编译|驱动|ROS|OpenHarmony|引用内容", question, re.I
        )
    ):
        scope = "本指南介绍如何在 Bianbu（K3 平台）上安装、切换并测试 PREEMPT_RT 实时内核（RT-Linux）。"
        for sid, source in enumerate(sources):
            content = source.get("content", "")
            if (
                source.get("path")
                == "software/SDK/bianbu/development/rt-linux_guide.md"
                and source.get("source_kind") == "official"
                and source.get("verified")
                and all(q in content for q in (scope, "验证当前内核版本", "uname -r"))
                and not any(s.get("path") != source.get("path") for s in sources)
            ):
                return validate_answer(
                    {
                        "status": "partial",
                        "conclusion": _statement(
                            "Bianbu 的 K3 RT-Linux 指南提供了当前内核版本的查询方法。",
                            sid,
                            scope,
                        ),
                        "points": [
                            {
                                "text": "查看当前运行的内核版本；指南中的版本输出仅是示例。",
                                "command": "uname -r",
                                "evidence": [{"source_id": sid, "quote": "uname -r"}],
                            }
                        ],
                        "source_ids": [sid],
                        "follow_up": "当前资料仅支持上述内核查询；尚未确认 Bianbu 发行版本查询的直接适用原文，请补充板型和系统来源以继续核实。",
                    },
                    sources,
                )
    service_import = (
        re.search(r"bianbu", question, re.I)
        and re.search(r"systemd", question, re.I)
        and re.search(r"python", question, re.I)
        and re.search(r"找不到依赖|依赖缺失|无法导入|ModuleNotFoundError", question)
        and not re.search(
            r"系统级|root|sudo|OpenHarmony|代码|命令|脚本内容|ExecStart=",
            question,
            re.I,
        )
    )
    service_path = (
        "software/SDK/ros/k1/02_System_configuration/2.7_Automatic_Startup.md"
    )
    rootfs_path = "software/SDK/bianbu/system_integration/bianbu_2.0_rootfs_create.md"
    if service_import and all(
        s.get("path") in {service_path, rootfs_path} for s in sources
    ):
        scope_quote = "以 systemd 用户服务为例，适用于 bianbu 系统。"
        directory_quote = "`WorkingDirectory` 填写你的工作目录路径。"
        start_quote = "`ExecStart` 填写你的启动命令或可执行文件路径。"
        env_quote = "如需设置环境变量，可在 `Environment` 行添加。"
        log_quote = "日志可通过 `journalctl --user -u demo.service` 查看。"
        for sid, source in enumerate(sources):
            if (
                source.get("path") == service_path
                and source.get("source_kind") == "official"
                and source.get("verified")
                and all(
                    q in source.get("content", "")
                    for q in (
                        scope_quote,
                        directory_quote,
                        start_quote,
                        env_quote,
                        log_quote,
                        "Environment=PYTHONPATH=/if/you/need",
                    )
                )
            ):
                return validate_answer(
                    {
                        "status": "partial",
                        "conclusion": _statement(
                            "当前手册给出的是 Bianbu 的 systemd 用户服务配置示例。",
                            sid,
                            scope_quote,
                        ),
                        "points": [
                            {
                                "text": "核对 ExecStart 的启动命令或可执行文件路径，以及 WorkingDirectory 的工作目录。",
                                "evidence": [
                                    {"source_id": sid, "quote": start_quote},
                                    {"source_id": sid, "quote": directory_quote},
                                ],
                            },
                            _statement(
                                "Environment 按需添加环境变量；示例中的 PYTHONPATH 是可选配置。",
                                sid,
                                env_quote,
                            ),
                            _statement(
                                "journalctl --user -u demo.service 仅适用于示例中的用户服务；实际服务名和运行域需先确认。",
                                sid,
                                log_quote,
                            ),
                        ],
                        "follow_up": "原文不能判定依赖缺失原因。请提供实际 unit 配置、终端与服务使用的 Python 解释器/虚拟环境，以及完整报错。",
                    },
                    sources,
                )
    document_conflict = (
        re.search(r"文档|手册", question)
        and "版本" in question
        and re.search(r"不一致|冲突|矛盾", question)
        and re.search(r"步骤|操作", question)
        and not re.search(
            r"https?://|\d|manifest|repo|源码|编译|下载|命令|脚本",
            question,
            re.I,
        )
    )
    manifest_path = "software/SDK/buildroot/k3_buildroot/source.md"
    camera_path = (
        "software/SDK/buildroot/k1_buildroot/camera/camera_development_guide.md"
    )
    # Only handle the observed source-only evidence set. Additional documents
    # may actually specify version applicability and need the normal QA review.
    if document_conflict and all(
        s.get("path") in {manifest_path, camera_path} for s in sources
    ):
        quote = (
            "manifests 仓库的 main 分支定义了不同版本的 `manifest.xml`，"
            "xml 文件指定了各仓库的路径和分支。"
        )
        for sid, source in enumerate(sources):
            if (
                source.get("path") == manifest_path
                and source.get("source_kind") == "official"
                and source.get("verified")
                and "本文档介绍 SDK 源码的开发环境、下载和编译方式。"
                in source.get("content", "")
                and quote in source.get("content", "")
            ):
                return validate_answer(
                    {
                        "status": "partial",
                        "conclusion": _statement(
                            "当前引用的版本分支段落说明的是 SDK 源码："
                            "manifest.xml 指定各仓库的路径和分支。",
                            sid,
                            quote,
                        ),
                        "points": [],
                        "follow_up": (
                            "该段不能确定开发文档与版本的对应关系。请提供两份文档的链接、"
                            "版本和冲突步骤，以及实际板型、系统及 SDK 版本，再核对适用范围。"
                        ),
                    },
                    sources,
                )
    capture_parameters = (
        re.search(r"(?<![a-z0-9])k1(?![a-z0-9])", question, re.I)
        and re.search(r"usb.*(?:相机|摄像头)", question, re.I)
        and re.search(r"采集参数|收集.*参数", question)
        and not re.search(r"K3|OpenHarmony|代码|脚本|命令|标定", question, re.I)
    )
    if capture_parameters:
        for sid, source in enumerate(sources):
            if (
                source.get("source_kind") != "official"
                or not source.get("verified")
                or not source.get("path", "").endswith("/1_USB_Camera_Node.md")
                or "/k1/" not in source["path"]
            ):
                continue
            groups = [
                (
                    "video_device",
                    "image_width",
                    "image_height",
                    "pixel_format",
                    "framerate",
                    "io_method",
                ),
                ("autoexposure", "exposure", "gain"),
                ("auto_white_balance", "white_balance"),
            ]
            rows = {}
            for line in source["content"].splitlines():
                for name in sum(groups, ()):
                    if re.match(r"\|\s*`?" + name + r"`?\s*\|", line):
                        rows[name] = line
            if not all(name in rows for name in sum(groups, ())):
                continue
            # Extract parameter names, not example defaults or speculative
            # black-frame causes. Several real drivers have different names.
            points = []
            for group in groups:
                # Three evidence spans per statement; one contiguous table
                # slice retains all intervening original rows without edits.
                body = source["content"]
                start = min(body.index(rows[n]) for n in group)
                end = max(body.index(rows[n]) + len(rows[n]) for n in group)
                points.append(
                    _statement("、".join(group) + " 的当前值。", sid, body[start:end])
                )
            return validate_answer(
                {
                    "status": "partial",
                    "conclusion": _statement(
                        "若使用 K1 文档中的 ROS2 USB 相机节点，先收集以下采集参数的当前值。",
                        sid,
                        rows["video_device"],
                    ),
                    "points": points,
                    "follow_up": "这些参数定义不能确定黑屏原因；其他采集程序需提供程序名及实际配置，再核对其参数名。",
                },
                sources,
            )
    camera = (
        re.search(r"(?<![a-z0-9])k1(?![a-z0-9])", question, re.I)
        and not re.search(r"(?<![a-z0-9])k3(?![a-z0-9])", question, re.I)
        and re.search(r"usb.*(?:相机|摄像头)", question, re.I)
        and re.search(r"编号|持久命名", question)
        and re.search(r"避免|持久|固定编号|固定设备号", question)
        and not re.search(
            r"代码|脚本|规则|示例|命令|权限|话题|openharmony|VideoCapture|cv2",
            question,
            re.I,
        )
    )
    if camera:
        for sid, source in enumerate(sources):
            if (
                source.get("source_kind") != "official"
                or not source.get("verified")
                or not source.get("path", "").endswith("/1_USB_Camera_Node.md")
                or "/k1/" not in source["path"]
            ):
                continue
            span = re.search(
                r"插拔或顺序变化会导致 `/dev/videoX` 号变化。"
                r"使用 `udev` 基于序列号或 USB 路径创建持久命名",
                source["content"],
            )
            if not span:
                continue
            cause, method = span[0].split("。", 1)
            return validate_answer(
                {
                    "status": "answered",
                    "conclusion": _statement(method + "。", sid, span[0]),
                    "points": [_statement(cause + "。", sid, span[0])],
                    "follow_up": "",
                },
                sources,
            )

    # This answers only the inference/network restriction question, not an
    # entire competition rule review or another task's eligibility question.
    event = (
        re.search(r"CIE|赛题2", question, re.I)
        and re.search(r"推理", question)
        and re.search(r"云端|云实例|联网|Bianbu Cloud", question, re.I)
        and re.search(r"限制|要求|允许|联网|云端API", question, re.I)
        and not re.search(r"赛题\s*[13456789]|评分|提交|报名|安装|命令|代码", question)
        and all(year == "2026" for year in re.findall(r"20\d{2}", question))
        and all(task == "2" for task in re.findall(r"赛题\s*(\d+)", question))
    )
    if event and len(sources) == 1:
        source = sources[0]
        if (
            source.get("source_kind") != "event_rules"
            or not source.get("verified")
            or source.get("path") != "event/cie-2026/rules"
        ):
            return None
        body = source["content"]
        task = re.search(r"赛题2[:：]([^\n]+)\n(.*)", body, re.S)
        if not task:
            return None
        section = re.split(r"赛题\s*[13456789][:：]", task[2])[0]
        policy = event_policy()
        models, inference, deployment = (policy.get(k, "") for k in ("models", "inference", "deployment"))
        if not all(clause and clause in section for clause in (models, inference, deployment)):
            return None
        return validate_answer(
            {
                "status": "partial",
                "conclusion": _statement("赛题2：" + inference, 0, inference),
                "points": [
                    _statement(models, 0, models),
                    _statement(
                        "部署条款原文：" + deployment + "。", 0, deployment
                    ),
                ],
                "follow_up": "这些摘录不能确定所有外部联网限制或云实例使用边界；未明确部分需向组委会确认。",
            },
            sources,
        )
    return None
