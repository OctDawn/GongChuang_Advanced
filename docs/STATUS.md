# 当前定案（2026-11-17）

> 这份文档是**唯一真源**。代码行为、`.env` 取值、待办优先级，以这里为准；
> 与本文件冲突的口头结论、聊天记录、旧 README 片段都作废。

## 一、当前阶段

线上赛段已结束（已通过复赛），进入线下决赛。剩余约 10 天。

**定案架构**：

```
main_push_llm.py            ← 唯一入口（线下五视角逐件）
  └─ 阶段1 查栏 c：qwen3-vl-plus（2 图：托盘 + 空托盘参考）
  └─ 阶段2 识别 a：qwen3-vl-plus（5 视角：①②③④ + ⑤ 镜面看死角；20 图含固定前缀）
       └─ 失败 → YOLO（RKNN，待验证接入）
       └─ 再失败 → 回 0（本件不分拣）
  显示屏：6 行、白底黑字黑框、无「未归类」行
```

- **主力模型：千问**（DashScope 国内直连，不依赖代理）。
- **网络（2026-11-20 定案）**：4G 设备自带热点 `HRX-CDN-ZZR`，板子 wlan0 连它（`192.168.0.240`，网关 `192.168.0.1`）。
  不占板子 USB、不占插座、不影响相机；手机热点 `我是谁的Mate 60` 保留为备用（priority 10）。
  PC 要 SSH 板子，需同样连 `HRX-CDN-ZZR`，地址 `192.168.0.240`。
- **OpenAI：代码已支持，但本阶段不启用**（不配 `LLM_CHAIN`）。
- **YOLO：作为兜底，尚未开启验证**（`YOLO_FALLBACK=0`）。

## 二、入口与文件职责

| 文件 | 状态 | 说明 |
|---|---|---|
| `main_push_llm.py` | **在用（唯一入口）** | 线下五视角；`ONLINE_MODE=0` / `SINGLE_SHAPE_MODE=0` |
| `llm/qw.py` | **在用** | 单 provider 请求 + 重试；`qwen_realise()` / `warm_up()` 入口可转发给 chain |
| `llm/prompt.py` | **在用** | 五视角分类/查栏 prompt（含开口判据、贴纸规则、镜面机位说明） |
| `llm/shape_recognition.py` | **在用** | 五视角分类主流程、形状记忆 |
| `llm/shape_memory.py` | **在用** | 本次运行内的形状记忆（重启清空） |
| `llm/empty_examples.py` | **在用** | 固定参考图（空场景 + 形状示例）；2026-11-21 新增 ④侧视空场景与 3 张 ④/⑤ 示例 |
| `shared/common.py` / `shared/const.py` / `shared/show.py` | **在用** | 串口、相机、显示、常量 |
| `llm/providers.py` + `llm/chain.py` | **预留（默认不生效）** | 多 provider 链；不配 `LLM_CHAIN` 就走 qw 原路径 |
| `main_online.py` / `main_online2.py` | **冻结** | 线上赛段入口，赛后不再维护，仅留档 |
| `llm/online_batch.py` / `llm/grid_recognition.py` | **冻结** | 线上版1/版2 识别逻辑 |
| `tools/*` | 见下 | 诊断/自检工具，不属于运行时 |

工具分工（避免重复造）：

| 工具 | 用途 | 状态 |
|---|---|---|
| `tools/snap_views.py` | 现场拍识别区五视角并存盘（第 5 路走镜面裁剪） | 在用 |
| `test_llm.py classify` | 用 N 张图离线跑分类（N=STAGE_TWO_DETECTION_COUNT） | 在用 |
| `tools/grid_offline.py` | 单图跑 `classify_grid`（线上版2） | 在用 |
| `tools/net_check.sh` | 校验当前单 provider 的 key/端点 | 在用 |
| `tools/llm_probe.py` | 校验 `LLM_CHAIN` 各家可用性 | 备用 |
| `tools/node_sweep.py` | 逐节点实测能否连 OpenAI | 备用（未实机验证） |
| `tools/online_check.py` / `online_preview.py` / `grid_check.py` | 线上赛段自检 | 冻结 |

## 三、实测数据（2026-11-17 起）

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

**2026-11-19 五视角 + 镜面（关键）**：第 5 路是“对着镜面拍死角”的机位（镜中是**从斜下方向上仰视**的像）。
- 整图直送（含下半幅虚焦桶体）：3/3 判成 `cylinder`（实心，错）；
- 只裁镜面上半部（`DETECT_MIRROR_CROP=0,0,1,0.60`）：3/3 `cylinder_thinWalled`，耗时 3.0 秒。
- 替身实验（第 5 张换成 view_4 副本）：3/3 正确 → 证明是 **⑤ 的内容**在干扰，不是张数/尺寸。

## 四、`.env` 线下定案值

| 键 | 值 | 备注 |
|---|---|---|
| `ONLINE_MODE` | `0` | 否则跑线上版1 |
| `SINGLE_SHAPE_MODE` | `0` | 否则跑线上版2 |
| `STAGE_TWO_DETECTION_COUNT` | `5` | 五视角（④ 之后新增 ⑤ 镜面死角） |
| `DETECT_MIRROR_VIEW_INDEX` | `5` | 第 5 路走镜面专用裁剪；`0` = 关闭 |
| `DETECT_MIRROR_CROP` | `0,0,1,0.60` | 左,上,右,下；只留镜面所在的上 60% |
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
3. **YOLO 兜底**：新模型（shape/color）类别数校验通过（16/12），但**解码输出退化**：
   `yolo_single` 得到零宽框 + 错类别（`hemisphere`/`brown`）；旧 `.bak` 模型（15 类）在同一张图上 **0 检测**。
   结论：新旧都不对齐当前的预处理/解码约定，**保持 `YOLO_FALLBACK=0`**，不要在赛前耗在这条线上。
   复现：`python tools/yolo_single.py --model shape --image <某图> [--model-path ...]`。
6. **五视角实机验证**：接电控跑一整轮，确认第 5 路裁剪后不拖慢、不误判（离线已 3/3）。
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
- 识别区第 5 路（镜面机位）**必须裁剪后再送模型**：整图直送会把下半幅虚焦桶体当成主体，
  3/3 判成实心 `cylinder`；裁到镜面上 60%（`DETECT_MIRROR_CROP=0,0,1,0.60`）后 3/3 正确。
  镜中是“从斜下方向上仰视”的像，方向与实物相反，`⑤` 的机位说明必须写清，否则模型会按错方向推断。
- 例图（2026-11-21 更新）：空场景新增 `empty_side.jpg`（④侧视，已启用），形状示例新增
  `orange_cylinder_thinwalled_side/mirror.jpg`、`red_cube_sticker_mirror.jpg`（共 16 张）。
  `empty_mirror.jpg` 已拍但**未启用**：⑤ 实时图要裁上 60%，空场景参考走的是 crop_to_square，
  取景不一致会干扰模型；要启用需先统一裁剪链。
- Bus01 现挂 ④ + ⑤ 两路：④（icspring，未压缩）降到 **7.5fps**，⑤ 15fps，`fail=0`。
  每件只取 1 帧，7.5fps 够用；但 **Bus01 已满，不要再往这条总线加设备**。
- 4G 棒的可用姿势（2026-11-20）：**插笔记本供电、用设备自带 WiFi 热点**，板子无线连它。
  直接插板子 USB 会拉塌 5V（见上条）；只有一个插座时也没有带电源 hub 的余地。
  设备插上 USB 后热点会不会被切掉，取决于型号，换设备前需重测。
- 训练产物不入库（`.gitignore`）：`runs/`、`weights/`、`yolo_full/`、`yolo_offline_dataset/`、`models_new/`、`*.rknn.bak`、`*.pt`、`*.onnx`。
  入库只有 `models/*.rknn`（部署）与 `models/*_data.yaml`（类别名参考）；重训后记得把新 rknn 覆盖进去。
- **采样温度（2026-11-22）**：`LLM_TEMPERATURE=0` 已纳入定案——同一组图 5/5 输出完全一致。
  没有标准答案、靠裁判主观比对的场景，**自洽性比准确率重要**（同物同盒）。
- **提示词已到天花板（2026-11-22）**：浅色件（淡紫/浅棕被高光淡化成 white）与随机角度下的六棱柱→cube，
  试过加两条专用判据：两件都无改善，且浅色方框从「判错色」退化成 `uncertain`（直接丢件），已回退。
  结论：这两类属于成像/姿态限制，改文字无效；要靠光照（减少镜面高光）或接受。
- **`.env` 行内注释会被当成值的一部分（2026-11-22，踩过）**：`shared/dotenv.py` 不剥 ` #`，
  `YOLO_VIEW_INDICES=1,2   # 注释` 会让 `const.py` 的 `int("2   # 注释")` 抛 ValueError，
  被 `except` 静默吞成 `()`（= 全部视角参与）。表现为"配置写了但毫无效果"，无任何报错。
  **规则：`.env` 里只写 `KEY=VALUE`，注释单独占一行。** 排查：
  `grep -nE "^[A-Za-z_]+=.+[[:space:]]#" .env`，有输出就有隐患（数值型会崩，字符串型会把注释带进值里）。
  验证配置是否生效用 `python -c "from shared.dotenv import load_dotenv; load_dotenv(); import shared.const as c; print(c.XXX)"`。
- **YOLO 视角子集不能拍脑袋减（2026-11-22 实测，同一件橙色通心筒三组五视角）**：

  | 参与视角 | 颜色 | 形状 | 检出覆盖 |
  |---|---|---|---|
  | 全 5 路 | 2/3 orange（220300 被 ⑤ 镜面判成 brown） | **3/3 cylinder** | 3/3 |
  | ①② | 2/2 orange | 0/2（全部掉 `prism5_thinWalled`） | **1/3（215812 检不出）** |
  | ①②③④ | 3/3 orange | 2/3（220300 变 `pyramid4`） | 3/3 |

  结论：**形状投票必须全 5 路**（⑤ 镜面是正确票的来源之一），颜色问题改判据，不要砍视角。
  漏检时 `YoloFallback.classify()` 返回 `(None, None, None)`，`main_push_llm.py:540` 按"无物体"回 0 → 静默丢件。
- **棕/橙判据需同时看 s 与 v（2026-11-22）**：⑤ 镜面把饱和洗到 s=102~108，但 v 仍为 248~249，
  只按 `s<110` 会把橙色通心筒判成 brown，同一件货被拆进两个储物盒。已改为
  `brown if (s < 110 and v < 180) else orange`（`shared/color_hsv.py`）：棕是"又浊又暗"的暖色。
  实测余量：浅棕 r002 s=60/v=152；橙通心筒各视角 s=112~141/v=181~255。
  注意 `tools/color_probe.py` 检不到目标时会静默回退画面中心 45% 区域（1280×720 下像素恒为 186624），
  此时输出的是背景色，**不能当颜色依据**；看结果时别把"未检测到目标"那行 grep 掉。
