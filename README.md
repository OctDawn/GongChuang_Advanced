# Orange Pi 视觉分拣程序（LLM 版）

在 Orange Pi 5 Pro（RK3588S，Ubuntu 22.04）上运行视觉识别，通过串口与电控通信，由显示屏呈现分拣结果。

- **当前唯一入口**：`main_push_llm.py`（视觉大模型识别，本地 NPU 仅作可选回落）
- **启动方式**：`bash deploy/start_vision.sh`（带四道就绪门禁），或手动 `python main_push_llm.py`
- **开机自启**：`deploy/wave-vision-llm.service`（systemd）

> 历史方案（**已归档，不再维护**）：`main_push_yolo.py`（纯 YOLO）、`main_laser_llm.py` + `shared/laser.py`（视觉替换激光传感器）、`main_grab.py`（早期抓取）、`agent/`（自主决策）。
> 其中 `yolo/` 目录**仍然保留**：LLM 版的 YOLO fallback、贴纸解码、模型类别数防呆都在用。

---

## 更新记录

> 规则：每条不超过 20 字，写清「谁改的、改了什么」，倒序排列。

**10/16 hrx**
- 识别区改三路固定机位，取消 r/b 握手
- 相机改 udev 物理口别名 cam_*
- 四路相机拆两条总线解决带宽

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
├── main_push_llm.py             # 唯一入口：串口协议 + 阶段编排
├── test_llm.py                  # 离线测试（PC 上喂图片，不碰硬件）
├── requirements.txt
├── .env.example / .env          # .env 已 gitignore，含 key 与代理三行
├── llm/                         # 大模型层
│   ├── qw.py                    # OpenAI 客户端：缓存模式、重试、图片编码
│   ├── prompt.py                # 词表、提示词、纠错提示
│   ├── shape_recognition.py     # 三机位识别、校验、形状登记
│   ├── shape_memory.py          # 本次运行的形状参考图库（3 视角拼图）
│   ├── empty_examples.py        # 空场景图与带标签示例加载
│   └── example/                 # 2 张空场景 + 7 张形状示例
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
└── deploy/                      # systemd 服务与启动包装
```

按文件名运行，例如 `python main_push_llm.py`。所有相对路径（`capture/`、`models/`）都以项目根目录为基准。

---

## 当前流程

四路相机推送版（托盘广角 1 + 识别区三路固定机位 3），视觉只做识别与显示，机械/电控负责动作。

### 串口协议

| 方向 | 指令 | 含义 |
| --- | --- | --- |
| 电控 → 视觉 | `c` | 查栏：识别托盘各栏占用 |
| 电控 → 视觉 | `a` | 开始下一轮（直接进入阶段 2） |
| 电控 → 视觉 | `v` | 请求显示 |
| 视觉 → 电控 | `1/0 ×5` | 查栏结果，每栏一位、带换行 |
| 视觉 → 电控 | `0` / `1~6` | 分类编号（0 = 无物体 / 失败 / 类别或盒已满） |
| 视觉 → 电控 | `f` | 显示完成回执 |

> `b`（转向完成）/ `r`（要求转向）是机械改造前的握手，**现已废弃**：识别区改为三路固定机位、不再旋转，收到 `b` 只忽略，不再等待。

### 阶段编排

1. 程序持续读取托盘广角与**识别区三路固定机位**（① ② 斜上方相对、③ 正下方仰视），各自只保留最新一帧；托盘与识别区① 另外录像。
2. **阶段 1**：同时监听 `c` 与 `a`。收到 `c` → 识别托盘各栏，回复 5 位 `1/0`；收到 `a` → 直接进入阶段 2。
3. **阶段 2**：`a` 一到，从三路机位各取一张「`a` 之后」的最新帧（先丢积压旧帧再取新帧，三路同时拍、误差约 1 帧 ≈ 33ms），**全程没有 `r`/`b` 握手**（`STAGE_TWO_DETECTION_COUNT=3`）。
4. 三张图一次性交给大模型，得到分类编号后回 `0` 或 `1~6`。
5. 电控动作完成发 `v` → 视觉先上屏，再回 `f`。
6. 回到第 2 步，直到所有有物体的栏都推到底。

> 兜底：LLM 超时或失败时回 `0`（不回落 YOLO，见下文 `YOLO_FALLBACK`）。查栏失败则保守回全 `1`。

### 线程与故障处理

- **四路摄像头线程**（托盘 1 + 识别区 3）：`maxsize=1` 队列只留最新帧；`get_frame_retry` 连续 5 次取不到（或全是废帧）才判故障。
- **串口线程**：独立收发，读写异常置位 `stop_event`。
- **显示线程**：固定 7 行（6 类 + 末行「未归类」）；上电只铺纯黑，收到首个 `a`/`c` 才点亮。
- **主线程**：阶段编排。任一上游故障 → 抛 `CameraStopped` → 清理退出；所有等待都带超时，不会永久阻塞。

---

## 相机与设备别名（udev）

四路相机全部走 `/dev/wave/cam_*`，别名由 `/etc/udev/rules.d/70-wave-cameras.rules` 按
**USB 物理口**绑定，代码里不再出现 `/dev/videoN`。

| 别名 | 机位 | 物理口（`ID_PATH`） |
| --- | --- | --- |
| `cam_pallet` | 托盘上方广角（1280×960） | `platform-fc800000.usb-usb-0:1.2:1.0` |
| `cam_detect_1` | 识别区① 斜上方（俯约 45°） | `platform-fc800000.usb-usb-0:1.1:1.0` |
| `cam_detect_2` | 识别区② 斜上方（与①相对） | `platform-fc880000.usb-usb-0:1.3:1.0` |
| `cam_detect_3` | 识别区③ 正下方仰视（看底面） | `platform-fc880000.usb-usb-0:1.4:1.0` |

规则要点（都是踩过的坑）：

- **不用 `by-id`**：两台相机 `0bda:9143` 的假序列号都是 `200901010001`，`by-id` 名字直接撞车，
  其中一台干脆没有 `by-id` 名字。
- **属性必须写成 `ENV{ID_PATH}`**：写成 `ID_PATH=="…"` 会被 udev 判成 `Invalid key`，
  **静默丢弃整条规则**（不报错，只是别名一直不出现）。
- **必须加 `ATTR{index}=="0"`**：每台 UVC 相机同时暴露 `index0`（采集）与 `index1`（metadata），
  两者 `ID_PATH` 相同；不加判断别名会被后一个覆盖，指过去打不开。

### 带宽：四路是 2+2 分的，别再插回同一个 Hub

四路相机分在**两条独立总线**上（`platform-fc800000.usb` 与 `platform-fc880000.usb`），
每条（480M）只放得下 **2 路** MJPG 流。第 3 路 `STREAMON` 会直接失败，dmesg 报
`Failed to submit URB 0 (-28)`（`-28` = `ENOSPC` = 等时带宽不足，**不是供电问题**）。

### 换 USB 口 / 换相机后怎么做

```bash
# 1) 看现在谁在哪条总线、每个节点的物理口
for v in /dev/video*; do echo "== $v"; readlink -f /sys/class/video4linux/$(basename $v)/device; done
# 2) 拿到 ID_PATH（udev 规则就是按它匹配的）
for v in 0 2 4 6; do printf "video%s  " $v; udevadm info -q property -n /dev/video$v | grep '^ID_PATH='; done
# 3) 按新 ID_PATH 改 /etc/udev/rules.d/70-wave-cameras.rules，然后
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux --action=add
# 4) 验收：四个别名要指向真实采集节点（index0）
ls -l /dev/wave/
python tools/probe_cameras.py     # 走别名，四路 90/90 才算过
```

> ⚠️ 图号 ↔ 机位是**写死在 prompt 里的契约**（图1=①斜上、图2=②斜上、图3=正下仰视）。
> 换相机请把它装到目标机位、插到该机位别名对应的 USB 口，**别随手对调插头**。
> 拔插不需要 reload 规则（规则没变），只有改了规则文件内容才要 reload。

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
| `LLM_MAX_SIDE` | `1024` | 图片最长边（对当前 720 图是空转） |
| `LLM_REF_MAX_SIDE` | `512` | **只作用于前缀固定参考图**，压体积收益最大 |
| `LLM_REASONING_EFFORT` | `low` | 砍生成阶段长尾；`off` = 用服务端默认 |
| `LLM_KEEPALIVE_EXPIRY` | `20` | 连接空闲保活秒数；太长会复用僵尸连接 |
| `LLM_TIMEOUT` | `60` | 单次请求超时（实际受 `LLM_FALLBACK_TIMEOUT` 约束） |
| `LLM_STICKER_DECODE` | `0` | 贴纸解码总开关，`0` 时 label 只取 LLM 给的 kind/描述 |
| `SCREEN_OFF_UNTIL_START` | `1` | 上电只铺纯黑，收到首个 `a`/`c` 才画表格 |
| `SCREEN_DPMS` | `0` | `1` 时用 `xset dpms force off/on` 真灭背光（需 x11-xserver-utils） |
| `HTTPS_PROXY` / `HTTP_PROXY` | `http://127.0.0.1:7890` | **必须写进 `.env`**：systemd 不读 `.bashrc` |
| `NO_PROXY` | `localhost,127.0.0.1,…` | 别把代理自己排除掉 |

改完重启生效：`sudo systemctl restart wave-vision-llm`（**不需要** daemon-reload）。

### 图片链路

```text
相机 1280×720 → crop_to_square 720×720 → DETECT_LLM_INSET 每边内缩 0.13 → 532×532 → JPEG 92
```

- 前缀参考图单独按 `LLM_REF_MAX_SIDE=512` 缩；
- 空场景参考图与实时画面走同一套裁剪（`empty_examples` 里只裁一次，便于命中编码缓存）；
- 形状示例图不裁，保持手工准备的样子。

### 固定前缀与显式缓存

识别区请求分两段 `user` 消息：

1. 固定规则 + 词表 + 空识别区图 + 形状示例（段末打一次 `prompt_cache_breakpoint`）。
2. 历史形状参考拼图 + 本轮任务文字 + 当前三视角（段末打一次）。

顶层发 `prompt_cache_options={mode:explicit, ttl:30m}`。跑完会打印：

```text
LLM 用量: prompt_tokens=… cached_tokens=… cache_write_tokens=… completion_tokens=… reasoning_tokens=…
LLM 响应: id=… request_id=… service_tier=…
```

参考值：前缀约 7900 tokens，`cached_tokens≈6857`（命中率 86%），单次真实载荷 11 张图约 434 KB。

### YOLO fallback

| 场景 | 动作 |
| --- | --- |
| 托盘 `c` 查栏失败 | 调托盘颜色模型，按 5 栏几何 + 传感器方向回五行 `1/0` |
| 识别区分类失败 | 复用本轮三张照片，累计颜色/形状/贴纸置信度后输出编号 |

当前 `YOLO_FALLBACK=0`，这条路径**不加载也不执行**；置 `1` 才启用（同时会多加载 4 个 RKNN 模型）。

### 形状记忆（仅本次运行）

- 词表外形用 `new` 登记为 `unknown_001`，同一件再出现返回 `remembered`；
- 参考图为该形状的 3 个视角，按 2 列拼图（奇数视角补一格黑），每格 384×384；
- 重启新建会话、重新学习；旧图保留供排查。

```text
capture/images/<启动时间>/shape_memory_<会话ID>/
├── memory.json
├── unknown_001_<随机ID>/view_1..3.jpg
└── pending/<随机ID>/{view_1..3.jpg, review.json}   # 未归类现场与原因
```

`uncertain`（证据不足）是合法状态：留档、不登记，本件回 `0`。**`review.json` 是排查的第一现场**，里面有失败原因原文。

---

## 部署与开机自启

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

1. 等四路相机别名 `/dev/wave/cam_pallet` + `cam_detect_1/2/3`（最长 60s）
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

`display` 与 `XAUTHORITY` 由 service 注入（`DISPLAY=:0`、`/home/orangepi/.Xauthority`）。

### 代理（必须）

板内 mihomo 走 systemd（`mixed-port 7890`，节点必须**美国**），`.env` 里必须有三行 PROXY；否则自启后进程没有代理，表现为**每轮 LLM 失败 → 回 0 丢件**。

```bash
curl -s -o /dev/null -w '%{http_code}\n' -m 10 -x http://127.0.0.1:7890 https://api.openai.com/v1/models
# 401 = 通；000/超时 = 先 sudo systemctl restart mihomo 再测
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

### 关闭 / 恢复自启

```bash
sudo systemctl disable --now wave-vision-llm    # 关（disable 管开机，--now 停当前）
sudo systemctl enable  --now wave-vision-llm    # 恢复
systemctl is-enabled wave-vision-llm; pgrep -af main_push_llm
```

---

## 排查手册

### 自启动体检（一条命令组）

```bash
systemctl is-enabled wave-vision-llm mihomo lightdm
uptime -s
journalctl -u wave-vision-llm -b --no-pager | grep -E '就绪|网络就绪|启动主程序|待机中|display stopped|ERROR'
pgrep -af 'main_push_llm|start_vision'
systemctl --failed --no-pager
```

判读要点：`启动主程序` 的时间应该紧贴开机时刻；`pgrep` 只能有一行；`systemctl --failed` 里 `dnsmasq` 失败是无害的（端口 53 被占，与视觉链路无关）。

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

### 显示

- **整屏玫红 / 全黑**：rockchip GL 驱动问题，`shared/show.py` 已强制 `SDL_VIDEODRIVER=x11` + 软件渲染。
- **画面糊、分辨率掉到 800×480**：屏重新握手时 X 读不到 EDID，退到内置兜底模式。`tools/hdmi_watch.py` 看门狗会盯 EDID/模式并自动重同步（`deploy/wave-hdmi-watch.service`）。
- **屏一直全黑**：先确认是不是**待机黑屏**（`SCREEN_OFF_UNTIL_START=1`，等首个 `a`/`c` 才画表格）；日志里应有「显示屏待机中（纯黑）」。

### 常见故障对照

| 现象 | 病因 | 处理 |
| --- | --- | --- |
| 每轮都 `识别超过 15 秒，已放弃` | 网络/代理不通 | 见上面网络分层 |
| `摄像头打开失败` 且有两个实例 | 手动跑的和服务抢设备 | `sudo systemctl stop wave-vision-llm` 后确认 `pgrep` 为空 |
| `缺少形状示例照片` / `空场景参考照片` | `llm/example/` 文件不齐 | 补齐后重启（缺哪个日志会写全路径） |
| `Recognition display stopped:` | 显示线程当场死了 | 查 X 会话与 `xrandr` |
| 启动日志「相机别名不存在」 | udev 规则没生效 / 换了 USB 口 | `ls -l /dev/wave/`；按新 `ID_PATH` 改 `70-wave-cameras.rules` 后 reload + trigger（见「相机与设备别名」） |
| 多次识别同件结果不同 | 采图信息不足或 prompt 边界 | 看 `pending/*/review.json` 的原因原文 |
| 日志时间戳错乱 | 板子无 RTC，开机头半分钟时钟是上次关机时间 | `timedatectl set-ntp true` |

---

## 工具脚本（tools/）

| 脚本 | 用途 |
| --- | --- |
| `net_check.sh` | 启动前网络自检（出口 IP/国家、代理端口、key、模型可调用），退出码 = 不合格项数 |
| `probe_openai_api.py` | 接口兼容性探针：`service_tier`、`reasoning`、缓存字段、真实请求块形状 |
| `probe_model_classes.py` | 比对 `.rknn` 真实类别数与代码列表 |
| `hdmi_watch.py` | HDMI/DP EDID 与模式看门狗（`--once` 体检、`--force-mode` 钉模式） |
| `hdmi_health.py` | HDMI 链路体检，`--fix` 触发重协商 |
| `probe_display.py` | 显示层体检（xrandr / EDID / 字体） |
| `summarize_log.py` | 日志汇总（`python tools/summarize_log.py <日志> [报告.md] [明细.csv]`） |
| `dump_round_status.py` | 每轮 status/color/shape/reasoning 一览 |
| `timing_breakdown.py` | 按时间戳拆整轮各段耗时 |
| `check_examples.py` | 校验示例图：漏登记 / 词表 / 孤儿图（纯 ast，PC 可跑） |
| `set_empty_reference.py` | 替换空场景参考图（校验必须是未裁剪原图） |
| `preview_camera.py` | 摄像头预览（`--camera pallet/detect_up1/detect_up2/detect_down`，`--http 8080` 可在 PC 浏览器看 MJPEG） |
| `capture.py` | 抽帧 / 录像 |
| `crop_preview.py` | 按四边比例裁剪预览（`--use-const` 直接读 const.py） |
| `ab_reasoning.py` / `tier_ab.sh` / `probe_images.py` | 耗时与档位 A/B 对比（调优用） |

### 离线测试（PC，无需硬件）

```bash
python test_llm.py classify <图1> <图2> <图3>      # 三视角分类
python test_llm.py pallet   <托盘俯视图>            # 查栏
python tools/check_examples.py                      # 示例图自检
```
