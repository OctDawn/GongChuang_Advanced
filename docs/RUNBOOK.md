# 现场联调与赛前检查手册

更新时间：2026-11-25
适用入口：`main_push_llm.py`（systemd 单元 `wave-vision-llm`）

---

## 一、和电控联调

### 1.1 硬件与链路

| 项目 | 值 | 备注 |
|---|---|---|
| 串口设备 | `/dev/ttyS0` | `shared/const.py: SERIAL_PORT` |
| 波特率 | **115200**，8N1 | `BAUDRATE` |
| 电平 | 3.3V TTL | 双方必须共地；TX/RX 交叉 |
| 指令格式 | 单字符 ASCII + `\n` | 例如 `a\n` |
| 回包格式 | 单字符 ASCII + `\n` | 例如 `3\n`、`0\n` |

串口打不开的日志是 `打开串口失败`，随后 `stop_event` 置位，整场会退出并由 systemd 拉起——
所以联调第一步就是确认串口能开。

### 1.2 协议（以视觉板为接收方）

| 电控发 | 视觉动作 | 视觉回 | 时间预算 |
|---|---|---|---|
| `c` | 托盘五栏查栏（一次 LLM 调用） | 5 行 `1\n` / `0\n`，从左到右 | 12s（失败保守回全 1） |
| `a` | 识别区 5 视角拍摄 + 分类 | `1\n`~`6\n` 或 `0\n` | 12s（LLM 上限）+ 拍照 0.3s |
| `v` | 把上一件画进表格、累加总数 | 画完后回 `f\n` | 立即 |
| `b` | **已废弃**（识别区不再旋转） | 无 | 只记日志 |

要点：

- `a` 的回包就是分拣结果，电控必须等它；**超 15 秒没动作本轮结束**，所以 12 秒的 LLM 上限是硬约束。
- `v` 之前屏幕不会新增行；`f` 是“本轮结束”的确认，由视觉在画完表后发出。
- 同一盒第 5 件回 `0`（`MAX_ITEMS_PER_CATEGORY=4`），属正常，不要当故障。
- `c` 失败时视觉故意回全 1（宁可多推，不能漏推），电控要能接受这种保守值。
- 上电后屏幕是**纯黑**的，收到第一个 `a`/`c` 才亮。

### 1.3 联调步骤（按顺序做，每步看日志）

```bash
# 0) 服务在跑
systemctl is-active wave-vision-llm
journalctl -u wave-vision-llm -f | grep -E "接收|发送|阶段 2|查栏|托盘栏"
```

1. **通链路**：电控发 `c`，日志出现 `接收: c`，回 5 行；
   空托盘时应是 `00000`。
2. **查栏灵敏度**：托盘放 1 件（放中间栏），发 `c`，回包对应位为 1。
   左右方向不对就改 `shared/const.py: LASER_SENSOR_ON_LEFT`（默认 True，含义是激光传感器在左侧），
   改完重启服务。
3. **单件全流程**：放 1 件到识别区 → 发 `a` → 看到 `阶段 2 结果: color=… shape=… category=N`，
   电控收到 `N`；再发 `v` → 屏幕出现一行 → 收到 `f`。
4. **六类各一件**：确认回包是 1~6，且屏幕上序号/名称/图片/总数都对。
5. **满盒**：同一个盒子塞第 5 件 → 回 `0`，屏幕不新增，流程继续下一件。
6. **异常演练**：把网线拔掉（或停代理）再发 `a` → 12 秒后回 `0`，**流程不能卡死**，
   插回网线后下一件正常。
7. **重启恢复**：`sudo systemctl restart wave-vision-llm`，等预热完成（日志 `LLM 预热完成`），
   电控发 `a` 应正常，不需要重启电控。

### 1.4 需要和电控当面确认的 5 个点

1. 回包读取方式：是逐行读，还是读到换行为止？会不会把 `f` 当成下一件的回包？
2. 收到 `0` 时的动作：回托盘还是原地不动？不能卡住等下一件。
3. `v` 的时机：是“放到盒子上方”就发，还是“确认落盒”才发？视觉只认收到 `v` 才记账。
4. 串口参数：115200 / 8N1 / 3.3V / 共地，是否完全一致。
5. 推铲与回包是否互锁：视觉回包早于推铲动作时，电控会不会重复触发。

---

## 二、比赛开始前检查

### 2.1 开机（提前 10 分钟）

顺序：电控上电 → 视觉板上电 → 显示器上电。

```bash
systemctl is-enabled wave-vision-llm   # 必须 enabled（自启）
systemctl is-active wave-vision-llm    # active
journalctl -u wave-vision-llm -b --no-pager | grep -E "模型自检|预热完成|初始化完成"
```

三条必看：

- `模型自检通过: gpt-6.2-astra（HTTP=200）`
  （失败会打印 `切到备份模型: qwen3-vl-plus`，此时精度会下降，要考虑修代理）
- `LLM 预热完成，耗时 x 秒`，且 `cached_tokens` 接近 `prompt_tokens`（缓存命中）
- `cam_pallet` + `cam_detect_1..5` 六路全部 `初始化完成`

### 2.2 配置抽查

```bash
cd ~/WaveRider_GongChuang_Vision
grep -E "DASHSCOPE_MODEL|LLM_SERVICE_TIER|LLM_STICKER_ENABLED|YOLO_FALLBACK|LLM_FALLBACK_TIMEOUT|STAGE_TWO" .env
```

期望值：

```
DASHSCOPE_MODEL=gpt-6.2-astra
LLM_SERVICE_TIER=ultrafast
LLM_STICKER_ENABLED=0
YOLO_FALLBACK=1
LLM_FALLBACK_TIMEOUT=12
STAGE_TWO_DETECTION_COUNT=5
DETECT_MIRROR_VIEW_INDEX=5
```

### 2.3 光路与画面

```bash
python tools/preview_camera.py            # 逐路看画面
python tools/pallet_lanes.py              # 托盘五栏网格是否对齐
bash tools/venue_check.sh                 # 一键体检，退出码 = 失败项数
```

- 5 路画面都清晰、不糊、不过曝；⑤ 镜子干净、位置没被碰偏。
- 透明亚克力板无划痕遮挡，背光正常。
- 托盘五栏网格线（黄）与实际栏位对齐，送图裁剪框（红）框住货物。

### 2.4 显示

```bash
python tools/render_table_offline.py --size 1920x1080   # 离屏渲染检查
```

- HDMI 已出画面，分辨率与现场屏一致。
- 表格列：序号 / 货物名称 / 图片 / 分拣总数量。
- 上电是纯黑属正常，收到 `a`/`c` 才亮。

### 2.5 赛前演练（必做一遍）

1. 空台 `c` → `00000`。
2. 六类各一件走完 `a`/`v`，记录**每件耗时**（目标 < 10 秒）。
3. 同盒第 5 件 → 回 `0` 不卡。
4. 断网 30 秒 → 回 `0` 不卡，恢复后正常。
5. 重启服务 → 2 分钟内恢复可用。

### 2.6 应急开关

| 情况 | 动作 |
|---|---|
| GPT 不可用 | 无需操作，启动自检自动切千问；看日志确认 |
| 想彻底不用 YOLO | `.env` 改 `YOLO_FALLBACK=0` 后重启 |
| 想恢复贴纸识别 | `.env` 改 `LLM_STICKER_ENABLED=1` 后重启 |
| 想回滚提示词 | `git checkout prompt-before-thinwall-20261125 -- llm/prompt.py` |
| 想回滚示例图 | `git checkout 81aeb75 -- llm/empty_examples.py llm/example/` |
| 显示屏不亮 | `SCREEN_OFF_UNTIL_START=0` 后重启，跳过亮屏门控 |
| 代理挂了但不想重启 | 本轮会超时回 `0`，恢复代理后下一件正常 |

### 2.7 已知短板（现场要有心理准备）

- **无底薄壁棱柱**：LLM 仍有一定概率判成 cube / 实心棱柱；YOLO 对 `prism4_thinWalled` 很强（20/20），
  对 `prism6_thinWalled`、`pyramid5` 完全不会（16 类模型里没有这两类）。
- **慢请求**：ultrafast 实测 5~7 秒，偶发 8~10 秒；超过 12 秒会回落 YOLO。
- **灰色**：只有 LLM 能输出，YOLO 回落会当成白/黑。
- **贴纸关闭**：同色同形但贴纸不同的件会并进同一盒（本轮预期行为）。
