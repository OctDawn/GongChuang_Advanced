# agent/ —— 自主分拣 agent（独立于 llm/ 的固定流程）

真正「模型自主决策」的开放集分拣方案。与现有 YOLO / LLM 后端并存，
串口协议（a/b/c/v → r/0~6/f）**完全一致**，电控零改动。

## 和现有方案的本质区别

| | YOLO 版 | LLM 版（llm/） | agent 版（本目录） |
|---|---|---|---|
| 识别决策者 | 代码（RKNN 阈值） | 代码（固定流程 + 词表 + 记忆） | **模型**（自主选择动作） |
| 拍几个角度 | 写死 4 个 | 写死 4 个 | **模型自己决定**（≤4） |
| 贴纸解码 | 代码触发 | 词表 + 代码解码 | **模型按需主动调用** |
| 分组归属 | 三元组 key 硬编号 | 三元组 key 硬编号 | **模型自己判断「完全一致」** |

## 依赖边界

- 复用 `shared/`（摄像头 / 串口 / 常量 / 显示）—— 基础设施。
- 复用 `yolo/`（`DetectRunner` 大托盘定位、`decoder` 贴纸解码）—— 作为工具。
- **不 import `llm/` 包**（不含 prompt / shape_recognition / shape_memory），
  agent 自己维护分组记忆、自己写提示词、自己实现多轮会话客户端。

## 目录结构

```text
agent/
├── main_agent.py       # 入口：YOLO 查栏 + agent 识别编排（a/b/c/v 协议）
├── loop.py             # agent 主循环：模型决策 + 工具 dispatch
├── llm_client.py       # 多轮图文会话客户端（独立，含 .env 读取、JSON 解析）
├── memory.py           # 分组记忆（储物盒分组，本次运行跨轮次累积）
├── prompts_agent.py    # 系统提示词（任务 + 规则 + 动作清单 + 约束）
├── tools.py            # 贴纸解码工具（封装 yolo/decoder）
├── config.py           # 步数上限 / 解码开关等配置
├── test_offline.py     # 离线测试（不接摄像头/串口）
└── README.md
```

## 运行

```bash
# 项目根目录，.env 已配好 DASHSCOPE_API_KEY
python agent/main_agent.py
```

## agent 循环（阶段2）

```
第1张已拍（a 触发）
   ↓
模型看照片 → 输出 action
   ├─ rotate  → 代码发 r、等 b、拍下一张，喂回模型
   ├─ decode  → 代码调 yolo/decoder 解码贴纸，结果喂回模型
   ├─ finish  → 代码按模型判断归组/新建组，回串口 1~6
   └─ no_object → 回串口 0
   ↑________直到 finish / no_object / 步数用尽
```

- 拍几个角度、解不解码、何时结束、归哪组，**全部由模型现场决定**。
- `config.MAX_AGENT_STEPS`（默认 6）兜底防超时；角度上限沿用 `STAGE_TWO_DETECTION_COUNT`（4）。

## 离线测试

```bash
python agent/test_offline.py test_images
python agent/test_offline.py test_images --expected expected.json --save agent_results.json
```

只依赖 openai + opencv（不需要 rknn），PC 上即可跑。每个子目录 = 一件货物，
目录内多张图 = 不同角度。

## 已知限制

- 模型单次判断仍可能出错（遮挡、近似形状、首次建组错误），无绝对稳定保证。
- 15 秒硬约束下，多步 agent 有超时风险；现场需实测单轮延迟，必要时下调 `MAX_AGENT_STEPS`。
- 大托盘查栏仍用 YOLO 定位，其泛化能力取决于 color 模型对前景的响应。
