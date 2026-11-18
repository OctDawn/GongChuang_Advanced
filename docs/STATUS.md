# 当前定案（2026-11-17）

> 这份文档是**唯一真源**。代码行为、`.env` 取值、待办优先级，以这里为准；
> 与本文件冲突的口头结论、聊天记录、旧 README 片段都作废。

## 一、当前阶段

线上赛段已结束（已通过复赛），进入线下决赛。剩余约 10 天。

**定案架构**：

```
main_push_llm.py            ← 唯一入口（线下四视角逐件）
  └─ 阶段1 查栏 c：qwen3-vl-plus（2 图：托盘 + 空托盘参考）
  └─ 阶段2 识别 a：qwen3-vl-plus（4 视角，19 图含固定前缀）
       └─ 失败 → YOLO（RKNN，待验证接入）
       └─ 再失败 → 回 0（本件不分拣）
  显示屏：6 行、白底黑字黑框、无「未归类」行
```

- **主力模型：千问**（DashScope 国内直连，不依赖代理）。
- **OpenAI：代码已支持，但本阶段不启用**（不配 `LLM_CHAIN`）。
- **YOLO：作为兜底，尚未开启验证**（`YOLO_FALLBACK=0`）。

## 二、入口与文件职责

| 文件 | 状态 | 说明 |
|---|---|---|
| `main_push_llm.py` | **在用（唯一入口）** | 线下四视角；`ONLINE_MODE=0` / `SINGLE_SHAPE_MODE=0` |
| `llm/qw.py` | **在用** | 单 provider 请求 + 重试；`qwen_realise()` / `warm_up()` 入口可转发给 chain |
| `llm/prompt.py` | **在用** | 四视角分类/查栏 prompt（含开口判据、贴纸规则） |
| `llm/shape_recognition.py` | **在用** | 四视角分类主流程、形状记忆 |
| `llm/shape_memory.py` | **在用** | 本次运行内的形状记忆（重启清空） |
| `llm/empty_examples.py` | **在用** | 固定参考图（空场景 + 形状示例） |
| `shared/common.py` / `shared/const.py` / `shared/show.py` | **在用** | 串口、相机、显示、常量 |
| `llm/providers.py` + `llm/chain.py` | **预留（默认不生效）** | 多 provider 链；不配 `LLM_CHAIN` 就走 qw 原路径 |
| `main_online.py` / `main_online2.py` | **冻结** | 线上赛段入口，赛后不再维护，仅留档 |
| `llm/online_batch.py` / `llm/grid_recognition.py` | **冻结** | 线上版1/版2 识别逻辑 |
| `tools/*` | 见下 | 诊断/自检工具，不属于运行时 |

工具分工（避免重复造）：

| 工具 | 用途 | 状态 |
|---|---|---|
| `tools/snap_views.py` | 现场拍识别区四视角并存盘 | 新增，已在板上跑通 |
| `test_llm.py classify` | 用 4 张图离线跑四视角分类 | 在用 |
| `tools/grid_offline.py` | 单图跑 `classify_grid`（线上版2） | 在用 |
| `tools/net_check.sh` | 校验当前单 provider 的 key/端点 | 在用 |
| `tools/llm_probe.py` | 校验 `LLM_CHAIN` 各家可用性 | 备用 |
| `tools/node_sweep.py` | 逐节点实测能否连 OpenAI | 备用（未实机验证） |
| `tools/online_check.py` / `online_preview.py` / `grid_check.py` | 线上赛段自检 | 冻结 |

## 三、实测数据（2026-11-17）

四视角（`qwen3-vl-plus`，`portable`，`reasoning=off`）：

| 指标 | 数值 |
|---|---|
| 单件图片数 / 体积 | 19 张（15 固定前缀 + 4 视角）/ 395~423 KB |
| prompt_tokens | 8715（前缀缓存命中 1280） |
| 单件耗时 | **3.22 ~ 4.25 秒**（5 件样本） |
| 预算余量（按 15s） | 约 3.5 倍 |

单件精度（手电筒照明，仅作能力参考）：

| 物件 | 判定 | 结果 |
|---|---|---|
| 橙色通心筒，竖放 | `cylinder_thinWalled` | ✅ |
| 橙色通心筒，横放 | `cylinder_thinWalled` | ✅ |
| 橙色球 | `uncertain`（四图严重过曝） | ❌ 光照问题 |
| 白贴纸红方块 | `cube` + `label_kind=pattern` | ✅ |
| 橙色四棱锥 | `pyramid4` | ✅ |

线上版2 单图：约 2.6 秒/张。

**2026-11-18 结论（同一批图，只改 prompt）**：删掉「封闭端面上那圈窄边是壁厚、不是空腔证据」这一句后，横放通心筒由 `cylinder`（错）变为 `cylinder_thinWalled`，且竖放/球/贴纸方块/四棱锥全部无回退；横放 5/5、竖放 5/5 稳定。耗时回到 3.0~4.5 秒（尾延迟 6.8 秒一次）。**2026-11-18 21:56 复测**：横放 5/5 正确，其中一次 13.37 秒（超过 12 秒预算，生产环境会回 0）——
属偶发网络抖动（当天 50+ 次中仅 1 次）；保留 `LLM_FALLBACK_TIMEOUT=12`：宁可偶发丢 1 件，也不要把单件拖到 15 秒违规边缘。

## 四、`.env` 线下定案值

| 键 | 值 | 备注 |
|---|---|---|
| `ONLINE_MODE` | `0` | 否则跑线上版1 |
| `SINGLE_SHAPE_MODE` | `0` | 否则跑线上版2 |
| `STAGE_TWO_DETECTION_COUNT` | `4` | 四视角 |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 国内直连 |
| `DASHSCOPE_MODEL` | `qwen3-vl-plus` | |
| `LLM_CACHE_MODE` | `portable` | 千问不支持显式缓存 |
| `LLM_REASONING_EFFORT` | `off` | 传了会 400 |
| `LLM_SERVICE_TIER` | 空 | OpenRouter 专用 |
| `LLM_FALLBACK_TIMEOUT` | `12` | 单件总预算（实测 3~4.5s，尾延迟 6.8s） |
| `YOLO_FALLBACK` | `0` → 待改 `1` | YOLO 验证后再开 |
| `LLM_CHAIN` | **不写** | 写了才启用多 provider 链 |
| `HTTPS_PROXY` / `HTTP_PROXY` | 注释掉 | 千问直连 |

规则常量：`MAX_TEMP_CATEGORIES=6`、`MAX_ITEMS_PER_CATEGORY=4`（赛规：每盒 ≤4 件）。

## 五、待办（按优先级）

1. ~~正常光照下的基线重拍~~ → 已完成（2026-11-18，5 件，4/5→修正 prompt 后 5/5）。
2. ~~`LLM_FALLBACK_TIMEOUT` 定档~~ → 已定 **12**。
3. **YOLO 兜底**：模型重训中；离线验证工具 `tools/yolo_check.py` 已就位。
4. **`MAX_ITEMS_PER_CATEGORY=4` 与电控/盒子联调**：第 5 件回 0 时的行为确认（不卡流程）。
5. **现场彩排**：现场光照 + 现场网络，跑完整一轮（含显示屏、串口时序）。
6. **（可选）OpenAI 链启用**：先用 `tools/node_sweep.py` 实测节点，再配 `LLM_CHAIN`。

## 六、反史山约定

1. **一个入口**：线下只用 `main_push_llm.py`；线上两个入口冻结，不再加功能。
2. **一个配置源**：只有 `.env`（systemd `EnvironmentFile`）；禁止 shell `export` 覆盖。
3. **新功能默认关闭**：`LLM_CHAIN`、`SHOW_UNCATEGORIZED_ROW` 都是这个模式，不配就不改变行为。
4. **删，不要注释**：不再使用的代码直接删（git 可回滚）；确需保留的写进本文档的「冻结」表。
5. **单变量改动**：一次只改一处，改完立即验证，再进下一处。
6. **每次改动一个 commit**：消息写清「为什么」，便于赛前回滚到已知good点。

## 七、已知坑（别重复踩）

- `.env` 重复键：两个加载器都是「先到先得」，重复键会让实际生效值不可预测。改完跑
  `sort .env | grep -vE "^\s*#" | cut -d= -f1 | uniq -d`，必须为空。
- `mihomo` 是唯一代理出口：配置错误会导致全局断网。改它必须「备份 → 改 → `mihomo -t` → 重启」。
- 4G 模组 `enx889e96529263`：已被 NetworkManager 标为 `unmanaged`（`/etc/NetworkManager/conf.d/99-ignore-4g.conf`），**不要启用**。
  2026-11-18 实测：撤销 unmanaged 后它能拨号（192.168.0.245、能 ping 通），但启用的 3 分钟里
  `Bus 03 / Port 3` 的识别区相机反复重枚举 8 次（设备号涨到 16，`usb 3-1.3: USB disconnect`），
  4G 与它不在同一控制器 → 判定为 **5V 供电被拉塌**，不是带宽问题；回退后计数不再增长。
  若确实需要备用：必须给模组**独立供电**（带外接电源的 USB hub），不要赌 USB3 口。
- 手电筒/点光源直射会让高反光件（球）过曝，模型会如实回 `uncertain` → 回 0。
- 识别区加 45° 镜面（2026-11-18 实测）：反射像在原图约 110×80 像素，送模型后只剩 60×45，
  白底贴纸的图案读不出。加三行 prompt 规则（镜像属同一件、镜中贴纸计入、看不清就忽略）后
  仍 3/3 为 `label_kind=none`，且横放通心筒出现一次 `cylinder`（同批图曾 5/5 正确），已整体 revert。
  结论：**镜子这条路线要成立，必须先把反射像做到 ≥150px 并离开画面边缘**；否则建议撤掉。
- 线上版2 的历史图不能代表线下四视角难度。
