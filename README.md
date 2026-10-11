# Orange Pi 视觉控制程序

本目录用于在 Orange Pi（RK3588，Linux）上运行视觉识别，并通过串口与电控通信。目前有以下方案：

- **YOLO（RKNN）**：入口 `main_push_yolo.py`，模型推理由 `yolo/inference_utils.py` 中的 `DetectRunner` 完成，本地 NPU 推理。
- **LLM（视觉大模型 + YOLO fallback）**：入口 `main_push_llm.py`，优先调用 `openai/gpt-6-astra`（可换 Qwen-VL 等）；查栏和四视角分类的 LLM 请求超过配置时限（默认 35 秒）或失败时，用同轮原图运行本地 YOLO。
- **Laser（视觉替换激光传感器）**：入口 `main_laser_llm.py`，检测画面变化判断落料，再用左、右、顶部三个视角识别，具体见 [方案说明](docs/laser_llm.md)。
- **Agent（自主决策）**：入口 `agent/main_agent.py`，大托盘 YOLO 定位 + 识别区由模型自主决策（拍几个角度、解不解码、归哪组，均由模型现场决定），独立于 `llm/` 的固定流程（详见 `agent/README.md`）。

`main_grab.py` 为早期抓取版本（沿用旧 a/b/f 线性协议）。各方案复用公共模块和显示屏，Laser 使用 a/s 落料流程，推送版使用 r/b 转向流程。

## 上次更改

10/3 hrx

1. 修薄壁判据：**彩色物体的侧面开口不再被判成实心**（三处配套改）。
   - 现象：黄色的薄壁件在 `cube` 与 `prism4_thinWalled` 之间反复横跳，同一件多次跑结果不同 → 拆成两类 → 白吃盒位 → 触到"每盒 4 件"上限后丢件。
   - 根因：4 张形状示例**全是黑色的**，`prompt.py` 写的是"**中部暗色块**也可以是空腔"，而 `black_prism6` 的 note 还写着"**实体表面明暗变化不等于空腔**"。模型因此学成"空腔 = 暗色块"；而黄色件的开口内部是**与外壳同色的另一面**——既不暗、又符合"表面明暗变化"，于是被判成实心。（这也解释了为什么 10/1 缩图到 0.6 反而判对：那是光影分布变了导致的偶然匹配，不是真看懂了。）
   - 改法（把判据从"颜色"挪到"几何"）：
     a) `llm/prompt.py`：去掉"暗色块"暗示，明确"**空腔不一定呈暗色**，彩色物体的开口内部往往与外壳同色"，并写明"**只要任一视角**能看出侧面/顶面存在开口（口沿、内壁、内部底面可见）就判带 `thinWalled` 的薄壁形状"；
     b) `llm/empty_examples.py`：两条 `*_thinwalled` 的 note 由"中部暗色块表现为空腔，内壁不清晰也可判断薄壁"改为"**多个视角都能看到被外缘完整包围的开口/内壁**"；
     c) 新增示例 `llm/example/yellow_prism4_thinwalled.jpg`（现场这件黄色薄壁件、开口最清楚的视角），并在 `SHAPE_EXAMPLES` 登记。
   - **验证（9/29 那批 18 轮逐项回归）**：除 r009 由 `cube` 修正为 `prism4_thinWalled` 外，**其余 17 项与 9/29 真机逐项一致、零扩散**（cone / prism6 / pyramid3 / ball 都没有冒出新 `*_thinWalled`）；6 个黄色方块干净分成 **3 实心 + 3 薄壁**，每件 3/3 稳定；10/1 那件蓝色薄壁圆柱仍 `cylinder_thinWalled` 未被带坏。
2. 顺手排掉两个**无效方向**，以后别再试：
   - **提高 reasoning 档位没用**：`LLM_REASONING_EFFORT` 从 low 到 high，`high` 烧掉 **214 个 reasoning token 仍判 `cube`**，只慢不赚 → **保持 `low`**。（此前"reasoning>0 才会判出薄壁"只是 6 个样本的巧合。）
   - **调 `DETECT_LLM_INSET` 没用**：`crop_detect_llm` 是**裁切**而非缩放，物体在 532×532 里的像素数与 720×720 相同，改 inset 不给物体增加任何细节。
3. 取帧健壮性：**挡住近纯色废帧**。`capture/images/20260929_163117/original/stage2_r001_02.jpg` 存下来是一张纯白帧（mean=254、std=1.8），第 1 件因此只送了 3 个视角——而 `categorize()` 是按首次出现顺序分配 1~6 的，**第 1 件正是"定调"的那件**。
   - `shared/common.py` 新增 `frame_is_usable()`（`std < 4` 或过曝/全黑视为废帧）；`get_frame_retry()` 改为"超时与废帧都算一次失败，废帧丢弃后继续取下一帧"。
   - **两种退出语义严格分开**：一次帧都没收到才返回 `None`（按摄像头故障处理，**原语义不变**）；收到过帧但均不合格则**退回最后一帧 + 告警**，不新增停机路径——宁肯送一张可疑帧，也不能因为"镜头正对着干净白底"就把整场判成硬件故障。
   - 因为改在取帧层，阶段 1 查栏、阶段 2 四视角、两个摄像头一次性全覆盖；`original/` 下也不会再写入纯白帧。
4. 上游延迟已恢复：同一请求 10/1 测得 9.2s（判断为 DevDay 后的上游拥堵），10/3 复测 **4.14s（均值）**，与 9/29 的 4.81s 同量级 → 结论仍是**不买档位**。
5. 待办：可选再补一张**黄色实心立方体**示例（让"薄壁 vs 实心"成为显式成对示例）；赛前 1~2 天装 systemd 自启 + 冷启动（真断电）演练。

10/1 hrx

1. 修复显示屏整片玫红 / 全黑（**重要**）：这块板子的 rockchip GL 驱动加载失败（日志里的
   `glx: failed to create dri3 screen` / `failed to load driver: rockchip`），SDL 自动挑到的
   visual/renderer 会把画面渲染成整片玫红，严重时窗口根本画不出来（表现为"全程黑屏"，
   极易被误判成显示门控的问题）。`shared/show.py` 在 `pygame.init()` 之前用 `setdefault`
   强制 `SDL_VIDEODRIVER=x11` / `SDL_RENDER_DRIVER=software` / `LIBGL_ALWAYS_SOFTWARE=1`，
   对所有入口生效，外部显式设置仍可覆盖。表格只有 20fps，软件渲染完全够用。
2. 显示门控竞态修复（`main_push_llm.py`）：原来显示线程直接采样 `start_requested` /
   `column_check_requested`，而主循环一发现就立刻 `clear()`；显示线程 20fps 才采样一次，
   有 10%~40% 概率整段错过（表现为"一直黑屏"）。改为控制器消费 a/c 时置位一个**永不清除**
   的 `display_wake`，并与两个原始事件一起作为门控（谁先被看到都算点亮）。
3. 上游档位（service_tier）实验结论：**没有可买的速度**。`llm/qw.py` 新增
   `LLM_SERVICE_TIER`（`.env` 可调，默认空 = 不发），透传 OpenRouter 的 `service_tier`。
   `openai/gpt-6-astra` 在 OpenRouter 上有 7 个上游：openai/flex $5/$25、azure 与 openai
   $10/$50、bedrock 与 azure/us $11/$55、openai/fast $20/$100、openai/ultrafast $60/$300。
   - **坑**：请求 `ultrafast` 时服务端会**回落成 `priority`**（官方文档：ultrafast 优先走
     ultrafast 端点，失败回落 priority，再回落默认），"看着测了 ultrafast"其实没测到。因此
     **响应日志新增打印服务端实际回报的 `service_tier`**，每次都能核对实际落档。
   - 结论：priority 相对 default 无可见收益（8.56s vs 8.89s，n=3）→ **不要买档位**。
4. 延迟归因（**以下都已排除，以后别再重复排查**）：节点延迟、上行带宽、图片载荷与分辨率
   （245→136KB 无变化）、图片张数（9→4 张仅省 1.3s）、API 端点（`/responses` 与
   `/chat/completions` 相同）、缓存模式（explicit 与 portable 相同）、客户端（curl-http、
   curl-socks5、python-httpx 三者一致）。**结论：9.2 秒里约 7 秒在 OpenRouter/上游侧
   （其自记 2.2s）；9/29 同一请求为 4.81s。**
5. 新增排查开关与工具（**默认全部关闭，不影响线上**）：`LLM_DEBUG_TIMING=1` 打印
   「SDK 序列化 / 建连+上传+上游等待 / 读响应体」三段耗时；`LLM_DEBUG_DUMP=<path>` 把原始
   请求体落盘（便于用 curl 原样重发做客户端对照）；`test_llm.py classify --runs N` 重复 N 次
   并打印中位/均值/极值；`tools/tier_ab.sh` 档位 A/B（代理预检 + 失败重试 + 汇总表）；
   `tools/probe_images.py` 量化"图片张数 → 延迟"。
6. 补记 9/26：`tools/preview_camera.py` 新增 `--http 8080` 网页预览（MJPEG，不依赖 X11
   转发，PC 浏览器直接看）；叠加框改为默认全关（`--tray` / `--columns` 才显示）；窗口标题
   改 ASCII（避免远程 X server 无中文字体时显示成 ??????）。
7. ⚠️ **部署隐患（自启前必须处理；README 9/25 第 6 条与事实不符）**：9/25 那条写的是
   "`.bashrc` 与项目 `.env` 均设 HTTPS_PROXY"，**实测板子的 `.env` 里没有代理三行**，代理
   只在 `~/.bashrc` 里；而 `deploy/*.service` 只配 `EnvironmentFile=.env` —— **systemd 不读
   `.bashrc`，自启后进程没有代理 → LLM 全部 Connection error → 静默回落 YOLO**（日志形态和
   "地区门禁 403"一模一样）。修复：把下面三行写进 `.env`（`.env` 已 gitignore，不进仓库）：

   HTTPS_PROXY=http://127.0.0.1:7890
   HTTP_PROXY=http://127.0.0.1:7890
   NO_PROXY=localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12

   另建议 `LLM_FALLBACK_TIMEOUT=12`（当前正常件就要 ~9 秒，设 10 会大面积误回落）、
   `LLM_RETRIES=1`。
8. 修复表格里「类」等汉字显示成**非简体字形**：`shared/show.py::_load_font` 原来是"按名字
   模糊匹配、谁先遍历到用谁"，而 `pygame.sysfont.get_fonts()` 的列表里 `notosanscjkhk` /
   `jp` / `kr` / `tc` 都排在简体 `sc` **之前**，于是长期在用非简体 face 渲染（「类」最明显）。
   改为：
   - 先按 `SC_FONT_FILES` 顺序尝试**简体专用字体文件**：`NotoSansCJKsc-*.otf`、
     `wqy-microhei.ttc`、`wqy-zenhei.ttc`、`arphic/uming.ttc`；
   - 再按名字排序匹配：`notosanscjksc` / source-han-sans / wqy 等简体提示优先，
     `jp` / `kr` / `tc` / `mono` 变体**降权**（而非直接丢弃，保证极端情况下仍有字体可用）。
   本机实测选中 `wqy-microhei.ttc`（文泉驿微米黑）。想改用 Noto Sans CJK SC，把
   `SC_FONT_FILES` 里那两行 wqy 删掉即可（会走名字匹配命中 `notosanscjksc`）。
   **注意**：不要直接指向 `NotoSansCJK-Regular.ttc` —— 它的**第 0 个 face 是 JP**，而
   `pygame.font.Font` 不能指定 face 索引，这正是字形变歪的根源之一。


9/28 hrx

0. 针对没有继电器的pcb做的伪黑屏程序。
1. 显示屏上电门控：`shared/show.py::run_recognition_display` 新增两个**可选**参数 `gate_events=()` 与 `dpms=False`，默认关闭——`main_push_yolo.py` / `main_laser_llm.py` / `main_grab.py` / `agent/main_agent.py` / `tests/test_laser.py` 这些只传 2~3 个参数的老调用方行为**完全不变**。
   - 传入 `gate_events` 后显示线程进入**待机**：只 `fill(BLACK)`，不画表格；循环里 `any(e.is_set())` 一旦成立才点亮并开始画表格。`main_push_llm.py` 传的是 `(start_requested, column_check_requested)`，即 `communicate_serial` 收到第一个 a 或 c 的那一刻。
   - 点亮状态用显示线程内部的 `activated` **锁存**，不受主程序 `wait_for_start_or_check` 里 `clear()` 影响——否则一轮开始后屏幕会被打回黑屏。
2. 新增 `shared/screen.py`：`xset` 的薄封装（`screen_configure()` / `screen_off()` / `screen_on()`）。找不到 `xset` 或没有 DISPLAY 时静默返回 `False`，不抛异常，避免屏幕控制反过来拖垮分拣主流程。`screen_configure()` 会关屏保并把 DPMS 超时全设 0（不自动休眠），但保留手动 `force off/on`——比赛途中最关键的那十几秒不会自己黑屏。
3. `.env` 新增两个开关（`.env.example` 同步补注释）：
   - `SCREEN_OFF_UNTIL_START`（默认 `1`）：`1` = 上电阶段只铺纯黑，收到首个 a/c 后才画表格；`0` = 关掉门控，一启动就出表格（调试用）。
   - `SCREEN_DPMS`（默认 `0`）：`1` = 额外用 `xset dpms force off/on` 真灭背光，需要 `sudo apt install x11-xserver-utils`；`0` = 只铺黑底、背光仍亮。
4. 取舍说明（重要）：默认走「黑窗口」是因为它在裁判视角下**更安全**——HDMI 信号保持、始终有画面，只是全黑；DPMS `force off` 真灭背光且唤醒通常 <1 秒，但个别 HDMI 屏唤醒时会弹「无信号」OSD 或要 1~3 秒重新同步。若赛规要求"屏幕持续有显示"（如超 15 秒无显示本轮结束），就保持 `SCREEN_DPMS=0`。
5. 调试：不想黑屏用 `SCREEN_OFF_UNTIL_START=0 python main_push_llm.py`（或改 `.env`）。待机/点亮各有日志（「显示屏待机中（纯黑）」/「首次收到 a/c，点亮显示屏」），事后可对时间线。


9/26 hrx

1. 贴纸解码改为「总开关 + 默认关闭」：
   - `shared/common.py` 新增 `sticker_decode_enabled()`，读环境变量 `LLM_STICKER_DECODE`，**默认关闭**（填 `1` 才开）。
   - `main_push_llm.py::_resolve_label` 在关闭时**直接返回** `label_description or label_kind`，完全不做解码。原来的开关只关掉了「label 模型定位」，整帧扫描（最多 4 帧）仍在执行，单件白花 0.5~1.4 秒且日志看不出原因。
   - `yolo/fallback.py` 的回落路径共用同一开关，保证 LLM 与 YOLO 两条路径给出**一致的类别键**（否则同一件货一条路给 `qrcode`、另一条给解码内容，会被算成两个类别）。
   - 附带：关闭后 `label_runner` 不再加载，省一个 RKNN 模型的加载与 NPU 占用。
   - **代价**：`text/qrcode/barcode` 类的类别键只剩类型（如 `qrcode`），**同色同形但贴纸内容不同**的货物会被并成一类。需要区分时把 `.env` 的 `LLM_STICKER_DECODE` 改成 `1`，不用改代码。
2. 新增 `tools/set_empty_reference.py`：替换空场景参考图（`llm/example/<scene>_empty.jpg`）。背景/底板更换后**必须**更新参考图，否则模型会拿旧底板作基准，把新背景本身误判成"新物体"走 open-set 登记，实测会白白占掉一个储物盒。工具会校验源图是否为**未裁剪原图**（已裁剪的图直接拒绝，防止二次裁剪导致参考图与实时画面取景错位），`--dry-run` 可先预览裁剪结果，正式替换时自动把旧图备份为 `*_empty.bak.jpg`。
3. `llm/example/detect_area_empty.jpg` 更新为新背景（源图为 720x720 未裁剪原帧）；替换后模型收到的参考图为 512x512，与实时四视角（532x532）**取景一致**（仅等比缩小）。

9/25 lj

1. 增加main_laser_llm.py，用视觉检测画面中的RGB变化来替换激光传感器。收到a后等物体掉下来，检测到就发s喊停。
2. 顶部摄像头单开线程，一直识别托盘里有什么、在第几道。落料后先判断是不是已有的颜色+形状组合，明确是新类就先发箱号，让电控先动，再补具体识别。
3. shared里增加laser.py（变化检测，替换激光传感器），图片共享和采集合到common.py。
4. 原main_llm.py、main_push.py分别改名为main_push_llm.py、main_push_yolo.py，更新了下面的文件夹结构。

9/25 hrx

1. 识别提速：阶段2 单次 LLM 真机中位 9.1s → 4.3s，单件视觉侧（收到 a → 发出结果）12.7s → 8.2s。三处改动：
   - 新增 `LLM_KEEPALIVE_EXPIRY`（默认 300 秒）并让 `_get_client()` 注入自定义 httpx 连接池。**httpx 默认只保活 5 秒**，而阶段2 相邻两次请求间隔 10~20 秒（三次转向 + 机械动作 + 等 v），连接每次都被回收、每次重做 TCP+TLS 握手。实测首字节 4.13s → 1.8s，是本次最大的一处收益。
   - 新增 `LLM_REASONING_EFFORT`（`.env`，默认 `low`，经 `extra_body.reasoning` 传给 `/responses`；填 `off` 回退服务端默认 medium）。实测 reasoning tokens 由 71~130 降到 0~58，主要收益是砍掉生成阶段的长尾（137→0/16/58），20 次 A/B 的分类答案完全一致。
   - 新增 `LLM_REF_MAX_SIDE`（`.env`，0=不限，线上设 512）：只作用于前缀的 5 张固定参考图（1 张空场景 + 4 张形状示例），它们占单次载荷约 2/3 且每轮重发。设 512 后总载荷 342→245 KB、prompt tokens 6236→5204。
2. `_load_httpx()` 兼容性处理：本机 openai SDK 的 httpx 被换名（请求日志的 logger 名是 `httpx2`），裸 `import httpx` 会 ModuleNotFoundError，因此按 `httpx` → `httpx2` → `openai._base_client.httpx` 依次探测；全部失败则退回 SDK 默认客户端（只丢失保活优化，不影响功能）。
3. 新增 `tools/ab_reasoning.py`：用同一条四视角请求对比不同接口/reasoning 档位的耗时与 reasoning tokens，并打印每张图的尺寸与 KB，用于量化调优收益。
4. `tools/net_check.sh` 加固：出口国家改为多源兜底（ipinfo 会限流 429，之前会导致"静默无输出"），新增 `127.0.0.1:7890` 监听检查，并明确提示 HK/MO 节点会被 OpenRouter 对 OpenAI 系模型的地区门禁拦（403）。
5. `deploy/wave-vision-llm.service` 的 `[Unit]` 增加 `After/Wants=mihomo.service`。
6. 运行环境（重要）：LLM 链路现在依赖板内 mihomo（systemd，`mixed-port 7890`）作为出口，**节点必须是美国**——实测 HK 节点会被 OpenRouter 403 地区门禁拦。`.bashrc` 与项目 `.env` 均设 `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:7890`，赛前先跑 `bash tools/net_check.sh` 确认 4 项全绿。
7. 取帧重试：新增 `FRAME_RETRY_TIMES`（默认 5）与 `common.get_frame_retry()`，`check_columns` / `run_stage_two` 三处调用改用它——原来单次 1 秒取帧超时就直接 `raise CameraStopped()` 让整个进程退出，USB 摄像头偶发抖动就会导致现场停摆；现在连续 5 次（共 5 秒）取不到才判故障，中途恢复会打 WARNING 留痕。
8. 显示屏末行固定为「未归类」：类别满（≥6 类）/ 储物盒已满（≥4 件）时不再静默丢件，末行显示货物名称（附原因）、最近一帧图片与累计丢弃件数，并打 WARNING 日志（`main_llm._show_discarded`）。显示表格行数由 6 行改为 7 行。
9. `LLM_FALLBACK_TIMEOUT` 改为 `.env` 可调（默认仍为 35 秒，实测建议现场设 18）；配合 `LLM_RETRIES=1`——原先一次 Connection error 会因为重试+35 秒总时限白等 33 秒。
10. 新增代理部署文件 `deploy/mihomo.service` 与 `deploy/mihomo-config.example.yaml`（订阅链接为占位符），板内代理从此可按仓库重建。


9/25 hrx

1. LLM 输入裁剪可调：新增 `shared/const.py` 的 `PALLET_LLM_INSET` / `DETECT_LLM_INSET`（四边独立比例，0 为不裁），在把图发给大模型前内缩；只影响大模型输入，**YOLO fallback 与 capture 下保存的原图都不受影响**。当前值：阶段1 托盘 `{left:0.01, top:0.01, right:0.03, bottom:0.03}`、阶段2 识别区四边 `0.13`。
   - 阶段1 是「先按 `TRAY_*` 裁出托盘区域，再内缩」，阶段2 是「先用采集端的 `crop_square` 裁成正方形，再内缩」；空场景参考图与当前图走同一套裁剪，保证取景一致。
   - 阶段1 左右内缩会整体平移栏位（托盘每一栏的归属会偏），若非必要建议保持 left/right 为 0。
2. 图片压缩可调：新增 `.env` 的 `LLM_IMAGE_SCALE`（等比缩放系数，1.0 不缩）与 `LLM_JPEG_QUALITY`（默认 85）。注意 `LLM_MAX_SIDE=1024` 对这两个摄像头的图一直是**空转**（720x720、805x523 本来就小于 1024），要压体积得靠 `LLM_IMAGE_SCALE`。
3. 新增日志「LLM 图片载荷：N 张，共 X KB（尺寸列表）」，用于核对每次真正发出去的图有多大、多长。
4. 修复 `test_llm.py pallet` 的图序 bug：必须传 `[空托盘参考图, 当前托盘图]`，之前传反会让模型把空参考图当成当前托盘，稳定输出全 0。
5. 新增 `tools/crop_preview.py`：PC 上按四边比例裁剪预览并保存，`--use-const` 直接读 `const.py` 的比例，`--compare` 输出「原图+红框 / 裁剪结果」对比图，`--scale/--quality` 可预估载荷体积。依赖 `opencv-python`、`numpy`。
6. 新增 `tools/net_check.sh`：启动前网络自检（出口 IP 与国家、key 是否有效、目标模型能否真正调用）。OpenRouter 的地区限制（403）在程序里会表现为「35 秒超时 → 回落 YOLO」，先跑这个脚本可避免误判。
7. 阶段2 形状记忆改为保存**裁剪后**的四视角，使「历史参考拼图」与当前图取景一致。

9/22 lj

1. 现在摄像头初始化有cap = cv2.VideoCapture(camera_id, cv2.CAP_V4L2)，CAP_V4L2，可调缓存区，但是导致windows无法使用（若需windows板端调试，可在laser_sensor_replace.py中用同名函数）。
2. 增加laser_sensor_replace.py，测试文件，尝试通过检测图片中像素RGB变化来替换激光传感器（计划xzs车反克隆使用），效果可以。

9/17 hrx

1. 修复 `match_vocab` 的 `_canon` 归一化：改为只保留字母数字，颜色/形状键的大小写、空格、连字符、下划线等变体都能归一命中，减少模型输出变体引发的无谓纠错重试。
2. `shape_recognition.classify` 的异常处理由白名单改为 `except Exception` 兜底：识别期任何异常都统一转成 `LLMError` 走 YOLO fallback，避免未预期异常导致整个流程崩溃。


9/16 hrx

1. 默认识别后端切换为 OpenRouter + `openai/gpt-6-astra`（Responses API + 显式缓存，`LLM_CACHE_MODE=openai_explicit`）。
2. 新增离线测试脚本 `test_llm.py`：PC 上无需摄像头/串口/RKNN，直接喂图片测识别。
   - 四视角分类：`python test_llm.py classify 图1.png 图2.png 图3.png 图4.jpg`
   - 托盘查栏：`python test_llm.py pallet 托盘图.jpg`
   依赖 `openai`/`opencv-python`/`numpy`/`pyserial`，`.env` 配好 key 即可。

9/16 lyc

1. 根据实际测试微调了提示词。
2. 为了让gpt-6-astra正确命中缓存，以利于推理时长和我的钱包，现在.env中设置`openai_explicit`会采用response api 以及openai 自己的显式缓存写法。如果用qwen则不用修改。

9/15 hrx

1. 修复了摄像头打开失败无法退出的问题。

9/15 lyc

1. 添加fallback：查栏和四视角分类的 LLM 请求超过 35 秒或失败时，用同轮原图运行本地 YOLO。

9/14 lyc

1. 修改llm分支，现在添加了示例照片。
2. 微调了提示词，现在大托盘的识别只会识别存在物体主体部分的栏。同时模型对一些容易混淆情况地处理令人满意了。
3. 修改了显示屏代码。
4. 修改了逻辑。现在识别区40秒内没有给出结果，则什么都不发，继续流程，与电控逻辑相适配。

9/13 hrx

1. 货物名称中文化（颜色+形状，如 blue/prism3 → 蓝色三棱柱）；加大行高适配方形图片

9/13 hrx

1. 显示屏改为全屏固定表格，未识别到的行留空；字号缩小、图片列加宽。

9/13 lyc

1. 修改了llm分支，现在会发空样本图片给llm。

9/13 lyc

1. 修改const.py, 现在默认串口是 dev/ttyS0。同时修改了托盘在画面中的参数。
2. llm/qwen.py中删除了temperature=0, 同时适配qwen与gpt。

9/13 hrx

1. 储物盒每盒上限强制为 4 件（新增 `MAX_ITEMS_PER_CATEGORY=4`）：类别满盒时回 `0`，避免第 5 件塞进同一储物盒；YOLO/LLM/抓取三入口统一在分类后判断，agent 侧由 `GroupMemory` 拦截。
2. 显示屏改为流水式表格，字段为 序号 / 货物名称 / 图片 / 分拣成功总数量（累计）；表头中文化，并自动加载系统中文字体（找不到时退回默认字体，中文会变方框，需安装中文字体）。
3. `requirements.txt` 补齐贴纸解码可选依赖（`zxing-cpp` 推荐、OCR 二选一），README 写明系统库安装命令。

9/12 hrx

1. 贴纸识别开放化：去掉 11 类闭集词表，改为 `label_kind`（none/pattern/text/qrcode/barcode）+ 图案开放描述；码/文字类用 label 模型定位贴纸框后交给 `yolo/decoder` 确定性解码（`LLM_STICKER_DECODE=0`（默认）时**完全不解码**，label 直接取 LLM 给出的 kind/描述）。

9/12 hrx

1. 将 agent/ 由「开放分类器」重构为「自主 agent」：识别区改为模型驱动工具循环（rotate/decode/finish/no_object），拍几个角度、解不解码、归哪组均由模型现场决定；独立于 llm/ 包，仅复用 shared/ 与 yolo/（定位/解码当工具）；新增 loop.py / llm_client.py / memory.py。

9/12 hrx

1. 新增 agent_vision/ 开放集分拣方案：大托盘 YOLO 定位 + 识别区 Qwen-VL 开放式分类，解决「未见过对象无法识别」问题；离线测试脚本 test_offline.py 复用 agent_core 判定逻辑，与线上行为一致。

9/11 lyc

1. 新增 LLM 未知形状记忆：保存四视角图片和简短结构描述，分配 unknown_001 等形状 ID；后续识别带上全部历史参考图，相同形状复用 ID，明显不同的形状新增类别。记忆仅在本次运行有效，重启重新学习，旧图片保留供排查。
2. 整理目录：LLM 代码移入 llm/，YOLO 代码及执行器移入 yolo/，公共配置、摄像头、串口和显示移入 shared/；三个 main 入口保留根目录，统一调整导入及资源路径，启动命令不变。

9/11 hrx

1. llm_api/qw.py：重试按错误类型分流（鉴权/权限立即失败、网络原样重试、解析/校验失败带纠错提示重试）；新增 validator 回调，词表校验失败自动纠错重试；客户端改单例复用连接池；_parse_json 容忍 BOM 与结尾围栏。
2. prompt.py：新增 match_vocab 无损词表归一化与中文名反查表，兜住大小写/空格/连字符/中文输出；刻意不做编辑距离猜测，避免 prism3/prism4 等近邻类别误判。
3. main_llm.py：修复 _parse_pallet_result 不认 JSON 布尔 true/false 的 bug；分类解析改用 match_vocab；查栏/分类两处调用传入 validator。

9/11 hrx

1. show.py 表格改全屏、字号/行高随分辨率等比放大，加 ESC 退出。
2. main_push.py 阶段2诊断输出收进 DEBUG_STAGE2 开关，默认关闭；进度/结果改简洁中文一行。

9/11 hrx

1. 将 YOLO 与 LLM 两个方案合并到同一仓库，抽出公共层 `common.py`（摄像头 / 串口 / 帧取 / 事件等待 / 临时分类 / 图像裁剪 / 托盘几何），两个入口统一引用。
2. 统一 `categorize` 为「颜色 / 形状英文名 + 贴纸」字符串 key，YOLO 侧由类别 ID 先映射成英文名再调用，两个后端行为一致。
3. `const.py` 合并为共用常量：保留原 YOLO 全部内容，末尾追加 LLM 调试项 `SAVE_LLM_RESPONSES` / `LLM_RESPONSE_DIR`。
4. `main_grab.py` 恢复维护：公共函数迁入 `common.py`，补上「类别已满回 0」处理，识别区摄像头对齐主流程的中心裁剪。
5. `display.py` 的类别中文名对齐 `const.py`（修 `blue2`→`deep_blue`、`pytamid3`→`pyramid3`，补全 12 色 / 16 形状）。
6. 新增 `deploy/` ，其下 YOLO / LLM 两个 systemd 服务（包括原根目录下 wave-vision 自启服务），`.gitignore` 增加 `llm_responses/`。

9/10 hrx

1. label_detect_area 由 4 类扩展到 11 类，更新 const.py 的 LABEL_CLASSES 与 LABEL_PATTERN_TYPES。
2. 大托盘由 4 列改为 5 列，并更新 readme 文档。

9/8 hrx

1. 整理了 README.md
2. 维护了原 main.py 抓取方案，更名为 main_grab.py

9/8 lyc

1. 对齐了const.py。
2. 修改main_push.py: 类别已满时返回0,而不是直接退出。

9/7 lyc

1. 添加了开机自启。安装方式：首先在wave-vision.service中第13行把信息填好，之后在板端执行

```Shell
cd ~/<你的这个仓库的目录路径>
sudo cp wave-vision.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wave-vision.service
```

      之后就可以开机自启main_push.py了。看日志可以用

```Shell
journalctl -u wave-vision.service -b
```

9/6 19:23 lyc

1. 修改了capture.py，现在摄像头配置与主流程一致，通过设置变量可以实现识别区的裁剪。
2. 修改了main_push.py， 每次保存托盘画框图时，同步保存对应原图。

9/6 lyc again

1. 在阶段一同时等待a 或c，之后触发对应行为，发送c完成原来行为之后就回到开始监听a or c，发送a之后才进入第二阶段。
2. 保存大托盘画上4个区域的画框图片。
3. 修改波特率为115200。

9/6 lyc

1. yolo框中心在托盘x,y坐标之外直接排除，不考虑这些yolo框；
2. 在保存画框图时，在另一个文件夹里保存对应的原始图；
3. 将const.py中类别与models里面的yaml文件对齐。

9/4 hrx

1. 修复多线程死锁：摄像头/串口故障时置位 `stop_event` 通知主流程退出；所有阻塞 `get`/`wait` 加超时并检查停止标志；新增 `CameraStopped` 异常统一收尾，避免上游故障导致主线程永久卡死。
2. `read_camera` 帧管理改为「清空旧帧 + `put_nowait`」，真正只保留最新帧；摄像头打开/读取失败时置位 `stop_event`，不再静默退出。
3. `communicate_serial` 串口读写增加 `SerialException`/`OSError` 异常保护，串口断开时正常退出而非静默崩溃。
4. `temporary_categories`/`categorize` 收进 `PushController` 实例，消除全局可变状态并发隐患；`PushController` 增加 `stop_event` 参数。
5. 阶段 2 诊断图命名加轮次前缀（`stage2_r{轮次}_{序号}.jpg`），多轮识别不再互相覆盖。
6. 摄像头帧率读到 0 时兜底为 25.0，避免 `VideoWriter` 收到非法帧率。
7. show.py 的 `RecognitionRow.image` 类型注解由 `object | None`（Python 3.10 的 PEP 604 语法）改为 `Optional[object]`，修复 Python 3.8 下类定义时注解求值报 `TypeError` 的问题。

9/3 hrx

1. 调整 NMS 阈值：inference_utils.py 中 NMS_THRESH 改为 0.5，减少同类框因 IoU 略高被误删导致的漏检。
2. main_push.py 阶段 2 新增三模型叠加画框诊断图：新增 draw_debug_frame()，每轮检测把颜色（红）/形状（绿）/贴纸（蓝）检测框叠加画到同一张图，并保存到 capture/images/<启动时间>/stage2_01.jpg ~ stage2_04.jpg，便于定位识别问题。
3. PushController 增加 image_dir 参数以支持保存诊断图。
4. 分离了 large_pallet 和 detect_area 的颜色列表。

9/3 hrx

1. 修改了置信度阈值、盘子边界比例等参数。
2. main_push.py 阶段 2 增加诊断输出（[Stage2 诊断]）：打印每轮颜色/形状/贴纸检测框数与置信度、最终累加置信度汇总，便于排查识别问题。

9/3 hrx

1. 阶段 1 通信改为同时监听 `c` 与 `a`：电控不一定要发 `c` 查栏，也可能跳过查栏直接发 `a` 开始下一轮；视觉按先收到的指令处理（`c` 则查栏回复后等 `a`，`a` 则直接进入阶段 2）。
2. 同步更新 README 流程说明。

9/2 hrx

1. 修正了 readme 文档中的项目结构。
2. 修改 main_llm 文件格式为 python 源文件。（保留了原文本文件）
3. 修正了 read_camera 重构没改完的问题，修正线程参数和函数签名不对齐、帧未放回队列的问题。
4. 拆分 main_pull 中的主函数，主循环拆分为 PushController 类。

9/2 lj

1. 小改摄像头相关程序。
2. 合并大模型分支。

 9/2 hrx

1. main_push.py 第一阶段通信改为：电控发 `c` 后视觉识别托盘各栏，回复 4 位数组（`1\n` 有 / `0\n` 无，第 1 位对应靠近激光传感器的一栏，即画面左侧，可通过参数修改顺序），不再发送「第几列, 推送距离」。
2. 视觉回复布尔数组后等待电控发 `a` 再进入阶段 2。
3. 阶段 2 结束后协议改为：电控发 `v`，视觉先在显示屏显示信息，再回复 `f`（原为电控发 `f` 后视觉才显示）。
4. 新增 column_occupancy() 等列判断函数与 LASER_SENSOR_ON_LEFT 开关；串口线程新增 `c`/`v` 命令处理；录像逻辑抽为 record_pallet_streams()。
5. 同步更新 README 流程说明。

 8/31 hrx

1. 识别区检测改为转 3 次 × 90°（共 4 个角度），将const.py 中 STAGE_TWO_DETECTION_COUNT 由 3 改为 4。
2. 同步更新 main.py / main_push.py 中关于检测次数与转向次数的注释。
3. 同步更新 README 流程说明（转向 120 度 → 90 度，转 2 次 → 3 次）。
4. 8cpu + 3npu 我觉得区区 5 线程不在话下！（呃不过真的很烫

 8/31 lyc

1. 修改了main.py与main_push.py, 使其契合目前的通讯协议。目前等待电控发送"f"后才会把更新信息放到与显示屏线程的通信队列中。
2. 修改了README.md中的流程说明。
3. 目前开了5个线程，希望没问题（

 8/28 lyc

1. 添加了show.py文件，其中`RecognitionRow`类别保存一行信息，
   `run_recognition_display()`绘制pygame窗格。
2. main.py 与 main_push.py 中用`run_recognition_display()`构建相应的显示线程。

 8/27 hrx

1. 机械爪抓取改为推铲推料：盘子均分为 4 列，推铲在 top 端（画面底端）推动整列，把最靠近 bottom 端（画面顶端）的末端物体顶下
2. 阶段 1 与电控通讯改为发送「第几列, 推送距离」，不再发送目标中心坐标与尺寸
3. 新增托盘范围常量（COLUMN_COUNT、TRAY_X_MIN/MAX、TRAY_Y_MIN/MAX、TRAY_LENGTH_REAL、PUSH_MARGIN），用于把画面坐标映射到托盘，适配托盘与视野大小不同
4. 修改后的main文件备份为main_push.py

 8/24  hrx

1. 识别区新增贴纸识别，应用 label 模型，图案类直接标记，码/文字类解码（新增 decoder.py，复用前期测试的解码脚本）
2. 新增粗略类别数预判，若颜色+形状类别数大于特定上限，则在第二阶段忽略贴纸（新增 category_counter.py，默认关闭，需要时可以开 ENABLE_CATEGORY_PRE_CHECK=True）
3. 阶段2贴纸解码结果并入临时类别编号；粗略类别数达到阈值时跳过贴纸，直接按颜色+形状分类

 8/23  lj

1. 将常量配置移到 `const.py` 中
2. capture.py移入tools，增加自动抽帧和录视频，注释换功能（可能有bug,且不能展示画面，谁有更好写法改一下）。
3. 改readme。以后readme结构要包含：第一部分：上次更改，push前写一个更改小总结，像我这样简单写一下改了啥，写时间和是哪位长官。第二部分，结构介绍，后面就和lyc老师之前写的一样（PS:以后在这里编写要正式，作为视觉组沟通方式（不用ai了），这一条就例外了）

## 项目结构

```text
orangepi/
├── main_push_llm.py             # LLM 推送入口
├── main_push_yolo.py            # YOLO 推送入口
├── main_laser_llm.py            # 视觉替换激光传感器，三摄像头识别
├── laser_sensor_replace.py      # 早期画面变化检测测试
├── main_grab.py                 # YOLO 早期抓取入口
├── test_llm.py                  # LLM 图片测试入口
├── llm/
│   ├── __init__.py
│   ├── qw.py                    # 百炼 OpenAI 兼容接口
│   ├── prompt.py                # 词表、识别/快速分组/托盘盘点提示词
│   ├── shape_recognition.py     # 四视角及固定机位识别、快速分组、校验与登记
│   ├── shape_memory.py          # 本次运行形状参考图库
│   ├── pallet_monitor.py        # 顶部托盘持续盘点，记录物体和道位
│   ├── empty_examples.py        # 空场景与带标签示例加载
│   └── example/                 # 空场景和带标签的形状示例
├── yolo/
│   ├── __init__.py
│   ├── inference_utils.py       # DetectRunner / OBBRunner 与后处理
│   ├── decoder.py               # 贴纸解码
│   ├── fallback.py              # LLM 双场景备用识别，预加载与四视角 YOLO 汇总
│   ├── category_counter.py      # 粗略类别数预判
│   └── py_utils/                # RKNN / ONNX / PyTorch 执行器与 COCO 工具
├── agent/           
│   ├── main_agent.py            # 入口：YOLO 查栏 + agent 识别编排
│   ├── loop.py                  # 模型决策 + 工具 dispatch 主循环
│   ├── llm_client.py            # 多轮图文会话客户端
│   ├── memory.py                # 分组记忆（储物盒分组）
│   ├── prompts_agent.py         # 系统提示词（动作清单 + 约束）
│   ├── tools.py                 # 贴纸解码工具（封装 yolo/decoder）
│   ├── config.py                # 步数上限 / 解码开关
│   ├── test_offline.py          # 离线测试
│   └── README.md                # 本方案说明
├── shared/
│   ├── __init__.py
│   ├── const.py                 # 公共配置及项目根目录定位
│   ├── common.py                # 摄像头、帧共享、串口、分组校验与图像工具
│   ├── laser.py                 # ROI 画面变化检测，判断落料
│   └── show.py                  # pygame 显示与结果队列
├── models/                      # RKNN 模型及训练类别说明
├── tests/                       # 离线回归测试
├── tools/                       # 采集、裁剪预览、网络自检与耗时对比
├── docs/laser_llm.md             # Laser 方案与配置说明
├── deploy/                      # systemd 服务
├── README.md
├── PLANS.md                     # 本地实施记录（已被忽略）
├── requirements.txt
├── .env.example
└── .gitignore
```

按入口文件名运行，例如 `python main_push_llm.py` 或 `python main_laser_llm.py`。公共配置在 `shared/const.py`，
提示词在 `llm/prompt.py`。模型相对路径、`capture/` 和 `llm_responses/` 均以项目根目录为基准；
`llm/qw.py` 仍读取根目录 `.env`。`tools/capture.py` 自身的原始采集输出仍在 `tools/raw/`、`tools/videos/`。
历史更改记录里的旧路径保留原样，当前路径以此结构为准。

## 当前流程

以下为双摄像头推送版；Laser 三摄像头流程见上面的简介及 [方案说明](docs/laser_llm.md)。

1. 程序持续读取 `large_pallet` 和 `detect_area` 两个摄像头，并分别录制两路视频。
2. 阶段 1 同时监听电控的 `c` 与 `a`：
   - 发 `c`：视觉识别托盘各栏，回复 5 位数组（`1` 有 / `0` 无，第 1 位对应靠近激光传感器的一栏），随后等待 `a`或`c`。
   - 发 `a`：跳过查栏，直接进入阶段 2。
3. 阶段 2：`a` 触发第一次检测，视觉回复 `r` 要求转向 90 度。
4. 电控转向完成后发 `b`，视觉继续检测；如此转向 3 次，共 4 个角度。
5. 最后一次检测后，视觉回复 `0`（无物体/无法分类）或 `1`~`6`（种类），电控完成操作。LLM 入口在请求超过 35 秒或失败时改用 YOLO，最终回包还需等待本地推理完成。
6. 电控发送 `v`，视觉先在显示屏上显示识别结果，再回复 `f`。
7. 回到第 2 步：电控可能发 `c` 重新查栏，也可能直接发 `a` 开始下一轮，直到所有有物体的栏全部推到底。

> 说明：以上流程对 YOLO / LLM / Agent 三个后端完全一致，差别仅在识别实现——YOLO 用 RKNN 模型逐角度检测后累加置信度汇总；LLM 先拍齐 4 张图再一次性交大模型综合判断（含形状记忆）；Agent 大托盘查栏用 YOLO 定位，识别区由模型自主决定拍几个角度、是否解码贴纸、归入哪个分组（可识别未见过的东西）。查栏同理：YOLO 用颜色模型检测后按列中心判断，LLM 裁剪托盘图后交大模型返回数组，Agent 复用 YOLO 定位。
> `ENABLE_CATEGORY_PRE_CHECK`（默认关闭）开启后，阶段 1 会用形状模型粗略统计托盘上的类别数；种类足够多时阶段 2 跳过贴纸识别，直接按颜色+形状分类。

## 线程与故障处理

推送版采用「工作线程 + 主线程编排」结构：

- 两个摄像头读取线程（`read_camera`）：各自只保留最新一帧到队列（`maxsize=1`），避免阶段切换拿到积压旧画面。
- 串口线程（`communicate_serial`）：独立处理串口收发，避免串口 I/O 阻塞视觉主流程。
- 显示线程（`run_recognition_display`）：固定 6 行对应类别 1～6，显示类别编号、货物名称、最近一次成功分拣的图片、本类成功数量。同类成功更新原行，不追加流水；未使用类别数量为 0，名称与图片留空。只有收到电控完成信号后才更新，识别失败或超时不计数。

主线程负责阶段编排（等指令 / 查栏 / 多角度识别 / 上屏回执）。任一上游（摄像头、串口）故障时会置位 `stop_event`，主流程抛出 `CameraStopped` 并清理资源退出；所有等待均带超时兜底，不会永久阻塞。

## 运行前配置

### YOLO 版

在 `shared/const.py` 顶部确认以下配置：

- `PALLET_MODEL_PATH`
- `DETECT_AREA_COLOR_MODEL_PATH`、`DETECT_AREA_SHAPE_MODEL_PATH`：检测区颜色/形状模型路径。
- `DETECT_AREA_LABEL_MODEL_PATH`、`LABEL_CLASSES`：识别区贴纸模型路径与类别，顺序须与模型的 `data.yaml` 一致。
- `ENABLE_CATEGORY_PRE_CHECK`、`PALLET_SHAPE_MODEL_PATH`、`CATEGORY_COUNT_THRESHOLD`：粗略类别数预判开关与相关配置（默认关闭）。
- `CAMERA_PALLET_ID`、`CAMERA_DETECT_AREA_ID`：两个摄像头编号。
- `SERIAL_PORT`、`BAUDRATE`：电控串口配置。
- `COLUMN_COUNT`、`TRAY_X_MIN`、`TRAY_X_MAX`、`TRAY_Y_MIN`、`TRAY_Y_MAX`：托盘均分的列数与托盘在画面中的范围（相对画面宽高比例，`0~1`），用于把画面坐标映射到托盘，适配托盘与视野大小不同。`TRAY_Y_MIN` 为出口端（bottom，画面顶端），`TRAY_Y_MAX` 为推铲端（top，画面底端）。
- `TRAY_LENGTH_REAL`、`PUSH_MARGIN`：托盘沿推送方向的真实长度（可选，设为 `None` 时按比例输出）与推铲额外多推的余量。
- `PALLET_COLOR_CLASSES`、`DETECT_AREA_COLOR_CLASSES`、`SHAPE_CLASSES`：托盘/识别区颜色与形状类别，顺序必须与对应模型的 `data.yaml` 一致。
- `PRIVILEGED_SHAPES`：需要优先判定的形状名称；单次置信度达到 `PRIVILEGED_SHAPE_CONFIDENCE` 时覆盖形状置信度求和结果。

需要安装 OpenCV、NumPy、pyserial、pygame 和板端 RKNN Lite 运行环境。贴纸解码为可选功能：二维码用 OpenCV 自带（无需额外依赖）；条码需 `pyzbar`（含系统库 libzbar0）或 `zxing-cpp`（推荐，纯 pip 无系统依赖）；文字需 `pytesseract`（含系统 tesseract-ocr 及中文语言包）或 `paddleocr`（模型较大，板端慎用）。系统库安装（Ubuntu/Debian）：

```bash
sudo apt install -y libzbar0 tesseract-ocr tesseract-ocr-chi-sim
```

Python 依赖（已在 `requirements.txt` 中注释说明，按需取消注释）：

```bash
pip install zxing-cpp        # 条码/二维码解码（推荐，无系统依赖）
pip install pytesseract      # 文字 OCR（依赖上面的 tesseract 系统库）
```

配置完成后运行：

```bash
python main_push_yolo.py
```

### Agent 版

自主 agent，独立于 `llm/` 固定流程，仅复用 `shared/` 与 `yolo/`（详见 `agent/README.md`）。依赖与 LLM 版相同（密钥读根目录 `.env`），外加板端 rknn（复用大托盘 color 模型定位）：

```bash
python agent/main_agent.py
```

离线验证 agent 决策（PC 上即可，无需摄像头/rknn）：

```bash
python agent/test_offline.py test_images --expected expected.json
```

### LLM 版

运行前需在 `llm/example/` 放入两张对应摄像头拍摄的**未裁剪空场景原图**：
`pallet_empty.jpg`（空托盘）和 `detect_area_empty.jpg`（空识别区），目录中已提供现场照片。
同时加载 `llm/empty_examples.py` 中 `SHAPE_EXAMPLES` 列出的带标签示例，当前为黑色薄壁五棱柱、
实心六棱柱、薄壁四棱柱及实心三棱锥，共四张。
保持运行时的机位、方向、分辨率和光照；程序自动使用与实时画面相同的裁剪方式。
缺少或无法读取任一图片时，程序在启动摄像头和串口前报错退出；换图后需重启。
详见 [示例照片说明](llm/example/README.md)。

查栏一次发送两张图：第 1 张为空托盘参考，第 2 张为当前托盘。
分类按固定参考、历史参考、当前目标排列：第 1 张为空识别区，第 2～5 张为四个带标签示例，
之后是全部历史形状拼图，最后四张为当前目标。提示词图号随示例数和历史数自动计算；
空图只解释固定背景，不参与本轮计数或形状记忆。

Qwen-VL 识别，含未知形状记忆与开放集贴纸（图案用开放描述，码/文字用确定性解码）。
现在必须在板端安装 RKNN Lite 运行环境，并准备 `shared/const.py` 指定的四个模型：
`color_large_pallet.rknn`、`color_detect_area.rknn`、`shape_detect_area.rknn`、`label_detect_area.rknn`。
程序在打开摄像头和串口前预加载全部模型；加载失败退出，已加载的模型会释放。
`requirements.txt` 不包含板端 `rknnlite`，需使用已经具备该运行环境的 Python 环境。

```bash
conda create -n llm python=3.10 -y
conda activate llm
pip install -r requirements.txt
cp .env.example .env   # 填入 DASHSCOPE_API_KEY
python main_push_llm.py
```

`.env` 已加入 `.gitignore`。相关环境变量：

| 变量                       | 默认值                                                | 说明                                                                                          |
| -------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `DASHSCOPE_API_KEY`      | （无）                                                | 必填，API Key（OpenRouter 或百炼）                                                                            |
| `DASHSCOPE_BASE_URL`     | `https://openrouter.ai/api/v1` | 兼容 OpenAI 的地址                                                                            |
| `DASHSCOPE_MODEL`        | `openai/gpt-6-astra`                                     | 视觉模型名（OpenRouter 需带 provider 前缀）                                                                                    |
| `LLM_MAX_SIDE`           | `1024`                                              | 发送前图片最长边（降延迟/费用）                                                               |
| `LLM_TIMEOUT`            | `60`                                                | 单次网络请求超时（秒）；main_llm 查栏和分类请求另有固定 35 秒总上限，随后切换 YOLO            |
| `LLM_RETRIES`            | `2`                                                 | 失败重试次数                                                                                  |
| `LLM_CACHE_MODE`         | `openai_explicit`                                          | `openai_explicit`=Responses API + 显式缓存（gpt-6-astra）；`portable`=Chat Completions（Qwen 等） |
| `LLM_SHAPE_MEMORY_LIMIT` | `12`                                                | 本次运行可登记的词表外形状数，范围`1~32`；与六个分拣类别不同                                |
| `LLM_STICKER_DECODE`     | `0`                                                 | 贴纸解码总开关：`0`(默认)=完全不解码，label 直接取 LLM 给的 kind/描述；`1`=label 框定位解码、失败降级整帧解码。LLM 路径与 YOLO fallback 共用，保证两条路径的类别键一致  |

录像自动保存在 `capture/videos/`，阶段 1 的目标截图保存在 `capture/images/<启动时间>/`。

> LLM 贴纸识别：图案类由模型开放描述（如「红色五角星」，可区分红星/蓝星）；文字/二维码/条码优先定位贴纸框再解码。定位关闭或未解出时使用整帧解码；仍失败时用贴纸类型名。

### 固定前缀与可选显式缓存

识别区请求由三段 `user` 消息组成：

1. 固定识别规则、词表、示例目录 + 空识别区图片、带标签示例。
2. 本次运行的历史目录与图号 + 历史形状拼图。图库不变时这段也不变。
3. 本轮任务文字 + 当前四视角。格式校验失败的纠错文字只放这一段。

托盘请求有两段：固定查栏规则 + 空托盘；本轮查栏任务 + 当前托盘。
没有删减图片、降低分辨率或改变形状判定规则；改变的是消息分段、图片顺序及对应图号。

默认模式适用于当前使用的 Chat Completions 兼容格式：

```dotenv
LLM_CACHE_MODE=portable
```

该模式只发送通用的 `role/content/text/image_url`，不会向 Qwen 或其他模型发送 OpenAI
专用缓存字段。前缀一致不等于必定命中；自动缓存行为取决于服务商。

当模型及端点确认支持 OpenAI 显式缓存时（例如官方 `gpt-6-astra`），可在 `.env` 设置：

```dotenv
LLM_CACHE_MODE=openai_explicit
```

此模式调用 `/responses`（需要支持 `client.responses.create` 的 OpenAI SDK），
将内容转换为 `input_text/input_image`，从 `output_text` 读取结果。
在识别区的第 1、2 段末尾各加一个 `prompt_cache_breakpoint={"mode":"explicit"}`，
托盘只在第 1 段末尾设置。顶层发送 `prompt_cache_options={"mode":"explicit","ttl":"30m"}`，
当前照片和纠错段不设缓存断点。通过 SDK 的 `extra_body` 传递顶层参数，支持尚未声明该字段的
SDK 版本；这不代表服务商一定接受。切换 Qwen 时使用 portable，仍调用 `/chat/completions`，
不发送 OpenAI 专用字段；暂不适配 Qwen 缓存。转发端点必须同时支持 Responses 和显式缓存。
若显式请求返回 HTTP 400，不重复重试该请求，日志提示检查支持范围或关闭专用模式；
main_llm 仍按现有逻辑使用 YOLO fallback。更改环境变量后需重启进程。

请求完成会打印：

```text
LLM 用量: prompt_tokens=... cached_tokens=... cache_write_tokens=... completion_tokens=... reasoning_tokens=...
```

两种接口的输入/输出 token 用量统一为上述日志字段，并记录响应 ID 与请求 ID。
`cache_write_tokens` 是服务端报告的缓存写入量，未提供时为 `unknown`。
`cached_tokens>0` 表示服务端报告缓存读取；`0` 表示报告未命中；`unknown` 表示未提供该字段，
不能据此判断命中与否。首轮通常需写缓存；前缀修改、缓存到期、路由及最小缓存长度都可能影响命中。
OpenAI 当前文档给出的 GPT-5.6 及以后模型最小前缀为 1024 tokens，缓存写入亦有独立费率；
不为凑长度添加无用内容。当前仍会编码、上传参考图，服务端缓存省的是重复前缀计算。
Responses 诊断实验使用现场照片验证了更换当前四视角后读取 5094 tokens 固定前缀缓存；
本次接入经模拟 HTTP 离线回归，板端运行及长期命中率仍需现场验证，缓存命中不保证总延迟降低。

参数依据：[OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)、
[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)（2026-09-16 核对）。

### LLM 双场景 YOLO fallback

| 场景             | LLM 等待截止 / 失败后的动作                                                          |
| ---------------- | ------------------------------------------------------------------------------------ |
| 大托盘`c` 查栏 | 对本次未裁剪原图运行托盘颜色模型，按五栏几何与传感器方向返回五行`1/0`              |
| 识别区四视角分类 | 复用已拍四张照片，累计颜色/形状/贴纸置信度，保留薄壁优先和贴纸解码规则，输出分拣编号 |

两场景均从 `qwen_realise` 调用开始计时，编码、网络等待和全部重试共用 **35 秒**，
常量为 `main_llm.LLM_FALLBACK_TIMEOUT`。API 最终报错、响应无法通过校验或分类 `uncertain`
也会切换 YOLO；有效的 LLM `empty` 或全空查栏结果直接采用，不触发 fallback。
迟到的 LLM 结果会丢弃，不再登记形状、更新计数或重复发送串口。后台 HTTP 可能稍后才结束。

35 秒不包含拍照、历史参考准备、YOLO 推理或贴纸解码。YOLO 耗时会写入日志；
**总耗时能否赶在电控 40 秒窗口内返回，需要板端实测，本次没有新增整轮 40 秒截止。**
不额外发送旋转命令，也不重新拍照。两种后端共用组合编号、六类/每盒四件限制与 `v → f` 握手；
只有可分拣结果收到 `v` 后才增加成功数量。YOLO 也失败时，查栏保留全 `1` 回包，分类回 `0`。

YOLO 使用训练词表，无法识别 LLM 的 `unknown_*` 身份或完整开放图案语义。只有颜色/形状/贴纸
字符串三元组相同才复用编号；后端之间语义近似但字符串不同的结果可能分到不同类别。

### LLM 未知形状记忆（仅本次运行）

识别优先采用原来的 16 种常见几何体及薄壁类型。遇到字典没有收录的几何体时，
允许模型报告 `new`，由程序生成 `unknown_001`、`unknown_002` 等稳定形状 ID。
正常情况下只请求一次，包含当前四视角和全部历史参考图；模型报告新形状且本地校验通过后直接登记。
仅新形状输出简短结构描述（建议 30~80 字），用于辅助比对，不作为分类键；模型不能自行编造 ID。
常见形状、历史匹配和空场景省略 `shape_description`，无需生成几何说明；不确定时用一句话说明原因。

每次识别会携带一张空识别区参考图、全部带标签示例、本次运行所有已登记形状的参考拼图，最后为当前四张图。每张参考拼图包含
一个历史形状的四视角，等比缩放为 `768×768`，不会为省空间静默漏掉旧形状。
相同形状再次出现时返回 `remembered` 及已有 ID；明显不同的结构可建立新的 ID。
颜色、贴纸、位置、旋转、整体缩放及轻微制造差异不改变形状身份；边数、孔洞布局、
空心/实心、主体轮廓及显著比例差异可用于区分词表外形状。两个物体都明确属于已有类型时，
不按细长程度拆类，例如不同长宽比例的长方体都归 `cuboid`。这个判断由模型结合参考图完成，
并非像素距离阈值，也不是在线训练或更新模型权重。

| 模型状态       | 本地处理                                                |
| -------------- | ------------------------------------------------------- |
| `known`      | 校验已有形状词表后使用英文键                            |
| `remembered` | 只接受本次运行目录中的 ID，校验通过即复用               |
| `new`        | 单轮判断及校验通过后保存参考原图、简短描述及新 ID       |
| `uncertain`  | 留档、不登记 LLM 形状，main_llm 改用 YOLO 分类          |
| `empty`      | 必须明确表示无目标，颜色、形状和贴纸全部为 JSON`null` |

图库非空时也不增加固定复核请求；所有参考图在同一次请求中发送。
`uncertain` 是合法的识别状态，本地保存原因和照片，不重新发送同一组图片。
只有网络/API 失败或 JSON 格式、字段校验错误才由 API 包装层按 `LLM_RETRIES` 重试。

`main_llm` 为形状分类请求传入 35 秒截止，超时后使用四视角 YOLO，详见上面的双场景 fallback。
超时的 LLM 结果不登记形状，YOLO 结果也不写入未知形状图库；当前四视角原图仍保留。

文件位置：

```text
capture/images/<启动时间>/shape_memory_<会话随机ID>/
├── memory.json                  # 成功登记的形状 ID、描述、样本目录
├── unknown_001_<随机ID>/
│   ├── view_1.jpg                # 登记时的四张原始视角
│   ├── view_2.jpg
│   ├── view_3.jpg
│   └── view_4.jpg
└── pending/<随机ID>/             # 无法归类的现场，不加入参考图库
    ├── view_1.jpg ... view_4.jpg
    └── review.json              # 原因及已经得到的规范化判断
```

新形状的图片和索引全部写入成功后，ID 才在内存中生效；索引用原子替换写入。
原型不会被后续照片自动覆盖，参考图丢失/损坏时会报错，不以空图库继续分类。
**重启会创建新会话，重新从 `unknown_001` 学习；旧图片保留供排查，不自动载入。**
这些文件已被现有 `capture/` 忽略规则覆盖。图片会占用磁盘，需要按现场留档策略清理旧会话；
不要在运行期间删除当前参考图库。关闭 `SAVE_LLM_RESPONSES` 仅关闭原始模型文本日志，
不会关闭参考图片和待复核现场的保存。

`unknown_001` 是形状身份，串口仍只发送组合分拣编号 `1~6`。例如蓝色和红色的同一未知形状
共用一个形状 ID，但颜色不同会分到不同组合。组合已满或盒满发送 `0`；新形状图库已满、
LLM 识别失败或待复核时先用 YOLO，YOLO 也无法分类才回 `0`。返回 `0` 不新增显示计数，
并继续等电控 `v` 后回复 `f`。图库满时仍可识别已有形状，不删除旧类或强行合并新类。电控仅凭 `0` 无法区分原因，
应结合本地日志；本次未增加错误指令或改变电控动作。

本功能增加参考图片输入：有 S 个带标签示例、N 个记忆形状时，一次请求输入 `5+S+N` 张图（当前为 `9+N`），不再固定请求两次；
格式/API 失败仍可能重试。需要现场检查延迟与电控等待时间。
单次模型判断仍可能出错，尤其是遮挡、近似形状或首次原型登记错误；不能保证绝对稳定。
记忆只影响 LLM 入口，不改变 YOLO 训练类别。

安装本项目依赖后，可运行离线回归测试（不会调用云端模型或驱动硬件）：

```bash
python -m unittest discover -s tests -v
```

现场验收建议：同一陌生物体多轮旋转、换位置后检查 ID 一致；换颜色/贴纸后检查形状 ID
仍一致、组合编号按现有规则变化；加入明显不同的物体检查新 ID；用薄壁/实心近邻及空场景
检查不会误合并；重启后检查重新学习。离线测试验证流程和存储，不代表视觉识别准确率。

## 部署（systemd）

```bash
# YOLO 版
sudo cp deploy/wave-vision-yolo.service /etc/systemd/system/wave-vision-yolo.service
# LLM 版
sudo cp deploy/wave-vision-llm.service /etc/systemd/system/wave-vision-llm.service
# 按实际修改 WorkingDirectory / ExecStart 的 conda 环境名 / EnvironmentFile(.env) 路径
sudo systemctl daemon-reload
sudo systemctl enable --now wave-vision-yolo   # 或 wave-vision-llm
journalctl -u wave-vision-yolo -f              # 查看日志
```
