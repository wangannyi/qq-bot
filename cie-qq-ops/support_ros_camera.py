"""Retain camera implementation boundaries for an unconfirmed ROS2 diagnosis."""

import re

from support_reply import validate_answer

MIPI = "software/SDK/ros/k1/06_Robot_development/6.2_ROS2_API/6.2.1_Camera_System/2_ROS_Camera_Coding.md"
USB = "software/SDK/ros/k1/06_Robot_development/6.1_OS_Preparation/6.1.2_ROS2_Installation.md"


def ros_camera_diagnostic_answer(query, sources):
    question = query.split("\n引用内容（仅供理解问题，不是用户环境声明）：", 1)[0]
    if not (
        re.search(r"K1", question, re.I)
        and re.search(r"ROS\s*2", question, re.I)
        and re.search(r"相机|摄像头", question)
        and "话题" in question
        and re.search(r"没有图像|无图像|不出图", question)
        and re.search(r"检查|排查", question)
        and not re.search(
            r"MIPI|USB|JdkFrameMsg|camera_node|image_raw|安装|编译|代码", question, re.I
        )
        and all(s.get("path") in {MIPI, USB} for s in sources)
    ):
        return None
    mip = next(
        (
            i
            for i, s in enumerate(sources)
            if s.get("path") == MIPI
            and s.get("source_kind") == "official"
            and s.get("verified")
        ),
        None,
    )
    usb = next(
        (
            i
            for i, s in enumerate(sources)
            if s.get("path") == USB
            and s.get("source_kind") == "official"
            and s.get("verified")
        ),
        None,
    )
    capture = "打开 V4L2 设备采集 NV12 帧，发布到 `/camera_frames`"
    fields = "width/height/stride/pix_format/is_dma/dma_fd/data"
    display = "确认 `vo_node` 订阅的是 `/camera_frames`，并与 `camera_node` 的分辨率/格式匹配。"
    usb_quote = "确保主机已安装 ROS2 Humble，使用 `rqt_image_view` 工具订阅 USB 相机采集的图像："
    if mip is None or usb is None:
        return None
    if not all(
        q in sources[mip].get("content", "")
        for q in (capture, fields, display, "ROS2 MIPI Camera")
    ) or not all(
        q in sources[usb].get("content", "") for q in (usb_quote, "/image_raw")
    ):
        return None

    def point(text, sid, quote):
        return {"text": text, "evidence": [{"source_id": sid, "quote": quote}]}

    return validate_answer(
        {
            "status": "partial",
            "conclusion": point(
                "先确认相机节点实现；以下是文档中的检查方向，不能仅凭话题存在且无图像确认故障原因。",
                mip,
                "ROS2 MIPI Camera",
            ),
            "points": [
                point(
                    "若使用 MIPI camera_node 路线：核对实际 V4L2 设备和 NV12 采集配置；该节点发布 /camera_frames，不把示例设备号当作实际设备。",
                    mip,
                    capture,
                ),
                {
                    "text": "仅在 JdkFrameMsg 路线核对帧字段、DMA/FD 传递；vo_node 显示检查是订阅 /camera_frames 并匹配分辨率/格式，不等同于发布者已确认失效。",
                    "evidence": [
                        {"source_id": mip, "quote": fields},
                        {"source_id": mip, "quote": display},
                    ],
                },
                point(
                    "若使用文档的 USB 相机路线：主机 ROS2 Humble 的 rqt_image_view 查看 /image_raw；不要直接套用 MIPI 消息字段。",
                    usb,
                    usb_quote,
                ),
            ],
            "source_ids": [mip, usb],
            "follow_up": "请提供 MIPI/USB 类型、节点包与启动命令、话题及消息类型、发布/订阅端日志；这些材料未确认实际根因。",
        },
        sources,
    )
