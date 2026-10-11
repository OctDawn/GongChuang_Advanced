# Linux / RK3588 三摄像头落料分拣

当前流程：顶部、左、右各一个采集线程；顶部盘点独立循环调用 LLM，识别物体及道位。
`a` 后 laser 检测变化并发 `s`，落稳后取左、右、顶部上半部分三张照片。
快速比较已有颜色+形状组：命中直接发箱号；明确新组先发箱号，再补全颜色/形状。
`v` 可在补全期间排队，显示刷新完成才回 `f`。第 4 点按推送道位提前分箱未实现。

运行入口：`python main_laser_llm.py`。使用板端已有的 `rknnlite` 环境和
`requirements.txt` 依赖，API 配置沿用 `.env`。启动时预加载四个 RKNN 模型，
供查栏、分类失败后的本地回退使用（仍沿用四模型加载器，本入口不执行贴纸推理）。

## 串口流程

串口、波特率沿用 `SERIAL_PORT` / `BAUDRATE`。电控可发单字符，也可附加 CR/LF；
视觉发送沿用旧程序的换行结尾。

| 电控发送 | 视觉动作与回复 |
|---|---|
| `c` | 查托盘，回复四行 `0`/`1`，例如 `1\n0\n1\n0\n` |
| `a` | 冻结参考画面，监测 ROI 变化；变化达到阈值后立即回复一次 `s\n` |
| 收到 `s` 后停推 | 视觉等画面稳定，采集各识别相机的一张新图，LLM 分类后回 `0\n` 或 `1\n`～`6\n` |
| `v` | 更新显示及计数，屏幕刷新完成才回 `f\n`；然后接受下一次 `a` 或 `c` |

无 `r/b` 转向阶段。重复 `a`、箱号发出之前的 `v`、忙碌时的 `c` 会记录日志并忽略。
即使分类回复 `0`，仍须用 `v/f` 结束本轮。编号按颜色和形状组合首次出现
顺序分配，重启清空；成功计数在 `v` 显示完成后增加。

沿用旧版本的异常语义：识别失败、类别超六种、盒满四件也回 `0`。
`c` 使用新鲜且运动版本有效的后台快照，否则用新帧 YOLO；失败保守回四个 `1`。
后台盘点顺序调用，不积压图片。快照道位从传感器侧起算，带拍摄及完成时间。
先发箱号后详细识别失败时保持该箱号及“待确认”名称，不再发 `0`；冲突会记录错误。

## 相机和参数

配置位于 `shared/const.py`：

- `LASER_CAMERAS`：固定为按顺序 `left`、`right`，加上 `CAMERA_PALLET_ID` 共三台。
  **右相机为占位路径，运行前必须配置真实路径**，可设置 Linux 环境变量 `CAMERA_RIGHT_ID`。
  程序拒绝重复设备路径，摄像头使用 V4L2。
- `LASER_TOP_DETECT_AREA=(0,1,0,0.5)`：顶部识别区默认完整图上半部分，保持长方形。
- 顶部后台盘点只裁 `TRAY_*` 托盘区域，不使用顶部识别区裁剪。
- `LASER_QUICK_TIMEOUT=3`：快速组匹配总时限；失败/不确定退回完整识别。
- `LASER_PALLET_TIMEOUT=20` / `LASER_PALLET_INTERVAL=0.5`：持续盘点请求上限与请求间隔。
- `LASER_PALLET_MAX_AGE=3`：`c` 使用快照的最大拍摄年龄，不按完成时刻计龄。
- `LASER_TRIGGER_CAMERA`：用于喊停的相机名称；其余相机同时参与分类。
- `LASER_AREA`：相对于完整相机图像的 `(x_min,x_max,y_min,y_max)`，
  应只覆盖落料通道，避免推杆进入 ROI。不相对于 LLM 裁剪图。
- `RGB_DIFF_THRESHOLD`：像素任一 BGR 通道变化阈值，默认 30。
- `STABLE_THRESHOLD`：变化像素比例阈值，默认 0.1，即 10%。
- `LASER_TRIGGER_FRAMES`：连续变化几帧触发，默认 1，优先及时喊停。
- `REQUIRED_STABLE_FRAMES`：学习参考画面和落稳检查使用的连续稳定帧数。
- `LASER_COLUMN_COUNT=4`：新协议栏数；原推送入口仍使用 `COLUMN_COUNT`。
- `LASER_SETTLE_SECONDS=0.3`：`s` 写出后至少等待 0.3 秒，再取新帧检查落稳。
- `LASER_SETTLE_TIMEOUT=3`：持续不稳则回 `0`。
- `LASER_DROP_TIMEOUT=10`：未检测到掉落时先发 `s` 停推、再回 `0`。
- `LASER_LLM_TIMEOUT=8`：每次 LLM 请求总上限，失败使用同轮原图回退 YOLO。

程序启动先等待相机、稳定参考和显示线程就绪，再打开串口。参考图未准备好就收到
`a` 时，回复 `s` 停推并回 `0`。这两个异常停推行为需要与电控一起联调。
软件通过画面变化判断触发，阴影、光照突变或支架抖动也可能触发，ROI 与阈值需要
按实际机位测试。8 秒仅是 LLM 请求上限，不是整件分拣时间保证。

## 模块职责

- `shared/laser.py`：`ChangeDetector.update(frame)` 返回变化比例和触发状态；
  空闲学习背景，`arm()` 后冻结参考，每轮一次触发；不打开硬件、不发送串口。
  `laser_task` 是可选线程入口，接收帧源、检测器和触发回调。
- `shared/common.py`：统一 `read_camera` 采集循环，支持旧队列与 `FrameStream` 广播。
  每个广播读者保留自己的序号，检测、YOLO、LLM 不会互相消耗图像；画框前需要 `copy()`。
  新组结果校验和箱号预留也在这里，不依赖 LLM。
- `main_laser_llm.py`：相机、串口、检测和显示线程的启动及状态协调；
  耗时识别在控制主线程执行，采集、检测和串口保持独立运行。
- `llm/shape_recognition.py`：`FixedViewRecognizer` 负责固定视角快速组匹配及颜色/形状识别，
  与旧 `ShapeRecognizer` 共用结果校验和形状登记。
  颜色/形状 ID 使用原词表英文键；词表外结构支持本轮 unknown_XXX 形状记忆。
- `llm/pallet_monitor.py`：持续托盘物体/道位盘点，线程安全快照及迟到结果隔离。
- `llm/prompt.py`：`PROMPT_GROUP_MATCH`、`PROMPT_FIXED_VIEW_CLASSIFY`、
  `PROMPT_PALLET_INVENTORY` 等固定规则；`build_*_prompt` 拼接视角数量、词表及参考目录。
- `shared/show.py`：沿用原表格，新增可选 `DisplayUpdate` 刷新完成回执，
  原推送入口的 `RecognitionRow` 调用保持兼容。

查栏和识别原图、模型回复及形状记忆保存在 `capture/images/<时间>_laser/`。
本入口暂不连续录像。

## 离线验证

安装项目依赖和 pytest 后执行：`python -m pytest tests/test_laser.py -q`。
测试使用合成图像、模拟串口、SDL 虚拟显示，不调用云端或 NPU。
真实落料误触发率、云端识别准确率和整机耗时仍需板端验证。
