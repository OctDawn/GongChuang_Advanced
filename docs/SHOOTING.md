# 拍 YOLO 训练数据：怎么拍、拍多少、怎么进训练

> 结论先行：**这次 18 类模型失败的原因是数据，不是参数。** 每类只有 18~30 张真实样本，
> 两个版本都没过基线（3/11 vs 6/11）。要再训，先把每类补到 **60~100 个独立姿态**。

## 一、目标量

一个「姿态」= 把货摆成某个样子，拍一次五视角（5 张图）。

| 每类姿态数 | 能达到什么 |
|---|---|
| < 30 | 只能验证流程，别指望准确率 |
| 40~60 | 大类可分，细分（prism5/6、pyramid3/4）仍会混 |
| **60~100** | 细分开始稳（推荐目标） |
| 150+ | 换光照/换机位也稳 |

18 类 × 60 姿态 ≈ 1,080 次拍摄 ≈ **4~5 小时**（每次 15 秒）。
时间紧就按优先级拍：`prism6_thinWalled`、`pyramid4`、`pyramid3`、`prism5/6` 系列、
`cylinder_thinWalled`、`cube`，先各拍 20~30 个。

## 二、开拍前（两条硬要求）

1. **停服务**：相机被主程序独占，否则 `snap_views` 直接失败。
   ```bash
   sudo systemctl stop wave-vision-llm
   ```
2. **不要移动相机、镜子、白挡板**。训练数据必须和比赛时的取景一致；
   移了机位等于换了一个任务。

## 三、拍摄脚本

```bash
cd ~/WaveRider_GongChuang_Vision

# 全部 18 类，每类 6 个姿态（默认）
bash tools/shot_sessions.sh

# 只拍重点类，每类 30 个姿态
bash tools/shot_sessions.sh --classes "prism6_thinWalled pyramid4 pyramid3" --count 30

# 中断后接着拍（已存在的 tag 自动跳过）
bash tools/shot_sessions.sh --classes "pyramid4" --count 30 --resume

# 空台负样本
bash tools/shot_sessions.sh --empty 20
```

脚本每个姿态都会停下来等你摆好货再回车；输入 `s` 跳过、`q` 退出。
结束会打印每类实际拍了几组，直接能看出还差多少。

## 四、tag 命名（决定训练时怎么读）

格式：`<类名>_p<两位序号>`，例如 `pyramid4_p07`。

- 类名必须用 18 类的**准确拼写**（`prism6_thinWalled` 是驼峰，别写成 `prism6_thinwalled`）；
  写错脚本会直接拒绝，不会白拍。
- **`empty` 只用于空台**（负样本）。
- ⚠️ 历史坑：11/24 那批里 `parallelepiped` 标签下其实是空台、`empty` 标签下其实是平行六面体
  （当时没找到平行六面体的件）。**这次不要再混**：平行六面体就写 `parallelepiped`，空台就写 `empty`。

## 五、姿态清单（每个姿态只改一处）

| 序号 | 姿态 | 要点 |
|---|---|---|
| p01 | 直立 | 最常见的出厂姿态 |
| p02 | 侧躺 | 让端面朝向 ①②，方便数边 |
| p03 | 倒放 | 开口朝下 → ③ 能看到内腔 |
| p04 | 绕竖轴 45° | 破坏“正对镜头”的偏置 |
| p05 | 绕竖轴 90° | 同上，另一个方向 |
| p06 | 偏左 5cm | 货不在正中时也要能识别 |
| p07+ | 自由 | 斜靠、半遮挡、贴近台边、叠放（可选） |

建议每 10 个姿态里至少有 2 个“贴着台边/偏角摆放”，现场机械手不会总放正。

## 六、光照

- **不要为了拍照调灯**：比赛现场是什么光就什么光，训练集要和现场同分布；
- 如果灯可调，挑 2~3 类在“全亮 / 关主灯”各拍一组（约 1/4 的样本），
  这是模型唯一能学到光照不变性的机会；
- ③ 仰视那一路背景过曝严重，YOLO 训练时会被丢掉（见下），不必为它单独折腾。

## 七、拍完怎么进训练

```bash
# 1) 只保留模型能可靠检出的样本，丢掉低置信/补框的（避免脏框）
python tools/build_shape_dataset.py --src capture/views --out datasets/shape18_new --conf 0.25

# 2) 按新口径：去掉 ③ 仰视、⑤ 镜面裁上 60%
python tools/mirror_crop_dataset.py --src datasets/shape18_new --out datasets/shape18_new2 \
    --drop-view 3 --mirror-crop 0.60

# 3) 训练（数据放 WSL 原生盘，别放 /mnt/d，9p 很慢）
python tools/train_shape18.py 30

# 4) 评测（与推理口径一致）
python tools/eval_shape.py --model runs/night3/R1/weights/best.pt \
    --drop-views 3 --mirror-crop 0.60
```

**及格线**：`tools/eval_shape.py` 在 `_eval2` 的 11 项里 **≥ 6/11**（当前 16 类模型 6/11），
且不能丢掉现在判对的项（三组通心筒、r002、r003、r004、r005）。

## 八、常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `snap_views` 报相机打不开 | 服务没停 | `sudo systemctl stop wave-vision-llm` |
| 拍完 `ls capture/views` 数量对不上 | tag 重复覆盖（同 tag 只保留一份） | 序号必须递增，别重拍同一个 pNN |
| 训练时 ③ 的图全是空白背景 | 仰视本来就看不到货 | 正常，训练时已丢弃 |
| ⑤ 训练图下半幅是虚焦桶体 | 忘了裁镜面 | `mirror_crop_dataset.py --mirror-crop 0.60` |
| 类别名报错 | 驼峰拼错 | 用脚本里的白名单，别手打 |
