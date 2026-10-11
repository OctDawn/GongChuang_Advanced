"""agent 方案配置。

只依赖 shared/（基础设施常量），不 import llm/ 包。
"""
# 单个货物 agent 决策的最大 LLM 轮次（含 rotate/decode/finish/no_object），超出视为失败
MAX_AGENT_STEPS = 6

# 是否允许模型调用贴纸解码工具（yolo/decoder）
ENABLE_DECODE = True
