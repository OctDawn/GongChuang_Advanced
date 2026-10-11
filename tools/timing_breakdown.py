import re
import sys
import datetime

sys.stdout.reconfigure(encoding="utf-8")
FMT = "%Y-%m-%d %H:%M:%S,%f"
TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})")

lines = open(sys.argv[1], encoding="utf-8-sig", errors="replace").read().splitlines()
events = []
for line in lines:
    m = TS.match(line)
    if not m:
        continue
    body = line.split("wave.vision", 1)[-1]
    events.append((m.group(1), body))


def dt(a, b):
    if not a or not b:
        return None
    return (datetime.datetime.strptime(b, FMT) - datetime.datetime.strptime(a, FMT)).total_seconds()


rounds = []
cur = None
for ts, body in events:
    if "接收: a" in body:
        cur = {"t_a": ts, "r": [], "b": [], "v": None, "send": []}
        rounds.append(cur)
        continue
    if cur is None:
        continue
    if "发送: r" in body:
        cur["r"].append(ts)
    elif "接收: b" in body:
        cur["b"].append(ts)
    elif "LLM 图片载荷" in body:
        cur.setdefault("t_payload", ts)
    elif "LLM 请求：" in body:
        cur.setdefault("t_req", ts)
    elif "HTTP Request: POST" in body:
        cur.setdefault("t_http", ts)
    elif "LLM 请求结束" in body:
        cur.setdefault("t_done", ts)
        cur.setdefault("lat", float(re.search(r"耗时 ([\d.]+) 秒", body).group(1)))
    elif re.search(r"发送: \d", body):
        cur["send"].append(ts)
    elif "接收: v" in body:
        cur["v"] = ts

print(f"{'轮':>3} {'a→第1帧+r':>9} {'r→b1':>7} {'b1→r2':>7} {'r2→b2':>7} {'b2→裁编码':>9} "
      f"{'→请求':>6} {'→首字节':>7} {'→生成完':>7} {'→回复':>6} {'→v':>5} {'总a→回复':>8}")
for i, r in enumerate(rounds, 1):
    t_a = r["t_a"]
    t_r1 = r["r"][0] if r["r"] else None
    t_b1 = r["b"][0] if r["b"] else None
    t_r2 = r["r"][1] if len(r["r"]) > 1 else None
    t_b2 = r["b"][1] if len(r["b"]) > 1 else None
    t_send = r["send"][0] if r["send"] else None
    vals = [
        dt(t_a, t_r1), dt(t_r1, t_b1), dt(t_b1, t_r2), dt(t_r2, t_b2),
        dt(t_b2, r.get("t_payload")), dt(r.get("t_payload"), r.get("t_req")),
        dt(r.get("t_req"), r.get("t_http")), dt(r.get("t_http"), r.get("t_done")),
        dt(r.get("t_done"), t_send), dt(t_send, r.get("v")), dt(t_a, t_send),
    ]
    print(f"{i:>3} " + " ".join(f"{v:>7.3f}" if v is not None else f"{'-':>7}" for v in vals[:4])
          + " " + " ".join(f"{v:>9.3f}" if v is not None else f"{'-':>9}" for v in vals[4:6])
          + " " + " ".join(f"{v:>7.3f}" if v is not None else f"{'-':>7}" for v in vals[6:]))
print()
print("单位：秒。a→第1帧+r = 主线程发现 a、取第 1 帧并发出第 1 个 r 的延迟")
print("b2→裁编码 = 最后一张拍下 → 3 张原图落盘 + 3 张裁切 + 9 张压缩编码完成的耗时")
print("→请求 = 图片载荷打印 → 真正发起 HTTP 请求（构建 payload）")
