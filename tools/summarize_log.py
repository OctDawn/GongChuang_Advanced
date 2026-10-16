import re
import json
import sys
import csv
import statistics
import collections
import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PATH = sys.argv[1] if len(sys.argv) > 1 else "attachments/1bbfb9f817d7/pasted_text_1791207033603.txt"
OUT_MD = sys.argv[2] if len(sys.argv) > 2 else "log_report.md"
OUT_CSV = sys.argv[3] if len(sys.argv) > 3 else "objects.csv"

COLOR_CN = {'black': '黑色', 'blue': '蓝色', 'brown': '棕色', 'deep_blue': '深蓝色',
            'deep_green': '深绿色', 'green': '绿色', 'orange': '橙色', 'pink': '粉色',
            'purple': '紫色', 'red': '红色', 'white': '白色', 'yellow': '黄色'}
SHAPE_CN = {'ball': '球', 'cone': '圆锥', 'cube': '正方体', 'cuboid': '长方体',
            'cylinder': '圆柱', 'cylinder_thinWalled': '薄壁圆柱', 'hemisphere': '半球',
            'parallelepiped': '平行六面体', 'prism3': '三棱柱', 'prism3_thinWalled': '薄壁三棱柱',
            'prism4_thinWalled': '薄壁四棱柱', 'prism5': '五棱柱', 'prism5_thinWalled': '薄壁五棱柱',
            'prism6': '六棱柱', 'prism6_thinWalled': '薄壁六棱柱', 'pyramid3': '三棱锥'}
FMT = "%Y-%m-%d %H:%M:%S,%f"


def cn(color, shape):
    return f"{COLOR_CN.get(color, color or '?')}{SHAPE_CN.get(shape, shape or '?')}"


def secs(t1, t2):
    if not t1 or not t2:
        return None
    try:
        return (datetime.datetime.strptime(t2, FMT) - datetime.datetime.strptime(t1, FMT)).total_seconds()
    except Exception:
        return None


def med(xs, nd=2):
    return f"{statistics.median(xs):.{nd}f}" if xs else "-"


def mean(xs, nd=2):
    return f"{statistics.mean(xs):.{nd}f}" if xs else "-"


lines = open(PATH, encoding="utf-8-sig", errors="replace").read().splitlines()

TS = re.compile(r"^(\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d{3})")
RECV = re.compile(r"接收: ([abcvf])\s*$")
SEND = re.compile(r"发送: (\S+)\s*$")
REPLY = re.compile(r"LLM 原始回复：(.*)$")
DONE = re.compile(r"LLM 请求结束，耗时 ([\d.]+) 秒")
STAGE2 = re.compile(r"阶段 2 结果: color=(\S+) shape=(\S+) label=(\S+) category=(\d+)")
EMPTY = re.compile(r"阶段 2 结果：无物体")
BOX_FULL = re.compile(r"阶段 2：储物盒 (\d+) 已满")
CAT_FULL = re.compile(r"阶段 2：类别已满")
DROPPED = re.compile(r"未归类第 (\d+) 件: (\S+)（(.*?)）")
FALLBACK = re.compile(r"识别区 LLM 超时或失败，切换多视角 YOLO: (.*)$")
FB_DONE = re.compile(r"识别区 YOLO fallback 耗时 ([\d.]+) 秒")
NEWCAT = re.compile(r"类别 (\d+) -> \((.*?)\)")
PALLET = re.compile(r"托盘栏占用: (\S+)")
TOK = re.compile(r"LLM 用量: prompt_tokens=(\d+) cached_tokens=(\d+) cache_write_tokens=\S+ completion_tokens=(\d+) reasoning_tokens=(\d+)")
VTO = re.compile(r"等待串口信号 v 超时")
WARN = re.compile(r"^(\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d{3}) (WARNING|ERROR|W|E) wave")

rounds, checks, tokens, warns = [], [], [], []
cur = cur_check = None
vto_total = 0
run_no = 0

for line in lines:
    m = TS.match(line)
    ts, hm = (m.group(1), m.group(2)) if m else (None, None)
    if "本次运行形状记忆目录" in line:
        run_no += 1
    if VTO.search(line):
        vto_total += 1
    m = WARN.match(line)
    if m:
        warns.append((m.group(2), m.group(3), line.split("wave.vision", 1)[-1].strip(": ")[:130]))

    m = RECV.search(line)
    if m:
        cmd = m.group(1)
        if cmd == "a":
            cur = {"idx": len(rounds) + 1, "t_a": ts, "hm_a": hm, "run": run_no}
            rounds.append(cur); cur_check = None
        elif cmd == "c":
            cur_check = {"t": ts, "hm": hm}; checks.append(cur_check); cur = None
        elif cmd == "b" and cur is not None:
            cur["t_b"] = ts; cur["b_count"] = cur.get("b_count", 0) + 1
        elif cmd == "v":
            if cur is not None:
                cur["t_v"] = ts
            elif cur_check is not None:
                cur_check["t_v"] = ts
        continue

    if cur_check is not None:
        m = DONE.search(line)
        if m:
            cur_check["latency"] = float(m.group(1))
        m = PALLET.search(line)
        if m:
            cur_check["occupancy"] = m.group(1)
        continue
    if cur is None:
        continue

    m = REPLY.search(line)
    if m:
        try:
            cur["reply"] = json.loads(m.group(1))
        except Exception:
            cur["reply_raw"] = m.group(1)[:80]
    m = DONE.search(line)
    if m:
        cur["latency"] = float(m.group(1))
    m = STAGE2.search(line)
    if m:
        cur["color"], cur["shape"], cur["label"], cur["category"] = m.groups()
        cur["outcome"], cur["t_result"], cur["hm_result"] = "classified", ts, hm
        continue
    if EMPTY.search(line):
        cur["outcome"], cur["t_result"], cur["hm_result"] = "empty", ts, hm
        continue
    m = BOX_FULL.search(line)
    if m:
        cur["full_box"] = int(m.group(1))
        continue
    if CAT_FULL.search(line):
        cur["cat_full"] = True
        continue
    m = DROPPED.search(line)
    if m:
        cur["outcome"], cur["drop_name"], cur["drop_reason"] = "dropped", m.group(2), m.group(3)
        cur["t_result"], cur["hm_result"] = ts, hm
        continue
    m = FALLBACK.search(line)
    if m:
        cur["fallback"] = m.group(1)
    m = FB_DONE.search(line)
    if m:
        cur["fb_secs"] = float(m.group(1))
    m = NEWCAT.search(line)
    if m:
        cur["new_cat"] = int(m.group(1))
    m = SEND.search(line)
    if m and m.group(1).isdigit():      # 只认结果回执（数字）；"发送: r"/"发送: f" 不算
        cur["t_send"] = ts
        cur["send_value"] = m.group(1)
    m = TOK.search(line)
    if m:
        tokens.append(tuple(int(x) for x in m.groups()))
        cur["tok"] = tuple(int(x) for x in m.groups())

for r in rounds:
    rep = r.get("reply") or {}
    if not r.get("color"):
        r["color"], r["shape"] = rep.get("color"), rep.get("shape")
    r["label"] = rep.get("label_kind") or rep.get("label_description")
    if r.get("outcome") is None:
        if rep.get("status") == "empty":
            r["outcome"] = "empty"
        elif r.get("full_box") or r.get("cat_full"):
            r["outcome"] = "dropped"
        else:
            r["outcome"] = "unknown"
    r["category_sent"] = int(r.get("category") or 0) if r["outcome"] == "classified" else 0

handled = [r for r in rounds if r["outcome"] == "classified"]
empties = [r for r in rounds if r["outcome"] == "empty"]
drops = [r for r in rounds if r["outcome"] == "dropped"]
MAXCAT = 6

o = []
w = o.append
stamped = [m.group(1) for m in (TS.match(l) for l in lines) if m]
first, last = stamped[0], stamped[-1]

w(f"# 智能分拣日志总结（{first[:10]} {first[11:16]}–{last[11:16]}）")
w("")
w(f"日志 {len(lines)} 行，时间跨度 {secs(first, last)/60:.1f} 分钟（{first[11:19]} ～ {last[11:19]}）")
w("")
w("## 一、总览")
w("")
w("| 指标 | 数值 |")
w("|---|---|")
w(f"| 阶段 2 轮次 | {len(rounds)} |")
w(f"| 识别到物体 | {len(handled) + len(drops)} 件（{len(handled)} 件归类 + {len(drops)} 件未归类） |")
w(f"| 空推（拍照时无物体） | {len(empties)} 次 |")
w(f"| 查栏 c | {len(checks)} 次 |")
w(f"| 建立类别 | {max([r.get('category_sent', 0) for r in handled] + [0])} 个（上限 MAX_TEMP_CATEGORIES={MAXCAT}） |")
w("")
w("## 二、逐件明细")
w("")
w("| # | 时间 | 货物 | 类别 | 结果 | LLM(s) | a→结果(s) | 备注 |")
w("|---|---|---|---|---|---|---|---|")
rows_csv = []
for r in rounds:
    rep = r.get("reply") or {}
    note = []
    if r["outcome"] == "empty":
        name, cat, res = "—（空）", "—", "无物体"
        note.append("空推")
    elif r["outcome"] == "unknown":
        name, cat = "（本轮无结果）", "0"
        if r is rounds[-1]:
            res = "日志在此截断，未见结果"
            note.append("非故障")
        else:
            res = "回 0（LLM 失败/超时）"
            note.append("未分拣；原因见日志")
    elif r["outcome"] == "dropped":
        name, cat = r.get("drop_name") or cn(r["color"], r["shape"]), "0"
        if r.get("cat_full"):
            res = "未归类（类别已满）"; note.append("6 个类别已占满，无处可放")
        else:
            res = f"未归类（储物盒 {r.get('full_box')} 已满）"; note.append(f"该盒已达 4 件上限")
    else:
        name, cat, res = cn(r["color"], r["shape"]), r["category"], "已归类"
        if r.get("new_cat"):
            note.append("新建类别")
    if r.get("fallback"):
        note.append(f"LLM 判 uncertain（{rep.get('shape_description') or '证据不足'}）→ YOLO 回落 "
                    f"{r.get('fb_secs', '?')}s")
    cyc = secs(r["t_a"], r.get("t_result"))
    cyc_txt = f"{cyc:.2f}" if cyc is not None else "-"
    lat = f"{r['latency']:.2f}" if r.get("latency") else "-"
    w(f"| {r['idx']} | {r['hm_a']} | {name} | {cat} | {res} | {lat} | {cyc_txt} | {'；'.join(note)} |")
    rows_csv.append([r["idx"], r["hm_a"], name,
                     COLOR_CN.get(r["color"], r["color"] or ""), SHAPE_CN.get(r["shape"], r["shape"] or ""),
                     r["category_sent"], res, lat,
                     f"{cyc:.2f}" if cyc is not None else "", "；".join(note)])

w("")
w("## 三、按类别统计（最终结果）")
w("")
w("| 运行 | 类别 | 货物名称 | 件数 | 时间点 |")
w("|---|---|---|---|---|")
by_cat = collections.OrderedDict()
for r in handled:
    by_cat.setdefault((r.get("run", 1), r["category"]), []).append(r)
for key in sorted(by_cat, key=lambda k: (k[0], int(k[1]))):
    items = by_cat[key]
    w(f"| #{key[0]} | {key[1]} | {cn(items[0]['color'], items[0]['shape'])} | {len(items)} | "
      f"{', '.join(i['hm_result'] for i in items)} |")
w("")
grp = collections.Counter((r["color"], r["shape"]) for r in handled)
w("按「颜色+形状」合并：")
for (color, shape), n in grp.most_common():
    cats = sorted({r["category"] for r in handled if (r["color"], r["shape"]) == (color, shape)})
    w(f"- {cn(color, shape)}：{n} 件 → 类别 {','.join(cats)}")
w("")
if drops:
    w("## 四、未归类明细")
    w("")
    for r in drops:
        reason = "类别已满（6 类占满）" if r.get("cat_full") else f"储物盒 {r.get('full_box')} 已满（≥4 件）"
        w(f"- 第 {r['idx']} 轮 {r['hm_result']}：{r.get('drop_name')} → {reason}，回 0")
    w("")

w("## 五、耗时统计")
w("")
lats = [r["latency"] for r in rounds if r.get("latency")]
clats = [c["latency"] for c in checks if c.get("latency")]
cycs = [c for c in (secs(r["t_a"], r.get("t_result")) for r in rounds) if c is not None]
cycs2 = [c for c in (secs(r["t_a"], r.get("t_send")) for r in rounds) if c is not None]
bs = [c for c in (secs(r["t_a"], r.get("t_b")) for r in rounds) if c is not None]
tov = [c for c in (secs(r.get("t_send"), r.get("t_v")) for r in rounds) if c is not None]
gaps = [g for g in (secs(p.get("t_v"), n["t_a"]) for p, n in zip(rounds, rounds[1:])
                     if p.get("run") == n.get("run")) if g is not None]
w(f"- 阶段 2 单次 LLM：n={len(lats)}，均值 **{mean(lats)}s**，中位 {med(lats)}s，"
  f"最小 {min(lats):.2f}s，最大 {max(lats):.2f}s")
w(f"- 查栏 c 单次 LLM：n={len(clats)}，均值 {mean(clats)}s，值 {[round(x,2) for x in clats]}")
w(f"- 单件 a→出结果：均值 **{mean(cycs)}s**，中位 {med(cycs)}s，最小 {min(cycs):.2f}s，最大 {max(cycs):.2f}s")
w(f"- 单件 发送结果→收到 v：均值 {mean(tov)}s，中位 {med(tov)}s")
w(f"- 单件 a→回复就绪（不含等 v）：均值 {mean(cycs2)}s，中位 {med(cycs2)}s，"
  f"最小 {min(cycs2):.2f}s，最大 {max(cycs2):.2f}s")
w(f"- 单件 a→三个视角 B 到位：均值 {mean(bs)}s")
w(f"- 上一件 v→下一件 a（仅同一次运行内统计）：均值 {mean(gaps)}s，中位 {med(gaps)}s，"
  f"最小 {min(gaps):.2f}s，最大 {max(gaps):.2f}s")
for run in sorted({r.get("run", 1) for r in rounds}):
    rs = [r for r in rounds if r.get("run", 1) == run]
    if not rs:
        continue
    end = rs[-1].get("t_v") or rs[-1].get("t_result")
    tot = secs(rs[0]["t_a"], end)
    if tot:
        w(f"- **运行 #{run}**：{len(rs)} 轮，{rs[0]['hm_a']} → {end[11:19]}，共 {tot:.1f}s"
          f"（{tot/60:.2f} 分钟），节拍 {tot/len(rs):.2f}s/轮")
w(f"- 纯视觉侧（不含搬运）：a→出结果 {mean(cycs)}s + 等 v {mean(tov)}s ≈ "
  f"{statistics.mean(cycs)+statistics.mean(tov):.2f}s / 件")
w("")
w("## 六、API 用量")
w("")
if tokens:
    p = sum(t[0] for t in tokens); c = sum(t[1] for t in tokens)
    comp = sum(t[2] for t in tokens); rea = sum(t[3] for t in tokens)
    w(f"- LLM 调用 {len(tokens)} 次（{len(lats)} 次阶段 2 + {len(clats)} 次查栏）")
    w(f"- prompt {p}，缓存命中 {c}（{c/p*100:.1f}%），completion {comp}（其中 reasoning {rea}）")
    w(f"- 阶段 2 固定前缀 ≈ {statistics.median([t[0] for t in tokens]):.0f} tokens/次，缓存后实际计费 ≈ "
      f"{statistics.median([t[0]-t[1] for t in tokens]):.0f} tokens/次")
    w(f"- 单件平均 reasoning tokens ≈ {mean([t[3] for t in tokens], 1)}")
    with_r = [r for r in rounds if r.get("tok") and r["tok"][3] > 0]
    without_r = [r for r in rounds if r.get("tok") and r["tok"][3] == 0]
    w("")
    w("**reasoning 与延迟的关系（阶段 2）**")
    w(f"- 触发了推理的轮次：{len(with_r)}/{len(rounds)}，LLM 均值 {mean([r['latency'] for r in with_r])}s，"
      f"reasoning 均值 {mean([r['tok'][3] for r in with_r], 1)}，最大 {max([r['tok'][3] for r in with_r], default=0)}")
    w(f"- 零推理轮次：{len(without_r)}/{len(rounds)}，LLM 均值 {mean([r['latency'] for r in without_r])}s")
    for r in with_r:
        w(f"  - 第 {r['idx']} 轮 {r['hm_a']}：reasoning={r['tok'][3]}，completion={r['tok'][2]}，LLM {r['latency']:.2f}s")
w("")
w("## 七、异常与串口")
w("")
w(f"- v 等待 1s 轮询超时：{vto_total} 次 —— 这是正常的轮询日志（每轮约 1 次），不是故障")
w(f"- W/E 级别日志 {len(warns)} 条：")
for hm, lvl, msg in warns:
    w(f"  - `{hm}` **{lvl}** {msg}")
w("")
w("## 八、查栏记录")
w("")
for c in checks:
    tv = secs(c["t"], c.get("t_v"))
    w(f"- {c['hm']}：占用 `{c.get('occupancy')}`，LLM {c.get('latency')}s"
      + (f"，等 v {tv:.2f}s" if tv is not None else "（本轮后直接来 a，未见 v）"))

open(OUT_MD, "w", encoding="utf-8").write("\n".join(o) + "\n")
with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fh:
    wr = csv.writer(fh)
    wr.writerow(["序号", "时间", "货物", "颜色", "形状", "类别(0=未归类)", "结果", "LLM耗时s", "周期s", "备注"])
    wr.writerows(rows_csv)
print("done", len(rounds), "rounds;", len(handled), "handled;", len(drops), "dropped")
