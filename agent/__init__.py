"""自主分拣 agent（独立于 llm/ 的固定流程）。

agent/ 只依赖 shared/（摄像头/串口/常量/显示）与 yolo/（定位/解码），
不 import llm/ 包的 prompt / shape_recognition / shape_memory。
"""
