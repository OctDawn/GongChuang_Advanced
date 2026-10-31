# Orange Pi 视觉分拣程序（LLM 版）

在 Orange Pi 5 Pro（RK3588S，Ubuntu 22.04）上跑视觉识别，通过串口与电控通信，由显示屏呈现分拣结果。
适用于「浙江省大学生工程实践与创新能力大赛·智能分拣赛项」。

- **当前唯一入口**：`main_push_llm.py`（视觉大模型识别，本地 NPU 仅作可选回落）
- **五路相机**：托盘广角 1 路 + 识别区**四路固定机位** 4 路（识别区已取消旋转）
- **启动方式**：`bash deploy/start_vision.sh`（带四道就绪门禁），或手动 `python main_push_llm.py`
- **开机自启**：`deploy/wave-vision-llm.service`（systemd）

> 历史方案（**已归档，不再维护**）：`main_push_yolo.py`（纯 YOLO）、`main_laser_llm.py` + `shared/laser.py`（视觉替换激光传感器）、
> `main_grab.py`（早期抓取）、`agent/`（自主决策）。其中 `yolo/` 目录**仍然保留**：LLM 版的 YOLO fallback、
> 贴纸解码、模型类别数防呆都在用。
>
> 仓库名是 `GongChuang_Advanced`，但板端目录仍叫 `~/WaveRider_GongChuang_Vision`（历史遗留，本文沿用板端路径）。

---

## 目录

- [更新记录](#更新记录)
- [项目结构](#项目结构)
- [当前流程](#当前流程) —— 串口协议 / 阶段编排 / 线程与故障处理
- [视觉调参（离线调试）](#视觉调参离线调试) —— 不装机械也能跑通整条链路
- [识别区四路机位（含回退三路）](#识别区四路机位含回退三路) —— 接线/带宽/对比/回退
- [相机与设备别名（udev）](#相机与设备别名udev) —— 别名表 / 带宽 / **调试机位** / 换线流程
- [运行前配置](#运行前配置) —— 环境 / `.env` / 图片链路 / 缓存 / 形状记忆
- [部署与运行](#部署与运行) —— 服务与门禁 / 图形会话 / 代理 / 手动跑 / 自启开关 / 更新板端
- [排查手册](#排查手册) —— 自启动 / 相机 / 网络 / 显示 / 故障对照
- [工具脚本（tools/）](#工具脚本tools)

---

## 更新记录

> 规则：每条不超过 20 字，写清「谁改的、改了什么」，倒序排列。

**10/31 hrx**
- 新增线上版2：单目 3 色 × 3 形

**10/25 hrx**
- 新增线上入口：整盘批量预判
- 相机掉线自动重连（不整场重启）
- 托盘/② 挪到 Hub 1.4/1.3，别名同步

**10/24 hrx**
- 正多面体按面形状/顶点棱数区分
- 立方体默认；长方体只认明显长条
- 棱锥按 ③ 底面角数判 3/4
- 词表补薄壁六棱柱/正多面体
- 四棱柱并入长方体（同义别名）
- 词表与 RKNN 类别表解耦
- 新形状输出六字中文名上屏

**10/23 hrx**
- 送图定版 full + 1024，square 退役
- 新增启动预热：建连 + 30m 前缀缓存
- 手动启动自动读 `.env`（dotenv 模块）
- 冷启动验收通过，服务已 enable
- 手册修掉三机位/四别名的旧说法
- 预览框跟随送图模式，支持 ④

**10/17 hrx**
- 识别区定版四路，④ 水平侧视上线
- 机位数改环境变量；支持试验第 4 路
- 例图改 ASCII 命名，薄壁/实心成对
- 识别区空场景拆顶部/底部两张
- 补“只有一个视角能看到开口”判据

**10/17 hrx**
- 新增 DETECT_CROP_MODE：全幅/居中两种送图
- 修离线脚本次裁裁剪不一致

**10/17 hrx**
- 新增 offline_debug：拍三路+跑 prompt+计时
- 工具自动读 .env，手动跑不再缺 key

**10/16 hrx**
- 识别区改三路固定机位，取消 r/b 握手
- 相机改 udev 物理口别名 cam_*
- 四路相机拆两条总线解决带宽
- 缩略图取机位①，不再取正下方那路
- udev 规则纳入 deploy/，补调试章节

**10/9 hrx**
- 网络/显示/自启三处加固，初赛完赛
- 启动加三道门禁：摄像头、代理、X

**10/3 hrx**
- 薄壁判据改几何口径（单线/双线）
- 挡住近纯色废帧
- 确认升 reasoning 档无用，保持 low

**10/1 hrx**
- 修显示屏玫红/全黑：强制软件渲染
- 修显示门控竞态（永不清除的唤醒位）
- 确认不必买档位，延迟在上游

**9/28 hrx**
- 显示屏上电黑屏门控（可配 DPMS）

**9/26 hrx**
- 贴纸解码改为总开关、默认关闭
- 新增替换空场景参考图工具

**9/25 lj**
- 新增 laser 视觉方案（已归档）

**9/25 hrx**
- 提速：连接保活 + reasoning low + 参考图 512
- 取帧重试 5 次；末行固定「未归类」
- 新增 net_check.sh 与 mihomo 部署文件

**9/25 hrx**
- 裁剪/压缩参数可调；新增裁剪预览工具
- 修 test_llm 托盘图序 bug

**9/22 lj**
- 新增 laser_sensor_replace 测试脚本

**9/17 hrx**
- 词表归一化；分类异常统一兜底

**9/16 hrx**
- 默认识别后端切到 gpt-6-astra
- 新增离线测试脚本 test_llm.py

**9/16 lyc**
- 微调提示词；启用 Responses 显式缓存

**9/15 hrx**
- 修摄像头打开失败退不出去

**9/15 lyc**
- 新增查栏与分类的 YOLO fallback

**9/14 lyc**
- 加示例照片；40 秒不出结果则跳过

**9/13 lyc**
- 发空样本图；串口默认 ttyS0
- 去掉 temperature，兼容 qwen 与 gpt

**9/13 hrx**
- 每盒上限限死；显示屏改流水表格
- 货物名称中文化，行高适配方图

**9/12 hrx**
- 贴纸识别开放化（label_kind + 描述）
- agent 重构为自主决策（已归档）

**9/11 lyc**
- 新增未知形状记忆；代码分目录

**9/11 hrx**
- 错误分流重试 + 词表校验
- 表格全屏，字号随分辨率缩放

**9/11 hrx**
- 合并 YOLO 与 LLM 两方案，抽公共层

**9/10 hrx**
- label 扩到 11 类；托盘 4 列改 5 列

**9/8 hrx**
- 整理 README；main.py 更名 main_grab.py

**9/8 lyc**
- 对齐 const.py；类别满时回 0

**9/7 lyc**
- 添加开机自启服务

**9/6 lyc**
- 阶段 1 同时等 a/c；波特率 115200
- 存画框图时同步存原图

**9/4 hrx**
- 修多线程死锁与帧管理；串口异常保护

**9/3 hrx**
- 调 NMS 与置信度阈值
- 阶段 2 增加叠加诊断图
- 分离托盘与识别区颜色列表

**9/2 hrx**
- 拆出 PushController；修 read_camera
- 查栏回复改布尔数组；v 后回 f

**9/2 lj**
- 小改摄像头相关程序，合并大模型分支

**8/31 hrx / lyc**
- 阶段 2 改多角度转向识别
- 契合串口协议，等 f 后上屏

**8/28 lyc**
- 新增 show.py 与显示线程

**8/27 hrx**
- 抓取改推铲推料，托盘分列映射

**8/24 hrx**
- 识别区加贴纸识别与解码

**8/23 lj**
- 常量集中到 const.py；采集移入 tools

---

## 项目结构

```text
GongChuang_Advanced/
├── main_push_llm.py             # 主入口（线下）：串口协议 + 阶段编排 + 逐件 4 视角识别
├── main_online.py               # 线上版1：整盘批量预判 + 固定取件顺序
├── main_online2.py              # 线上版2：单目 3 色 × 3 形（只开 1 路识别区相机）
├── test_llm.py                  # 离线测试（PC 上喂图片，不碰硬件）
├── requirements.txt
├── .gitattributes               # 全仓 LF（.sh 必须 LF，否则板上报 'bash\r'）
├── .env.example / .env          # .env 已 gitignore，含 key 与代理三行
├── llm/                         # 大模型层
│   ├── qw.py                    # OpenAI 客户端：缓存模式、重试、图片编码、启动预热
│   ├── prompt.py                # 词表、提示词、纠错提示
│   ├── shape_recognition.py     # 多机位识别、校验、形状登记（机位数随常量）
│   ├── shape_memory.py          # 本次运行的形状参考图库（N 视角拼图，默认 4）
│   ├── online_batch.py          # 线上版1：6 类参考图 + 整盘一次识别（按取件顺序排序）
│   ├── grid_recognition.py      # 线上版2：单目 3 色 × 3 形固定词表（9 类）
│   ├── empty_examples.py        # 空场景图与带标签示例加载
│   └── example/                 # 3 张空场景（托盘/顶部/底部）+ 11 张形状示例（顶部/底部成对）
├── yolo/                        # 本地 NPU 层（LLM 版按需使用）
│   ├── inference_utils.py       # DetectRunner 与后处理、类别数防呆
│   ├── fallback.py              # YOLO 回落（YOLO_FALLBACK=0 时不加载）
│   ├── decoder.py               # 贴纸解码
│   └── py_utils/                # RKNN / ONNX 执行器
├── shared/
│   ├── const.py                 # 全部常量（串口、摄像头、类别、裁剪比例）
│   ├── common.py                # 摄像头、帧管理、串口、取帧重试、裁剪
│   ├── show.py                  # pygame 全屏表格与待机黑屏
│   └── screen.py                # xset 封装（关屏保 / DPMS）
├── models/                      # 4 个 RKNN 模型 + 类别 yaml
├── tools/                       # 现场排查与调优脚本（见文末）
└── deploy/                      # systemd 服务、启动包装、udev 规则
    ├── start_vision.sh          # 四道门禁 + 启动主程序
    ├── wave-vision-llm.service  # 开机自启单元
    ├── wave-hdmi-watch.service  # 显示看门狗（可选）
    ├── 70-wave-cameras.rules    # 相机固定别名（拷到 /etc/udev/rules.d/）
    ├── mihomo.service           # 板内代理
    └── mihomo-config.example.yaml
```

按文件名运行，例如 `python main_push_llm.py`。所有相对路径（`capture/`、`models/`）都以项目根目录为基准。

---

## 当前流程

五路相机（托盘广角 1 + 识别区四路固定机位 4），视觉只做识别与显示，机械/电控负责动作。

### 串口协议

| 方向 | 指令 | 含义 |
| --- | --- | --- |
| 电控 → 视觉 | `c` | 查栏：识别托盘各栏占用 |
| 电控 → 视觉 | `a` | 开始下一轮（直接进入阶段 2） |
| 电控 → 视觉 | `v` | 请求显示 |
| 视觉 → 电控 | `1/0 ×5` | 查栏结果，每栏一位、带换行 |
| 视觉 → 电控 | `0` / `1~6` | 分类编号（0 = 无物体 / 失败 / 类别或盒已满） |
| 视觉 → 电控 | `f` | 显示完成回执 |

> `b`（转向完成）/ `r`（要求转向）是机械改造前的握手，**现已废弃**：识别区改为四路固定机位、不再旋转。
> 收到 `b` 只记一条 debug 日志（不置位任何事件），`r` 已从代码里删除。

### 阶段编排

1. 程序持续读取托盘广角与**识别区四路固定机位**（① ② 斜上方相对、③ 正下方仰视、④ 水平侧视），各自只保留最新一帧；
   托盘与识别区① 另外录像（四路同时写 mp4 会抢 IO，要全录就再各建 writer）。
2. **阶段 1**：同时监听 `c` 与 `a`。收到 `c` → 识别托盘各栏，回复 5 位 `1/0`；收到 `a` → 直接进入阶段 2。
3. **阶段 2**：`a` 一到，从四路机位各取一张「`a` 之后」的最新帧（先丢积压旧帧再取新帧，四路同时拍、
   误差约 1 帧 ≈ 33ms），**全程没有 `r`/`b` 握手**（`STAGE_TWO_DETECTION_COUNT=4`）。
4. 四张图一次性交给大模型，得到分类编号后回 `0` 或 `1~6`。
5. 电控动作完成发 `v` → 视觉先上屏，再回 `f`。
6. 回到第 2 步，直到所有有物体的栏都推到底。

> 兜底：LLM 超时或失败时回 `0`（不回落 YOLO，见下文 `YOLO_FALLBACK`）。查栏失败则保守回全 `1`。
>
> **图号 ↔ 机位是写死在 prompt 里的契约**：图1 = ①斜上、图2 = ②斜上、图3 = ③正下仰视、
> 图4 = ④水平侧视（机位数 = `STAGE_TWO_DETECTION_COUNT`）。四台相机装错位置会让模型按错误的
> 视角关系推理（见「换 USB 口 / 换相机后怎么做」）。

### 显示屏

- 固定 7 行：6 个类别 + 末行「未归类」。
- 上电只铺纯黑，收到首个 `a`/`c` 才点亮画表格（`SCREEN_OFF_UNTIL_START=1`）。
- **「图片」列取机位①的画面**（`DISPLAY_VIEW_INDEX=0`）。不能取机位③：那是正下方仰视，
  可能完全拍不到本体。要换成 ② 就把 `.env` 里 `DISPLAY_VIEW_INDEX` 设成 `1`。

### 线程与故障处理

- **五路摄像头线程**（托盘 1 + 识别区 4）：每路一个 `maxsize=1` 队列只留最新帧；`get_frame`
  连续多次取不到（或全是近纯色废帧）才判故障。
- **掉线自动重连**：`read_camera` 读帧失败先原地快试 5×0.1s，再按 0.3/0.6/1.2/2.4/5.0s 退避
  重新打开别名（合计约 9.5s），成功后丢前 3 帧继续跑；只有全部失败才判故障退出。
  重连期间相机线程置位一个 `recovering` 标记，主线程的 `_wait_frame` 看到它就最多多等 20s，
  否则主线程 5 秒就 `CameraStopped`，重连等于白做。
  日志：`开始重连（最多约 9.5s）` → `重连成功（第 N 次…）` 或 `重连失败（已尝试 5 次）`。
- **串口线程**：独立收发，读写异常置位 `stop_event`。
- **显示线程**：见上；门控用一个「永不清除」的唤醒位，避免和会立即 `clear()` 的事件抢采样。
- **主线程**：阶段编排。任一上游故障 → 抛 `CameraStopped` → 清理退出；所有等待都带超时，不会永久阻塞。

---

## 视觉调参（离线调试）

机械/电控没装好也能调视觉：**只拍图 + 跑 prompt + 记录**，完全不碰串口、显示屏、YOLO。

工具是 `tools/offline_debug.py`，它复用线上同一套模块（`llm.qw` / `llm.prompt` /
`llm.shape_recognition` / `llm.shape_memory` / `llm.empty_examples`），
所以图号顺序、裁剪、prompt、校验、形状记忆与线上完全一致——**离线跑出什么，线上就是什么**。

```bash
# 一步到底：四路各拍一张 + 跑 1 次
python tools/offline_debug.py --capture

# 拍一组，跑 3 次看稳定性和耗时分布
python tools/offline_debug.py --capture --tag 蓝薄壁4棱 --runs 3

# 用最近一次采集反复跑（不用再开相机）
python tools/offline_debug.py --latest --runs 5

# 用自己准备的一组图（顺序必须：①斜上 ②斜上 ③正下仰视 ④水平侧视）
python tools/offline_debug.py --images a.jpg b.jpg c.jpg

# 只走流程不发请求：验裁剪/尺寸/命名（不需要 key）
python tools/offline_debug.py --latest --dry-run

# 看历史采集
python tools/offline_debug.py --list
```

**前置条件**：主程序在跑会占着相机 → 先 `sudo systemctl stop wave-vision-llm`。
工具会**自动读项目根目录的 `.env`**（不覆盖已有环境变量），所以不用先 `source`。

输出在 `capture/offline/<时间戳>[_tag]/`：

| 文件 | 内容 |
| --- | --- |
| `view_1..N.jpg` | 相机原始帧（未裁剪），N = 机位数（默认 4） |
| `sent_1..N.jpg` | **模型真正看到的那张**：`full` → 1024×576，`square` → 532×532 |
| `reply_01_classify.txt` | 模型原始回复（重试带 `_try2` 后缀，每次都留） |
| `result.jsonl` | 每次 run 一行：status/颜色/形状/贴纸、总耗时、真实请求耗时、token、payload、错误 |
| `meta.json` | 设备、模型、缓存模式、机位顺序、时间戳 |

采集时会同时打印每路的 `mean/std` 并标记疑似废帧（过曝/全黑/未就绪）。
跑完打一张汇总：耗时中位/均值/最值、真实请求耗时、`N/M 次结果一致`、缓存命中率。

两个行为约定：

- **每次 run 默认新建形状记忆**（等于“程序刚启动”），所以 `new` 不会被上一次的 `remembered` 干扰；
  要模拟“同一场次里连续第 N 件”，加 `--keep-memory`。
- `status`（known / remembered / new / uncertain / empty）是从模型**原始回复**里解析的，
  不靠返回值反推——所以 `new` 和 `remembered` 能分清。

### 送图尺寸与速度

识别区送什么图由两个环境变量控制。**代码默认仍是 `square`，线上定版是 `full` + 1024**——
所以 `.env` 里必须显式写这两行，漏了会静默退回 `square`（居中裁 720，边角物体被切掉，而且日志
只打一行「送图模式: square」不会报错）：

| 配置 | 实际送模型尺寸 | tokens/图（实测标度） | 视野 |
| --- | --- | --- | --- |
| `DETECT_CROP_MODE=square` | 532×532 | ≈ 300 | **只看中间 720 宽**，边角被切掉（仅代码默认值，已退役） |
| `full` + `DETECT_SEND_MAX_SIDE=768` | 768×432 | ≈ 370 | 全幅（细节打折） |
| `full` + `DETECT_SEND_MAX_SIDE=1024` | 1024×576 | ≈ 645 | 全幅 ✅ 推荐 |
| `full` + `DETECT_SEND_MAX_SIDE=1280` | 1280×720（原图） | ≈ 1035 | 全幅、细节不损 |

要点（**10/17 实测**，不是估算）：

- **token ≈ 像素面积 / 890**，与“512 分块”无关。所以 532² ≈ 300 tokens/图，
  1024×576 ≈ 645，1280×720 ≈ 1035。
- 从 `square` 换到 `full` + 1024：prompt 7944 → 8979，**每图多 345，整体 +13%**；
  请求耗时中位 4.39s → 4.05s——**差异完全在单次波动（2.7~6.9s）的噪声里**。
  （这是 10/17 的旧数字；例图扩到 11 张后定版 prompt 已涨到约 10320，增量全在参考图与规则，与送图模式无关。）
- **质量差距是决定性的**：同一组图上 `square` 三次给了三个答案（其中一次模型自述
  “物体在各视角中均被画面截断”），`full` + 1024 三次全对且一致。
- **坑：`LLM_MAX_SIDE` 会二次封顶**。`qw.py` 发送前还有一道 `_downscale(…, LLM_MAX_SIDE)`，
  你设 `DETECT_SEND_MAX_SIDE=1280` 但 `LLM_MAX_SIDE=1024` 时，实际送图仍是 1024×576
  （payload/token 一模一样）。要真发原图，**两个都要改**。
  `tools/offline_debug.py` 在检测到这种组合时会打 ⚠。
- 四个机位是**同一物体**，信息有冗余；真要极简，可以单张全幅 + 两张小图，但目前不支持分开设。

**别猜，实测**（同一个 session 的图，只改环境变量）：

```bash
python tools/offline_debug.py --latest --runs 3                                        # square 基线
DETECT_CROP_MODE=full DETECT_SEND_MAX_SIDE=1024 python tools/offline_debug.py --latest --runs 3
DETECT_CROP_MODE=full DETECT_SEND_MAX_SIDE=1280 LLM_MAX_SIDE=1280 python tools/offline_debug.py --latest --runs 3
```

看汇总里的 token 数（缓存命中那行）和「真实请求耗时」：增幅符合预期、结果没变差，就写进 `.env` 定下来。

---

## 识别区四路机位（含回退三路）

**现状：识别区是 4 路**（`STAGE_TWO_DETECTION_COUNT=4` 已是默认）：①② 斜上相对、③ 正下仰视、
④ 水平侧视。④ 是用来补 ①② 的死角：45° 俯视会把两侧竖直面压扁，而 ④ 平视正对竖直面，
对「薄壁开口 / 壁厚」这类判据是直接加分。

想退回 3 路：`.env` 里写 `STAGE_TWO_DETECTION_COUNT=3`，别名、图号、prompt 机位说明、
形状记忆视角数全部自动跟着变，**代码一行不用改**。

### 第 1 步：接线（先过带宽这一关）

铁律：**每条 480M 总线最多 2 路 MJPG 流**（第 3 路 `STREAMON` 直接 `ENOSPC`）。
现在是 5 路（托盘 + 4 路识别区），必须分散到 **≥3 条总线**（2+2+1 或 2+1+2）。

```bash
lsusb -t          # ehci-platform 出现几次，就是几条独立 480M
```

- **坑**：把“新相机 + 现有相机”接到一个 Hub、再把 Hub 插到已经 2 路的口上 → 那一路变 3 路，必挂。
- 优先用**空着的 xhci**：Bus06 是 5000M 的 USB3 口，USB2 相机会落在 Bus05 的 480M 侧；
  Bus05 目前经 GenesysLogic Hub 挂了**托盘 + ②两路**，再加第 3 路必挂——要么换口，要么换总线。
  （ZTE 4G 网卡挂在 Bus01 的嵌套 Hub 上，不在 Bus05。）
- 接好后再跑一次 `lsusb -t` 确认每路所在总线，然后拿 ID_PATH：

```bash
for v in /dev/video*; do printf "%s  " "$v"; udevadm info -q property -n "$v" | grep '^ID_PATH='; done
```

### 第 2 步：钉别名

在 `/etc/udev/rules.d/70-wave-cameras.rules` 加一条（模板在 `deploy/70-wave-cameras.rules` 末尾，
已经注释好），把 `ID_PATH` 换成新相机的真实值：

```text
SUBSYSTEM=="video4linux", ATTR{index}=="0", ENV{ID_PATH}=="<新相机>", SYMLINK+="wave/cam_detect_4"
```

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux --action=add
ls -l /dev/wave/            # 应该多出 cam_detect_4
```

### 第 3 步：A/B 实测

```bash
sudo systemctl stop wave-vision-llm          # 相机被主程序占着

# ① 四路（现在就是默认）
python tools/offline_debug.py --capture --tag 四路对比 --runs 3

# ② 同一组图的三路对照（只看前 3 张，不用重拍）
STAGE_TWO_DETECTION_COUNT=3 python tools/offline_debug.py --latest --runs 3
```

> `--latest` 只要求 `view_1..N` 存在；带 `STAGE_TWO_DETECTION_COUNT=3` 时它只加载前 3 张，
> 所以两次跑的是**同一件物体、同一批图**，对比才有意义。

**建议额外做一件事**：补一张侧向空场景 `llm/example/empty_side.jpg`。
④ 看到的背景跟顶部/底部都不一样，没有这张它会把固定结构当物体。
它是**可选**的：文件不存在就自动跳过，图号会按实际张数自己对齐（不用改 prompt 数字）。

### 第 4 步：看什么

- **质量**：④ 有没有让 `uncertain` 变少、薄壁/实心判得更稳（重点看以前靠猜的那几件）；
- **成本**：+1 张 1024×576 大约 +645 tokens（+7%），请求耗时基本在噪声里，
  `LLM_FALLBACK_TIMEOUT=15` 还有余量；
- 如果一致性没明显提升，**就不要上**——多一路相机、多一条总线约束，维护成本不低。

### 决定上 / 回滚

**已经是这个状态**：机位默认 4，`deploy/70-wave-cameras.rules` 里 5 条别名都是生效的，
`start_vision.sh` 的门禁会自动等 `cam_detect_4`。

**退回三路**：`.env` 里加一行 `STAGE_TWO_DETECTION_COUNT=3`，重启服务即可。
`cam_detect_4` 别名留着不碍事（`const.py` 只按机位数取前 N 个）。

---

## 相机与设备别名（udev）

五路相机全部走 `/dev/wave/cam_*`，别名由 `/etc/udev/rules.d/70-wave-cameras.rules` 按
**USB 物理口**绑定，代码里不再出现 `/dev/videoN`。

规则源文件就在仓库里：`deploy/70-wave-cameras.rules`。

| 别名 | 机位 | 物理口（`ID_PATH`） | 总线 |
| --- | --- | --- | --- |
| `cam_pallet` | 托盘上方广角（0bda:3035，1280×960） | `platform-xhci-hcd.10.auto-usb-0:1.4:1.0` | Bus05（xhci，USB3 口 Hub） |
| `cam_detect_1` | 识别区① 斜上方（俯约 45°） | `platform-fc880000.usb-usb-0:1.3:1.0` | Bus03（fc880000） |
| `cam_detect_2` | 识别区② 斜上方（与①相对，0bda:9143） | `platform-xhci-hcd.10.auto-usb-0:1.3:1.0` | Bus05（xhci，USB3 口 Hub） |
| `cam_detect_3` | 识别区③ 正下方仰视（看底面） | `platform-fc880000.usb-usb-0:1.4:1.0` | Bus03（fc880000） |
| `cam_detect_4` | 识别区④ 水平侧视（补 ①② 死角） | `platform-fc800000.usb-usb-0:1.2:1.0` | Bus01（fc800000） |

安装：

```bash
sudo cp deploy/70-wave-cameras.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux --action=add
ls -l /dev/wave/
```

规则要点（都是踩过的坑）：

- **不用 `by-id`**：两台相机 USB 描述符完全相同（`0bda:9143`，假序列号都是 `200901010001`），
  `by-id` 名字直接撞车，其中一台干脆没有 `by-id` 名字。
- **属性必须写成 `ENV{ID_PATH}`**：写成 `ID_PATH=="…"` 会被 udev 判成 `Invalid key`，
  **静默丢弃整条规则**（不报错，只是别名一直不出现）。自查：`sudo udevadm test /sys/class/video4linux/video0 2>&1 | grep -i invalid`。
- **必须加 `ATTR{index}=="0"`**：每台 UVC 相机同时暴露 `index0`（采集）与 `index1`（metadata），
  两者 `ID_PATH` 相同；不加判断别名会被后一个覆盖，指过去打不开。

### 带宽：每条 480M 总线最多 2 路

现在是 **5 台相机（托盘 + 识别区 4 路）**，分在**三条独立总线**上（2+2+1）：

```
Bus03 fc880000   ①斜上 + ③正下                    2 路
Bus05 xhci 480M  托盘 + ②斜上   [USB3 口 Hub]      2 路
Bus01 fc800000   ④平视 + 嵌套 Hub(ZTE 网卡)        1 路
```

每条（480M）只放得下约 **2 路** MJPG 流。第 3 路 `STREAMON` 会直接失败，dmesg 报：

```text
uvcvideo 1-1.2.3:1.1: Failed to submit URB 0 (-28).
```

`-28` = `ENOSPC` = 等时带宽准入失败，**不是供电问题**（供电不足的表现是设备掉线重枚举，不是这个错）。
排查看 `lsusb -t`：`ehci-platform` 出现两次就是两条独立 480M 总线；Bus06（`xhci 5000M`）是空着的备用口。

### 调试相机机位

> **前提：主程序正在占着全部相机，必须先把服务停掉**，否则相机打不开。

```bash
# 1) 让出相机
sudo systemctl stop wave-vision-llm
pgrep -af main_push_llm           # 应输出为空

# 2) 网页预览（推荐：不依赖 X11，PC 浏览器直接看）
cd ~/WaveRider_GongChuang_Vision
python tools/preview_camera.py --camera detect_up1 --http 8080 --tray
#    PC 浏览器打开  http://<板子IP>:8080/     （热点链路一般是 http://192.168.43.5:8080/）

# 3) 调完位置，恢复主程序
sudo systemctl start wave-vision-llm
```

- **`--tray` 的叠加框跟着 `DETECT_CROP_MODE` 变**（名字是历史遗留，对识别区来说它画的是识别区叠加框）：
  - `square`：**蓝框** = 居中正方形（1280×720 → 720×720），**红框** = `DETECT_LLM_INSET` 内缩后的 532×532；
  - `full`（定版）：不裁边，**红框** = `DETECT_FULL_INSET`（当前为 0，即整个画面），角上标出实际送图尺寸 1024×576。
  `--show-llm` 和线上走同一条链路（`detect_frame_preprocessed` → `crop_detect_llm`），看到的就是真送图。
- **定版 `full` 下的取景要求**：不要求居中占满，物体在画面内就行（边角也能拍全）；
  但 720p 全幅缩到 1024×576 后单件细节比 532 方图少，且四角有镜头畸变，尽量让物体占画面 1/4 以上。
- `--camera` 取值：`pallet` / `detect_up1` / `detect_up2` / `detect_down` / `detect_side`（④ 水平侧视），
  机位个数跟随 `STAGE_TWO_DETECTION_COUNT`；旧命令 `detect` = `detect_up1`。
- **裁剪/缩放在软件里，不在相机里**：相机永远输出 1280×720；`full` 模式不裁边、等比缩到长边 1024
  （1024×576），`square` 模式才 `crop_to_square` → 720×720（左右各切掉 280 px）→ 内缩 0.13 → 532×532。
  所以**不要去把相机分辨率改成正方形**：多数 UVC 不支持，会退化成缩放或默认值，反而更糊。
- 想在板子自己的屏上开窗口、并同时看「实际发给模型的图」：
  `DISPLAY=:0 python tools/preview_camera.py --camera detect_up1 --show-llm`
  窗口模式按键：`q`/`Esc` 退出、`s` 存快照、`o` 开关叠加框、`c` 开关分栏线。
- 页面连不上（PC 与板子不同网段）就用 SSH 隧道：
  `ssh -L 8080:localhost:8080 orangepi@192.168.43.5`，再开 `http://localhost:8080/`。
- 另外可用冒烟测试一次性验四路带宽：`python tools/probe_cameras.py`（走别名，四路各 90 帧）。

### 换 USB 口 / 换相机后怎么做

```bash
# 1) 看现在谁在哪条总线、每个节点的物理口
for v in /dev/video*; do echo "== $v"; readlink -f /sys/class/video4linux/$(basename $v)/device; done
# 2) 拿到 ID_PATH（udev 规则就是按它匹配的）
for v in 0 2 4 6; do printf "video%s  " $v; udevadm info -q property -n /dev/video$v | grep '^ID_PATH='; done
# 3) 按新 ID_PATH 改 deploy/70-wave-cameras.rules 并同步到板上，然后
sudo cp deploy/70-wave-cameras.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux --action=add
# 4) 验收：五个别名要指向真实采集节点（index0）
ls -l /dev/wave/
python tools/probe_cameras.py     # 四路 90/90 才算过
```

> ⚠️ **别名绑的是 USB 物理口，不是相机**。所以「换相机」= 把相机装到目标机位 + 插到该机位别名对应的口，
> **代码零改动**。如果只是随手对调插头而相机没挪位置，图1/2/3 的画面就串了，prompt 的视角契约被破坏。
> 拔插**不需要** reload 规则（规则没变，udev 会自动应用到新枚举的设备）；只有改了规则文件内容才要 reload。
> 改线后记得三处一起更新：`deploy/70-wave-cameras.rules`、本文这张表、`shared/const.py` 的注释。

---

## 运行前配置

### 环境准备

```bash
conda create -n llm python=3.10 -y
conda activate llm
pip install -r requirements.txt        # 不含 rknnlite，板端环境需自带
cp .env.example .env                   # 填 key、代理三行
python main_push_llm.py
```

### `.env` 关键项

| 变量 | 线上取值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | （必填） | 变量名沿用旧名，实际是当前服务商的 key |
| `DASHSCOPE_BASE_URL` | `https://api.openai.com/v1` | OpenAI 官方 API |
| `DASHSCOPE_MODEL` | `gpt-6-astra` | 官方端点不带厂商前缀 |
| `LLM_CACHE_MODE` | `openai_explicit` | **不要改成 `portable`**：官方 `/chat/completions` 不接受 `service_tier`，会 400 |
| `LLM_SERVICE_TIER` | 空 | 留空即不传；`/responses` 接受 `ultrafast`，但实测不必买档位 |
| `LLM_FALLBACK_TIMEOUT` | `15` | 单轮 LLM 总时限（**硬规定，不改**），超时即回 `0` |
| `LLM_RETRIES` | `1` | 常规失败重试次数（总尝试 = 1 + retries） |
| `LLM_CONNECTION_RETRIES` | `2` | 连接类错误的**额外**重试次数（丢池重建后重试） |
| `YOLO_FALLBACK` | `0` | `0` = 不加载 YOLO 模型，失败即回 `0`；`1` = 启用回落 |
| `LLM_JPEG_QUALITY` | `92` | 发送前 JPEG 质量（85 会把薄壁口沿压掉） |
| `LLM_IMAGE_SCALE` | `1.0` | 发送前等比缩放 |
| `LLM_MAX_SIDE` | `1024` | 图片最长边的**最终天花板**：比它大的送图尺寸会被悄悄压回（见「送图尺寸与速度」） |
| `LLM_REF_MAX_SIDE` | `512` | **只作用于前缀固定参考图**，压体积收益最大 |
| `LLM_REASONING_EFFORT` | `low` | 砍生成阶段长尾；`off` = 用服务端默认 |
| `LLM_KEEPALIVE_EXPIRY` | `20` | 连接空闲保活秒数；太长会复用僵尸连接 |
| `LLM_WARMUP` | `1` | 启动预热：提前建连 + 写入 30 分钟前缀缓存；`0` 关闭（见「启动预热」） |
| `LLM_TIMEOUT` | `60` | 单次请求超时（实际受 `LLM_FALLBACK_TIMEOUT` 约束） |
| `LLM_STICKER_DECODE` | `0` | 贴纸解码总开关，`0` 时 label 只取 LLM 给的 kind/描述 |
| `SCREEN_OFF_UNTIL_START` | `1` | 上电只铺纯黑，收到首个 `a`/`c` 才画表格 |
| `SCREEN_DPMS` | `0` | `1` 时用 `xset dpms force off/on` 真灭背光（需 x11-xserver-utils） |
| `DISPLAY_VIEW_INDEX` | 空（=0） | 显示屏「图片」列取哪个机位：`0`=①斜上（默认）、`1`=②斜上、`2`=③正下 |
| `DETECT_CROP_MODE` | `full` | 送图方式：`full`=全幅等比缩放（**定版**）；`square`=居中方正 720→内缩 532（已退役，边角会被切掉）。**代码默认仍是 `square`，`.env` 不写就静默退回** |
| `DETECT_SEND_MAX_SIDE` | `1024` | `full` 模式的长边上限（只缩不放）：`768` 更快 / `1024` 均衡 / `1280` 原图直发 |
| `STAGE_TWO_DETECTION_COUNT` | 空（=4） | 识别区机位数，默认 4；写 `3` 退回三路（见「识别区四路机位」），会同时改取帧路数/prompt/记忆视角数 |
| `HTTPS_PROXY` / `HTTP_PROXY` | `http://127.0.0.1:7890` | **必须写进 `.env`**：systemd 不读 `.bashrc` |
| `NO_PROXY` | `localhost,127.0.0.1,…` | 别把代理自己排除掉 |
| `CAMERA_PALLET_ID` | 空（=别名） | 覆盖托盘相机路径，默认 `/dev/wave/cam_pallet` |
| `CAMERA_DETECT_1/2/3/4` | 空（=别名） | 覆盖识别区四路，默认 `/dev/wave/cam_detect_1..4` |

改完重启生效：`sudo systemctl restart wave-vision-llm`（**不需要** daemon-reload）。

### 例图与空场景（`llm/example/`）

**命名规范**（全 ASCII——Windows 下 `cv2.imread` 读不了中文路径）：

```text
<color>_<shape>[_<pose>]_<view>.jpg      view = top（①②斜上）/ bottom（③正下）
```

| 文件 | 说明 |
| --- | --- |
| `empty_top.jpg` | 识别区**顶部背景**（①② 机位看到的空场景） |
| `empty_bottom.jpg` | 识别区**底部背景**（③ 机位，背光/支架/亮边都不同） |
| `pallet_empty.jpg` | 托盘空场景（阶段 1 查栏用） |
| `black_prism4_thinwalled_top/bottom.jpg` | 同一件薄壁件的两个视角 |
| `black_cube_top/bottom.jpg` | 实心对照（与黑色薄壁四棱柱刻意同色） |
| `orange_prism3_top.jpg` / `orange_pyramid4_lying_top.jpg` | **横放对照**：两者轮廓都是三角形，只能靠“有没有汇聚点”区分（棱锥有多条棱汇于一点，棱柱没有） |
| … | 实心/薄壁、黑/彩色、竖放/横放均留对照 |

**为什么成对给**：识别区是“同一个物体、多个视角”（① ② 斜上 + ③ 正下 + ④ 平视）。同一件薄壁物体**通常只有一个视角能
看到开口**（开口朝上 → ①② 看到内腔、③ 看到封闭端面；开口朝下则反过来）。封闭端面上那圈与外形
平行的窄边是**壁厚/第一层打印面**，既不是空腔证据、也不是实心证据——所以每一对里都要有一张
“封闭端面长什么样”，否则模型会把那圈线当成开口（这就是“实心被误判成薄壁”的主要来源）。

**加/换例图的步骤**（先停主程序释放相机）：

```bash
# 1) 用真实机位拍一组（张数 = 机位数）
python tools/offline_debug.py --capture --dry-run      # 存 view_1..N.jpg
# 2) 挑图改名放进 llm/example/，再在 llm/empty_examples.py 的 SHAPE_EXAMPLES 里登记
# 3) 自检：文件名、词表、孤儿图、形状覆盖
python tools/check_examples.py
```

**换空场景**（换了底板/背景必须换，否则模型会拿旧背景当基准）：

```bash
python tools/set_empty_reference.py <未裁剪原图> --scene empty_top     # ①② 顶部
python tools/set_empty_reference.py <未裁剪原图> --scene empty_bottom  # ③ 底部
python tools/set_empty_reference.py <未裁剪原图> --scene pallet
```

> 空场景图必须是**未裁剪的相机原图**；`--dry-run` 会检查尺寸，防止二次裁剪导致取景错位。
> 识别区两张空场景缺一不可：少了哪一张，那一路的固定结构就会被当成新物体或贴纸。

### 图片链路

**定版（`full`）**：

```text
相机 1280×720 → 不裁边 → 等比缩到长边 1024（1024×576）→ JPEG 92
```

**旧模式（`square`，已退役）**：

```text
相机 1280×720 → crop_to_square 720×720 → DETECT_LLM_INSET 每边内缩 0.13 → 532×532 → JPEG 92
```

- 1024 正好等于 `LLM_MAX_SIDE`，所以不会被 `qw.py` 再压一次；想上 1280 必须两个都改；
- 前缀参考图单独按 `LLM_REF_MAX_SIDE=512` 缩；
- 空场景参考图与实时画面走同一套预处理（`empty_examples` 里只算一次，便于命中编码缓存）；
- 形状示例图不裁，保持手工准备的样子。

### 固定前缀与显式缓存

识别区请求分两段 `user` 消息：

1. 固定规则 + 词表 + 空识别区图 + 形状示例（段末打一次 `prompt_cache_breakpoint`）。
2. 历史形状参考拼图 + 本轮任务文字 + 当前各机位视角（段末打一次）。

顶层发 `prompt_cache_options={mode:explicit, ttl:30m}`。跑完会打印：

```text
LLM 用量: prompt_tokens=… cached_tokens=… cache_write_tokens=… completion_tokens=… reasoning_tokens=…
LLM 响应: id=… request_id=… service_tier=…
```

参考值（10/22 实测，`full` + 1024）：前缀约 **10320** tokens，其中 `cached_tokens≈7520`（命中率 73%）、
未命中约 **2800**（这部分每轮都重新计费）；单次真实请求 **17 张图**（4 张当前视角 + 13 张前缀参考图，
即 2 张空场景 + 11 张形状示例；形状记忆每多登记 1 个形状再多 N 张视角图）。

### 启动预热（`LLM_WARMUP`）

就绪后（`PushController.run()` 开头）起一个后台 daemon 线程 `llm-warmup`，用与第一轮**完全相同的
前缀**发一次极小请求，结果丢弃。一次请求办三件事：

1. **建连**：DNS + TCP + TLS + 代理隧道，连接留在 keepalive 池里复用；
2. **参考图编码**：前缀里的固定图走 `_ENCODE_CACHE`，正式请求不再重复缩放/编码/base64；
3. **前缀缓存**：显式缓存 `ttl=30m` 提前写入，第一轮真实请求直接 `cached_tokens` 命中。

日志形态：

```text
LLM 预热开始：前缀 2 段、参考图 13 张（… KB）
LLM 用量: prompt_tokens=… cached_tokens=0 cache_write_tokens=… completion_tokens=…
LLM 预热完成，耗时 … 秒（首轮应看到 cached_tokens 命中）
```

边界：

- **只对第一轮有意义**。之后每轮的请求本身就在 20 秒保活窗口内、缓存每轮自动续期，稳态 3~4 秒不变；
- **ttl 是 30 分钟**：开机后闲置超过半小时才开工，缓存已过期，首轮仍走冷路径；
- 线程是 daemon，异常只 `warning`，**不阻塞等 `a`/`c`、不占 `LLM_FALLBACK_TIMEOUT` 的 15 秒**；
- 前缀必须与 `classify()` 字节一致才命中，所以两边共用
  `ShapeRecognizer.classify_prefix_sections()`——改 prompt 时不要只改其中一处；
- 关掉：`.env` 里 `LLM_WARMUP=0`（首轮自己付建连与缓存重建的代价）。

### YOLO fallback

| 场景 | 动作 |
| --- | --- |
| 托盘 `c` 查栏失败 | 调托盘颜色模型，按 5 栏几何 + 传感器方向回五行 `1/0` |
| 识别区分类失败 | 复用本轮各机位照片，累计颜色/形状/贴纸置信度后输出编号 |

当前 `YOLO_FALLBACK=0`，这条路径**不加载也不执行**；置 `1` 才启用（同时会多加载 4 个 RKNN 模型）。

### 形状记忆（仅本次运行）

- 词表外形用 `new` 登记为 `unknown_001`，同一件再出现返回 `remembered`；
- 参考图为该形状的 N 个固定机位视角（默认 4），按 2 列拼图（奇数视角补一格黑），每格 384×384；
- **显示屏名字的优先级**：词表键 → 词表中文名（`SHAPE_DISPLAY_CN`）；词表外（`unknown_00N`）→
  模型在 `new` 时给的六字中文名（`shape_name`，存在 `memory.json` 的 `name` 字段）；
  没有就给 `新形状N`。注意：`shape_name` **只用于显示**，不参与归类、不参与记忆匹配；
**一条判据**：同一轮里“颜色+形状键+贴纸”相同就归同一盒，名字长短不影响分拣。
- 重启新建会话、重新学习；旧图保留供排查。

```text
capture/images/<启动时间>/shape_memory_<会话ID>/
├── memory.json
├── unknown_001_<随机ID>/view_1..N.jpg
└── pending/<随机ID>/{view_1..N.jpg, review.json}   # 未归类现场与原因
```

`uncertain`（证据不足）是合法状态：留档、不登记，本件回 `0`。**`review.json` 是排查的第一现场**，里面有失败原因原文。

### 形状词表与模型类别（别混）

| 常量 | 用途 | 能否加名字 |
| --- | --- | --- |
| `shared.const.SHAPE_CLASSES` | RKNN `shape_detect_area` 的**类别索引**，顺序必须与 `data.yaml` 一致 | ❌ 只有换模型时才跟着改 |
| `shared.const.LLM_SHAPE_EXTRA` / `LLM_SHAPE_CLASSES` | 大模型识别词表（= 模型类别 + 补充） | ✅ 加在这里 |
| `llm.prompt.SHAPE_CN` | 英文键 → 中文名，直接进 prompt | ✅ 必须与 `LLM_SHAPE_CLASSES` 同步 |
| `shared.common.SHAPE_DISPLAY_CN` | 显示屏中文名 | ✅ 同上 |

当前 **20 类** = 模型 16 类 + 补充 4 类：`prism6_thinWalled`（薄壁六棱柱）、
`octahedron` / `dodecahedron` / `icosahedron`（正八/十二/二十面体）。

> **为什么没有 `prism4`（四棱柱）**：正方体 ⊂ 四棱柱 ⊂ 长方体（平行六面体），三者不互斥。
> 单独建一类会让同一件在不同轮次被分到两个盒子，所以 `四棱柱` / `prism4` 只做**同义别名归到 `cuboid`**（见 `llm/prompt.py` 的 `SHAPE_EN`）。
> 正方体与长方体的边界：**`cube` 是默认类**。只有两种“一眼就看得出”的情况判 `cuboid`：
> ③ 底面明显长方形（长边 ≥ 短边的 1.5 倍），或 ④ 里竖直棱与底边明显不等（≥1.5 倍，明显柱状/扁平板）；
> 其余（三个方向差不多、或只是轻微不等）**一律 `cube`**。
> 相机离得近、透视很强，**禁止按像素长度换算比例**，只在同一个面内比相邻两条棱；①② 45° 俯视不能用。
> 棱锥的 3/4 只看 ③ 底面**角数**：3 角 → `pyramid3`，4 角 → `pyramid4`；侧视里棱锥轮廓都是三角形，不能据此判三棱锥。
> `prism4_thinWalled`（薄壁四棱柱）不受影响——开口空腔已经把它和实心方体分开了。

三个正多面体怎么分（prompt 里已写入，实测混淆时对照这张表）：

| 形状 | 面的形状 | 每个顶点汇几条棱 | 一眼特征 |
| --- | --- | --- | --- |
| `octahedron` 正八面体 | 8 个**正三角形** | **4** | 两个四棱锥底对底，上下各一尖，赤道 4 个顶点 |
| `dodecahedron` 正十二面体 | 12 个**正五边形** | **3** | 面大而平，能看出五边形，最容易被误判成球 |
| `icosahedron` 正二十面体 | 20 个**正三角形** | **5** | 接近球但三角面密集，**没有**上下双尖对称 |

共同底线：球在任何视角都是连续圆弧、没有直边；**只要看到直的棱和平面，就不是 `ball`**。

> 把一个名字加进 `SHAPE_CLASSES` 会**错位 YOLO 回落的类别索引**（`yolo/fallback.py` 按下标取名字），
> 所以补充词一律走 `LLM_SHAPE_EXTRA`。`prism6_thinWalled` 以前只在中文表、不在校验词表里，
> 模型输出英文键会被判非法并白白触发一次 3~4 秒的纠错重试。

同义别名走 `llm.prompt.SHAPE_EN`（如 `正四面体`/`tetrahedron` → `pyramid3`、`正六面体`/`hexahedron` → `cube`），
**不新增类别**——同一形状两种叫法会被拆成两个盒子。

加词后必查（不依赖 cv2，PC 上就能跑）：

```bash
python -c "import llm.prompt as p; print(len(p.SHAPE_VOCAB), sorted(set(p.SHAPE_VOCAB)-set(p.SHAPE_CN)))"
python tools/check_examples.py
```

词表不能有包含关系的类别（反例：`prism4` vs `cube` vs `cuboid`）。加词前先问一句：
**这个词是否已经完全被另一个词覆盖？** 是的话就做别名，不要新建类别。

---

## 线上赛段（整盘批量预判）

线下逐件看 4 路；线上只要求“3 分钟内分完 24 件”，时间几乎全花在识别上。
所以线上单独一个入口，**串口/显示/阶段编排完全共用**，只换识别方式：

```bash
sudo systemctl stop wave-vision-llm      # 手动跑，避免和自启抢相机
DISPLAY=:0 python main_online.py
```

1. 收到 `c`（或第一件 `a`）时，用**托盘俯视图**一次识别整盘：
   6 张类别参考图 + 1 张托盘图，**一次请求**；
2. 结果按电控取件顺序排队：**列从右到左，同列从上到下**
   （`lane` 降序 + `y` 升序，**由程序排序**，不依赖模型输出顺序）；
3. 之后每来一个 `a` 直接出队回类别（约 0.1s），**不再调识别区**；
   `v` 到了照常上屏并回 `f`；
4. 队列空 / 批量失败 / 某件没匹配到类别 → **自动回退逐件 4 视角**（只是慢，不会错乱）。

时间账：整盘一次 5~9 秒，之后每件只剩机械节拍。24 件 ≈ 6~9s + 24×节拍。

**盒号按“首次出现顺序”分配**（与线下的 `categorize()` 同语义，不是把参考图序号直接当盒号）：
第一个被取到的类别是 1 号盒，下一个新类别是 2 号盒……超过 6 类的那件回 `0`。
参考图序号只用于让模型区分“哪一类”，不决定盒号。启动后日志会打一行映射，例如：

```text
盒号 = 首次出现顺序：{'橙色正方体': 1, '蓝色球': 2, '红色正方体': 3, ...}
前 6 件（盒号）: [1, 1, 2, 3, 2, 1]
```

### 三个必须满足的前提

| 前提 | 不满足的后果 |
| --- | --- |
| 电控按「右→左列、同列上→下」取件 | **从错位那件开始全错**，而且不报错 |
| 24 件与 `ONLINE_CLASSES` 的 6 类一一对应、彼此差别明显 | 单张俯视图分不清（薄壁/实心、棱长比基本失效） |
| `c` 在货物摆好之后才发 | 早发会按空盘预判，队列为 0，退化成逐件 |

### 改六类参考图

只改 `llm/online_batch.py` 顶部的 `ONLINE_CLASSES`（顺序 = 类别号 1..N，**一个类别可以给多张**图，
用来覆盖同一物体的不同姿态），参考图放 `llm/example/`，用 ASCII 命名。类别数不能超过储物盒上限（6）。

### 怎么确认它真的按顺序

**软件端联调（不接电控）**：摆好货，拍一张，直接看顺序和类别：

```bash
sudo systemctl stop wave-vision-llm
python tools/online_check.py --tag 第一版
```

会打印每件的 `lane / y / 类别 / 名称`、取件顺序、各栏件数、查栏会回的 5 位；
同时把 `annotated.jpg`（栏线 + 序号:类别）存到 `capture/online_check/<时间戳>/`，
可在 PC 上看图核对“它以为的第几件”和“实际取的第几件”是否一致。

正式跑起来时，日志里每件会打一行：

```text
线上批量预判：24 件，各栏 [5, 5, 5, 4, 5]，前 6 件顺序 [3, 1, 2, 6, 4, 5]
线上预判结果: 3 -> 黑色薄壁五棱柱（lane=5 y=0.12，剩余 23 件）
```

`lane` 必须从 5 开始；同一个 `lane` 里 `y` 必须递增。对不上就是电控顺序或摆位和约定不一致。

### 与线下的差异（别忽略）

- 线上模式**不看贴纸**：物体表面的白色贴纸（文字/图案/二维码/条码）一律当干扰，
  类别只由**主体颜色和外形**决定（线下则会把贴纸作为分类信息之一）；
- 线上模式**不检查每盒 4 件**上限；
- 托盘只在 `c` / 首个 `a` 时预判一次，中途补货不会重算，要重跑请重启程序。

---

## 线上版2（单目 3 色 × 3 形）

物体推到识别区、只开 **1 路**相机逐件识别：比版1 稳（取景大、光照稳），比线下快（1 张图、几十 token）。

```bash
sudo systemctl stop wave-vision-llm
DISPLAY=:0 python main_online2.py
```

入口会强制 `ONLINE_MODE=0`、`SINGLE_SHAPE_MODE=1`、`STAGE_TWO_DETECTION_COUNT=1`
（只开 `cam_detect_1`，其余三路不占带宽）。串口/显示/盒号分配与线下完全一致。

| | 线下 | 线上版1 | 线上版2 |
| --- | --- | --- | --- |
| 输入 | 4 视角 | 1 张托盘俯视 | **1 视角** |
| 判别方式 | 词表 + 形状记忆 | 6 张参考图比对 | **3 色 × 3 形固定词表** |
| 单件耗时 | 4~6s | 首个 `c` 5~9s，之后 0.1s | ~1.5~3s |
| 贴纸 | 参与分类 | 忽略 | **忽略** |

### 判别范围（9 类，取其中 6 类）

`red / orange / blue` × `cube / ball / cylinder_thinWalled`

模型只返回这三个颜色与三种形状，校验器会拒绝词表外取值；同时容忍
`cylinder` / `tube` / `sphere` / 中文名 / 大小写与空格等写法（统一归一）。

### 盒号

与另外两个入口一致：**按首次出现顺序**分配 1..6，第 7 个新类别回 `0`。

### 单目自检（不接电控）

```bash
python tools/grid_check.py --tag 红正方体
```

会打印颜色/形状/耗时，并把 `frame.jpg`、`sent.jpg`（真正送模型的那张）存到
`capture/grid_check/<时间戳>/`，方便核对裁剪和取景。

> ⚠️ 用 systemd 起时，`start_vision.sh` 按 `.env` 的 `STAGE_TWO_DETECTION_COUNT`
> 等相机别名；届时要把该值也写成 `1`，否则门禁会等四路。手动跑本入口不受影响。

---

## 部署与运行

### 服务与包装

```bash
cd ~/WaveRider_GongChuang_Vision          # 板端路径
chmod +x deploy/start_vision.sh
sudo cp deploy/wave-vision-llm.service /etc/systemd/system/
sudo cp deploy/wave-hdmi-watch.service /etc/systemd/system/     # 可选
sudo systemctl daemon-reload
sudo systemctl enable --now wave-vision-llm
journalctl -u wave-vision-llm -b --no-pager | tail -30
```

`ExecStart` 走 `deploy/start_vision.sh`，它按顺序做**四道门禁**，全过才启动主程序：

1. 等五个相机别名 `/dev/wave/cam_pallet` + `cam_detect_1..4`（最长 60s）
2. 等板内代理端口 7890（最长 30s）
3. **网络就绪**：`curl` 走代理带 key 真打 `$BASE_URL/models`，拿到 200 才算通（最长 300s，单调时钟计时）
4. 等 X 会话 `:0` 可用（最长 60s）

另外它会把 `tools/net_check.sh` 的结果存到 `/tmp/net_check_last.log`。

> **就绪信号**：屏幕还显示桌面 = 还没就绪；屏幕变纯黑 = 已就绪，可以开始发 `a`/`c`。
> 冷启动后网络可能需要 1~5 分钟才通，这段时间门禁会一直等（这是有意的，别把它当故障）。

### 图形会话（自动登录）

```bash
# /etc/lightdm/lightdm.conf.d/60-autologin.conf
[Seat:*]
autologin-user=orangepi
autologin-user-timeout=0
```

`DISPLAY` 与 `XAUTHORITY` 由 service 注入（`DISPLAY=:0`、`/home/orangepi/.Xauthority`）。

### 代理（必须）

板内 mihomo 走 systemd（`mixed-port 7890`，节点必须**美国**），`.env` 里必须有三行 PROXY；
否则自启后进程没有代理，表现为**每轮 LLM 失败 → 回 0 丢件**。

```bash
curl -s -o /dev/null -w '%{http_code}\n' -m 10 -x http://127.0.0.1:7890 https://api.openai.com/v1/models
# 401 = 通；000/超时 = 先 sudo systemctl restart mihomo 再测
```

### 手动跑（不用 systemd）

**`main_push_llm.py` 启动时会自己读 `.env`**（`shared/dotenv.py`，在 import
`shared.const` / `llm.qw` **之前**执行——那两个模块在 import 期就读环境变量）。
所以直接跑就行，不需要 `set -a`：

```bash
cd ~/WaveRider_GongChuang_Vision
python main_push_llm.py           # 自己加载 .env
# 或者走门禁：  bash deploy/start_vision.sh
```

- **不覆盖已有变量**：systemd 注入的、或命令行 `FOO=1 python …` 传进来的优先。
- 启动日志第二行会打 `.env 载入 N 项`；`N=0` 说明 `.env` 不在项目根目录。
- 老写法 `set -a; . ./.env; set +a` 仍然可用，只是不再必需。
- **历史教训**：这个坑曾让「送图模式」静默退回 `square`、`LLM_FALLBACK_TIMEOUT` 退回 35 秒，
  而且表面一切正常——所以现在改成代码兜底。

两条路径的差别：

| | systemd | 手动 |
| --- | --- | --- |
| 四道门禁 | ✅ 自动等 | ✅ 用 `start_vision.sh` 才有 |
| key / 代理注入 | ✅ `EnvironmentFile` | ⚠️ 要自己 `set -a; . ./.env` |
| 崩溃自动重启 | ✅ | ❌ 挂了就没了 |
| 日志 | `journalctl -u wave-vision-llm` | 只在终端里，建议重定向到文件 |

### 只关开机自启（调试期常用）

调相机、改配置时不想让它开机抢设备，但还想保留「一条命令启停」：

```bash
sudo systemctl disable wave-vision-llm          # 只取消开机自启，不碰当前进程
systemctl is-enabled wave-vision-llm            # 应显示 disabled
sudo systemctl start / stop / restart wave-vision-llm   # 照常手动用
```

### 关闭 / 恢复自启

```bash
sudo systemctl disable --now wave-vision-llm    # 关（disable 管开机，--now 停当前）
sudo systemctl enable  --now wave-vision-llm    # 恢复
systemctl is-enabled wave-vision-llm; pgrep -af main_push_llm
# 彻底不要了：sudo rm /etc/systemd/system/wave-vision-llm.service && sudo systemctl daemon-reload
```

### 更新板端代码

**现状：板端代码是 scp 上去的**（板上没有 git 仓库，也**不要**在板上 `git init`）。在 PC 上改完、提交后，
把改动的文件 scp 过去再重启服务：

```bash
scp -r shared llm yolo models tools deploy main_push_llm.py test_llm.py requirements.txt \
    orangepi@192.168.43.5:~/WaveRider_GongChuang_Vision/
ssh orangepi@192.168.43.5 'sudo systemctl restart wave-vision-llm'
```

**板上不要改代码**，改动一律在 PC 上做。`.env` 与 `capture/`、`llm_responses/` 都不在同步清单里，
板端那份是独立的，别覆盖。等板上换成 git 之后，这一节可以简化成
`git pull --ff-only && sudo systemctl restart wave-vision-llm`。

---

## 排查手册

### 自启动体检（一条命令组）

```bash
systemctl is-enabled wave-vision-llm mihomo lightdm
uptime -s
journalctl -u wave-vision-llm -b --no-pager | grep -E '就绪|网络就绪|预热完成|启动主程序|待机中|display stopped|ERROR'
pgrep -af 'main_push_llm|start_vision'
systemctl --failed --no-pager
```

判读要点：`启动主程序` 的时间应该紧贴开机时刻；`pgrep` 只能有一行；`systemctl --failed` 里
`dnsmasq` 失败是无害的（端口 53 被占，与视觉链路无关）。

### 相机

```bash
ls -l /dev/wave/                                          # 五个别名在不在、指向哪个 videoN
v4l2-ctl --list-devices                                   # 相机枚举（注意别把 video-dec0/enc0 当相机）
lsusb -t                                                  # 几条总线、相机分别挂在哪
sudo dmesg | tail -40 | grep -iE 'uvc|bandwidth|ENOSPC|reset'
python tools/probe_cameras.py                             # 四路各 90 帧的冒烟测试
```

已知坑：

- **只有 2 路能开、另 2 路 `Failed to submit URB 0 (-28)`** → 四路挤到同一条 480M 总线了（等时带宽不足）。
  把线拆到两条总线（见「带宽」一节），或换带独立供电的 USB3 Hub。
- **`/dev/wave/` 不存在或别名残缺** → udev 规则没装 / 没 reload / `ID_PATH` 变了。注意
  `ID_PATH==""` 写法会被 udev 静默丢弃，用 `sudo udevadm test` 自查。
- **相机反复掉线（`dmesg` 里 `USB disconnect` + `URB (-19)` / `-71`）** → **不是带宽**（带宽是 `-28`）。
  `-19`=ENODEV 设备消失，`-71`=EPROTO 信号完整性差：优先查线材（短、粗、带屏蔽）、接触面、
  Hub 供电，以及电机动作时是否同时掉（EMI）。现在程序会自动重连（约 9.5s），不整场重启；
  但日志里反复出现 `开始重连` 就说明硬件必须处理，重连只能兜住偶发。
- **`摄像头打开失败` 而且 `pgrep` 有两个实例** → 手动跑的和服务在抢设备，先 `sudo systemctl stop wave-vision-llm`。
- **预览打不开相机** → 同上，主程序占着；调试相机必须先停服务。
- `/dev/video-dec0`、`/dev/video-enc0` 是 RK3588 的硬编解码节点，**不是相机**，写脚本别用 `/dev/video*` 裸通配。

### 网络（分层判定）

```bash
ip -br addr; ip route                                     # 1 有 IP 与默认路由
ping -c1 -W2 "$(ip route | awk '/^default/{print $3;exit}')"  # 2 网关
ping -c1 -W2 223.5.5.5                                    # 3 上游
getent hosts www.baidu.com                                # 4 DNS
curl -s -o /dev/null -w '%{http_code}\n' -m 10 -x http://127.0.0.1:7890 https://api.openai.com/v1/models   # 5 代理+key
journalctl -u mihomo -n 40 --no-pager | grep 'dns resolve failed'   # 6 节点域名解析
```

已知坑：

- **第 6 项报 `dns resolve failed: couldn't find ip`** → 节点那个 `.qpon` 域名解析不了。
  **实测不要给链路强指公共 DNS**：手机热点的 DNS、`223.5.5.5`、`119.29.29.29` 都解不出该域名，
  **只有网卡那侧运营商下发的 DNS（`192.168.0.1`）能解**。所以保持链路自动获取 DNS
  （`ipv4.ignore-auto-dns no`、`ipv4.dns ""`，已回滚掉早期强指公共 DNS 的做法），
  修法是确认主路（USB 网卡）在，再 `sudo systemctl restart mihomo`。
- **冷启动后 1~5 分钟才通** → mihomo 起得比外网早，第一次拉订阅失败。可用 systemd drop-in 让 mihomo 等外网后再启动。
- `Request timed out.` ≠ `Connection error.`：前者是隧道卡住（DNS/节点），后者是隧道建不起来（代理没起/节点死）。
- 主路是 USB 网卡（metric 5）、热点是备路且**是唯一 SSH 入口**，两个都不能随意断开。

### 显示

- **整屏玫红 / 全黑**：rockchip GL 驱动问题，`shared/show.py` 已强制 `SDL_VIDEODRIVER=x11` + 软件渲染。
- **画面糊、分辨率掉到 800×480**：屏重新握手时 X 读不到 EDID，退到内置兜底模式。`tools/hdmi_watch.py`
  看门狗会盯 EDID/模式并自动重同步（`deploy/wave-hdmi-watch.service`）。
- **屏一直全黑**：先确认是不是**待机黑屏**（`SCREEN_OFF_UNTIL_START=1`，等首个 `a`/`c` 才画表格）；
  日志里应有「显示屏待机中（纯黑）」。

### 常见故障对照

| 现象 | 病因 | 处理 |
| --- | --- | --- |
| 每轮都 `识别超过 15 秒，已放弃` | 网络/代理不通 | 见上面网络分层 |
| `摄像头打开失败` 且有两个实例 | 手动跑的和服务抢设备 | `sudo systemctl stop wave-vision-llm` 后确认 `pgrep` 为空 |
| 启动日志「相机别名不存在」 | udev 规则没生效 / 换了 USB 口 | `ls -l /dev/wave/`；按新 `ID_PATH` 改 `70-wave-cameras.rules` 后 reload + trigger |
| 某些相机 0 帧、dmesg 有 `URB 0 (-28)` | 四路挤在同一条 480M 总线 | 拆到两条总线（见「带宽」） |
| `缺少形状示例照片` / `空场景参考照片` | `llm/example/` 文件不齐 | 补齐后重启（缺哪个日志会写全路径） |
| `Recognition display stopped:` | 显示线程当场死了 | 查 X 会话与 `xrandr` |
| 多次识别同件结果不同 | 采图信息不足或 prompt 边界 | 看 `pending/*/review.json` 的原因原文 |
| 日志时间戳错乱 | 板子无 RTC，开机头半分钟时钟是上次关机时间 | `timedatectl set-ntp true` |

---

## 工具脚本（tools/）

| 脚本 | 用途 |
| --- | --- |
| `offline_debug.py` | **离线调参主力**：四路拍照 + 现有 prompt + 计时/用量记录（见「视觉调参」） |
| `online_check.py` | **线上版1 联调**：拍一张托盘图 → 整盘批量识别 → 打印顺序/类别，并保存标注图 |
| `grid_check.py` | **线上版2 联调**：拍一张识别区图 → 判断颜色+形状（3×3），保存原图/送图/结果 |
| `probe_cameras.py` | **相机冒烟测试**：四路同时开 + 并行连读 + 各存一张 jpg（走别名；`--devices` 可显式指定） |
| `preview_camera.py` | **相机预览 + 几何叠加**（调机位用）；`--camera pallet/detect_up1/detect_up2/detect_down/detect_side`，`--http 8080` 可 PC 浏览器看 MJPEG，`--tray` 画裁剪框（跟随 `DETECT_CROP_MODE`） |
| `capture.py` | 抽帧 / 录像；`CAMERA_TYPE` 可设 `pallet`/`detect_up1`/`detect_up2`/`detect_down`（改文件顶部那一行） |
| `set_empty_reference.py` | 替换空场景参考图（校验必须是未裁剪原图） |
| `check_examples.py` | 校验示例图：漏登记 / 词表 / 孤儿图（纯 ast，PC 可跑） |
| `net_check.sh` | 启动前网络自检（出口 IP/国家、代理端口、key、模型可调用），退出码 = 不合格项数 |
| `probe_openai_api.py` | 接口兼容性探针：`service_tier`、`reasoning`、缓存字段、真实请求块形状 |
| `probe_model_classes.py` | 比对 `.rknn` 真实类别数与代码列表 |
| `hdmi_watch.py` | HDMI/DP EDID 与模式看门狗（`--once` 体检、`--force-mode` 钉模式） |
| `hdmi_health.py` | HDMI 链路体检，`--fix` 触发重协商 |
| `probe_display.py` | 显示层体检（xrandr / EDID / 字体） |
| `summarize_log.py` | 日志汇总（`python tools/summarize_log.py <日志> [报告.md] [明细.csv]`） |
| `dump_round_status.py` | 每轮 status/color/shape/reasoning 一览 |
| `timing_breakdown.py` | 按时间戳拆整轮各段耗时 |
| `crop_preview.py` | 按四边比例裁剪预览（`--use-const` 直接读 const.py） |
| `ab_reasoning.py` / `tier_ab.sh` / `probe_images.py` | 耗时与档位 A/B 对比（调优用） |

### 常用：相机

```bash
python tools/probe_cameras.py                                  # 走别名，验四路带宽
python tools/preview_camera.py --camera detect_up1 --http 8080 --tray
# 只想单独测某几路：--devices 要 4 个参数，拿不存在的节点占位即可（占位会被跳过）
python tools/probe_cameras.py --devices /dev/video0 /dev/video4 /dev/none /dev/none
```

### 离线测试（PC，无需硬件）

```bash
python test_llm.py classify <图1> <图2> <图3> <图4>   # 四路分类（个数 = STAGE_TWO_DETECTION_COUNT）
python test_llm.py pallet   <托盘俯视图>            # 查栏
python tools/check_examples.py                      # 示例图自检
```

---

## 待办 / 已知遗留

- **例图覆盖不全**：词表已有 20 类，示例只覆盖 `cube` / `prism4_thinWalled` / `prism5_thinWalled` / `prism6`，
  其余（球、圆锥、圆柱、半球、正多面体…）都没有示例。新增的薄壁六棱柱/正八·十二·二十面体
  属于“有名字无示例”，模型只能靠 prompt 描述判断，最容易两可——出现实物时优先补这 4 类的例图。
- **缺“开口朝下”的例子**：三组薄壁件的开口都朝上（①② 能看到内腔，③ 看到封闭底面）。
  如果能补一张“薄壁件倒过来放”的 ③ 视角，模型就能学到“内腔也可以出现在底部视角”。
- **例图目前是临时俯拍/仰拍**，建议用 ①②③④ 四台真机位各拍一张，取景与线上完全一致。
- **④ 平视还没有例图和空场景**：`llm/example/` 里没有平视示例；`empty_side.jpg` 也可以补一张（可选，缺了只是少一张背景参考）。
- **空盘轮多跑一次请求**：模型给 `label_kind: "none"`，而校验器要求 `null` → 触发纠错重试，
  每次空盘多花 3.6~4.2 秒。修法一行：`status=="empty"` 时把 `"none"`/`"null"`/`None` 都当合法（尚未改）。
- **矮框（推料夹具）的 prompt 规则待补**：需要先有「带框的空场景图」`empty_top/bottom`，再改判据；
  现在拷图补上去容易把夹具当成物件。
- **`MAX_ITEMS_PER_CATEGORY = 16`** 与赛规「每盒不超过 4 件」不一致，待确认后修改。
- `llm/pallet_monitor.py` 全项目零引用（早期 agent 方案残留），可直接删。
- `shared/common.py` 的 `FrameStream` 类无人实例化（死代码），清理需动 `read_camera` 的错误分支，暂缓。
- 赛后轮换两把 key：本项目的 LLM key（曾在聊天/`journalctl` 露过明文）、队友的 VolcEngine Ark key
  （团队仓库历史里有）。
