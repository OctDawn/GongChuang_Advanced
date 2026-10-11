"""agent 阶段2 主循环：模型决策 + 工具执行。

在 a/b/c/v 协议约束内：
  第1张已拍 → 喂给模型 → 模型给 action → 执行工具 → 结果喂回 → 直到 finish/no_object。
决策权在模型：拍几个角度、解不解码、何时结束、归哪组，都由模型现场决定。

硬件交互通过回调注入（rotate_cb / decode_cb），因此本模块可脱离摄像头/串口离线测试。
"""
import logging

from shared.const import STAGE_TWO_DETECTION_COUNT
from agent.config import MAX_AGENT_STEPS, ENABLE_DECODE
from agent.llm_client import AgentSession, AgentLLMError
from agent.prompts_agent import SYSTEM_PROMPT, build_turn_text

logger = logging.getLogger("wave.vision.agent")


class StageTwoAgent:
    def __init__(self, memory, rotate_cb, decode_cb=None):
        self.memory = memory
        self.rotate_cb = rotate_cb          # callable() -> 新帧 或 None（无更多角度）
        self.decode_cb = decode_cb or (lambda frame, kind: None)

    def run(self, first_frame):
        """跑一件货物的 agent 循环。

        返回 (gid, name)：
            gid=None  无物体
            gid=0     储物盒已满（无法新建）
            1~max     分组号
        决策/请求失败抛 AgentLLMError；rotate_cb 抛出的异常（如 CameraStopped）向上传播。
        """
        session = AgentSession(SYSTEM_PROMPT)
        frames = [first_frame]

        def _text(asked):
            rotates_left = max(0, STAGE_TWO_DETECTION_COUNT - len(frames))
            steps_left = max(0, MAX_AGENT_STEPS - asked)
            return build_turn_text(
                self.memory.describe(), len(frames), rotates_left, steps_left,
            )

        session.add_view(first_frame, _text(0))

        for step in range(MAX_AGENT_STEPS):
            rotates_left = max(0, STAGE_TWO_DETECTION_COUNT - len(frames))
            action = session.ask()
            act = (action.get("action") or "").strip().lower()
            args = action.get("args") or {}

            if act == "rotate":
                if rotates_left <= 0:
                    session.add_text("角度已用满，不能再 rotate，请直接 finish 或 no_object。")
                    continue
                new_frame = self.rotate_cb()
                if new_frame is None:
                    session.add_text("无法再拍新角度，请基于现有视图 finish 或 no_object。")
                    continue
                frames.append(new_frame)
                session.add_view(new_frame, _text(step + 1))
                continue

            if act == "decode":
                if not ENABLE_DECODE:
                    session.add_text("decode 工具不可用，请直接 finish。")
                    continue
                kind = (args.get("kind") or "text").strip().lower()
                result = self.decode_cb(frames[-1], kind)
                if result:
                    session.add_text(f"贴纸解码结果({kind}): {result!r}")
                else:
                    session.add_text(f"贴纸解码({kind})未读出内容，可 rotate 或直接 finish。")
                continue

            if act == "finish":
                name = (args.get("name") or "").strip() or "未命名"
                group = args.get("group")
                if group is not None:
                    try:
                        group = int(group)
                    except (TypeError, ValueError):
                        group = None
                if group is not None and self.memory.valid(group):
                    if self.memory.group_is_full(group):
                        logger.error("组 %d 已满(>=%d件)，无法再放入", group, self.memory.max_items_per_group)
                        return 0, name
                    self.memory.incr(group)
                    logger.info("归入已有组 %d：%s", group, name)
                    return group, name
                gid = self.memory.add(name)
                if gid == 0:
                    logger.error("储物盒已满，无法新建分组")
                    return 0, name
                self.memory.incr(gid)
                logger.info("新建组 %d：%s", gid, name)
                return gid, name

            if act == "no_object":
                return None, None

            session.add_text("无法识别的 action，请只输出 rotate/decode/finish/no_object 之一。")

        logger.error("agent 决策步数用尽，未给出结果")
        raise AgentLLMError("agent 决策步数用尽")
