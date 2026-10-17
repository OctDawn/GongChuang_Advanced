"""LLM 形状分类：有限词表 + 本轮视觉记忆 + 单轮识别。贴纸已开放化。"""

import logging

from llm.qw import LLMError, LLMDeadlineExceeded, qwen_realise
from llm.prompt import (
    COLOR_VOCAB, SHAPE_VOCAB, COLOR_EN, SHAPE_EN, match_vocab,
    build_classify_prompt, build_classify_prefix,
    build_group_match_prompt, build_group_reference_prompt, build_fixed_view_classify_prompt,
    PROMPT_SHAPE_REFERENCE, PROMPT_CLASSIFY_CURRENT,
)
from shared.common import crop_detect_llm, parse_group_match
from shared.const import LASER_LLM_TIMEOUT, LASER_QUICK_TIMEOUT

logger = logging.getLogger("wave.vision")


def parse_classify_result(result, records=()):
    if not isinstance(result, dict):
        raise LLMError("分类结果必须为 JSON 对象")
    required = {"status", "color", "shape", "label_kind"}
    if not required.issubset(result):
        raise LLMError("分类结果缺少必需字段: " + ",".join(sorted(required - result.keys())))
    status = result["status"]
    if status not in ("empty", "known", "remembered", "new", "uncertain"):
        raise LLMError("status 必须为 empty/known/remembered/new/uncertain")
    description = result.get("shape_description", "")
    if not isinstance(description, str) or len(description) > 400:
        raise LLMError("shape_description 必须是不超过 400 字的字符串")
    if status == "empty":
        if any(result.get(key) is not None for key in ("color", "shape", "label_kind", "label_description")):
            raise LLMError("只有明确无物体才能 empty，颜色、形状及贴纸必须显式为 null")
        return dict(status=status, color=None, shape=None, label_kind="none",
                    label_description=None, description=description)

    color = match_vocab(result["color"], COLOR_VOCAB, COLOR_EN)
    if color is None and not (status == "uncertain" and result["color"] is None):
        raise LLMError("颜色不在词表中；无法判断时用 uncertain 和 color=null，不能当作 empty")
    shape = result["shape"]
    if status == "known":
        shape = match_vocab(shape, SHAPE_VOCAB, SHAPE_EN)
        if shape is None:
            raise LLMError("known 的 shape 必须来自形状词表；词表外清晰结构用 new")
    elif status == "remembered":
        if not isinstance(shape, str) or shape not in {record["id"] for record in records}:
            raise LLMError("remembered 只能引用本轮目录里真实存在的 ID")
    elif shape is not None:
        raise LLMError("new/uncertain 的 shape 必须为 null，禁止自行编造 ID")
    if status in ("new", "uncertain") and not description.strip():
        raise LLMError("new/uncertain 必须提供几何描述或证据不足原因")

    # 贴纸开放化：只校验大类 label_kind，图案内容用开放描述，码/文字由程序解码
    label_kind = result.get("label_kind") or "none"
    if label_kind not in ("none", "pattern", "text", "qrcode", "barcode"):
        raise LLMError("label_kind 必须为 none/pattern/text/qrcode/barcode")
    label_description = result.get("label_description")
    if label_kind == "pattern":
        if (not isinstance(label_description, str)
                or not label_description.strip() or len(label_description) > 40):
            raise LLMError("pattern 类必须提供不超过 40 字的 label_description")
        label_description = label_description.strip()
    else:
        label_description = None
    return dict(status=status, color=color, shape=shape,
                label_kind=label_kind, label_description=label_description,
                description=description.strip())


def _crop_empty_examples(empty_example):
    """空场景参考图 → 收模型尺寸的列表（识别区顶部/底部各一张）。"""
    if empty_example is None:
        return []
    if isinstance(empty_example, (list, tuple)):
        return [crop_detect_llm(image) for image in empty_example]
    return [crop_detect_llm(empty_example)]


class ShapeRecognizer:
    def __init__(self, memory, empty_example, request=None, shape_examples=()):
        self.memory = memory
        self.empty_example = empty_example
        self.shape_examples = list(shape_examples)
        self.request = request or qwen_realise
        # 空场景参考图只裁一次：它每轮都一样，固定下来才能命中编码缓存。
        # 识别区有顶部/底部两个背景（①② vs ③），所以统一按列表处理。
        self.empty_crops = _crop_empty_examples(empty_example)

    def _resolve_shape(self, result, views):
        """两种识别入口共用状态处理与形状登记；None 表示明确空场景。"""
        if result["status"] == "uncertain":
            raise LLMError("形状证据不足，暂不归类")
        if result["status"] == "empty":
            return None
        shape = result["shape"]
        if result["status"] == "new":
            shape = self.memory.add(result["description"], views)
            logger.info("登记本轮新形状 %s: %s", shape, result["description"])
        return shape

    def classify(self, frames, response_path=None, total_timeout=40.0):
        decisions = []
        try:
            if len(frames) != self.memory.view_count:
                raise LLMError(f"形状记忆识别需要 {self.memory.view_count} 个视角的照片")
            records = self.memory.records
            # 送模型的识别区图统一施加 DETECT_LLM_INSET；手工准备的形状示例图不裁。
            views = [crop_detect_llm(frame) for frame in frames]
            prefix_sections = [
                (build_classify_prefix(self.shape_examples),
                 self.empty_crops + [example["image"] for example in self.shape_examples]),
                (build_classify_prompt(records, self.shape_examples), self.memory.reference_images()),
            ]

            # 一次携带全部参考图；只有 API 包装层处理格式/网络异常重试。
            result = self.request(
                views, PROMPT_CLASSIFY_CURRENT,
                message_save_path=response_path("classify") if response_path else None,
                validator=lambda value: parse_classify_result(value, records),
                total_timeout=total_timeout,
                prefix_sections=prefix_sections,
            )
            decisions.append(result)
            shape = self._resolve_shape(result, views)
            if shape is None:
                return None, None, None, None
            return result["color"], shape, result["label_kind"], result["label_description"]
        except LLMDeadlineExceeded:
            # 当前各视角已由主流程保存；立即返回，避免额外落盘延迟流程恢复。
            raise
        except Exception as error:
            # 兜住所有识别期异常（含白名单外的编程错误），统一转 LLMError 走 YOLO fallback。
            try:
                path = self.memory.save_review(frames, str(error), decisions)
                logger.warning("未归类现场已保存: %s", path)
            except Exception as save_error:
                logger.error("未归类现场无法保存: %s", save_error)
            raise LLMError(str(error)) from error


class FixedViewRecognizer(ShapeRecognizer):
    """已裁剪固定机位图的颜色+形状识别，共用词表校验与记忆登记。"""
    def __init__(self, memory, request=None):
        super().__init__(memory, empty_example=None, request=request)

    def quick_match(self, frames, groups):
        references, catalog = [], []
        for gid, group in groups.items():
            start = len(references) + 1
            references.extend(group["frames"])
            catalog.append({"group": gid, "first_image": start,
                            "last_image": len(references), "color": group.get("color"),
                            "shape": group.get("shape")})

        def validate(value):
            try:
                return parse_group_match(value, groups)
            except ValueError as error:
                raise LLMError(str(error)) from error

        # 目录与参考图放在固定前缀中，当前目标图始终最后发送。
        return self.request(
            frames, build_group_match_prompt(len(frames)),
            prefix_sections=[(build_group_reference_prompt(catalog), references)] if groups else (),
            validator=validate, total_timeout=LASER_QUICK_TIMEOUT,
        )

    def classify(self, frames, response_path=None, total_timeout=LASER_LLM_TIMEOUT):
        if len(frames) != self.memory.view_count:
            raise LLMError(f"固定机位识别需要 {self.memory.view_count} 张图片")
        records = self.memory.records
        result = self.request(
            frames, build_fixed_view_classify_prompt(records, len(frames)),
            total_timeout=total_timeout, message_save_path=response_path,
            prefix_sections=[(PROMPT_SHAPE_REFERENCE, self.memory.reference_images())] if records else (),
            validator=lambda value: parse_classify_result(value, records),
        )
        shape = self._resolve_shape(result, frames)
        if shape is None:
            return None, None, None
        return result["color"], shape, None
