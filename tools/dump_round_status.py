import re
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
lines = open(sys.argv[1], encoding="utf-8-sig", errors="replace").read().splitlines()
TS = re.compile(r"^(\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d{3})")
REP = re.compile(r"LLM 原始回复：(.*)$")
TOK = re.compile(r"prompt_tokens=(\d+) cached_tokens=(\d+).*completion_tokens=(\d+) reasoning_tokens=(\d+)")
DONE = re.compile(r"耗时 ([\d.]+) 秒")
cur = None
rows = []
for line in lines:
    m = TS.match(line)
    hm = m.group(2) if m else ""
    if "接收: a" in line and line.rstrip().endswith("a"):
        cur = {"t": hm}
        rows.append(cur)
    if cur is None:
        continue
    m2 = REP.search(line)
    if m2:
        try:
            cur["r"] = json.loads(m2.group(1))
        except Exception:
            cur["r"] = {"raw": m2.group(1)[:40]}
    m3 = TOK.search(line)
    if m3 and "tok" not in cur:
        cur["tok"] = tuple(int(x) for x in m3.groups())
    m4 = DONE.search(line)
    if m4 and "lat" not in cur:
        cur["lat"] = float(m4.group(1))

print(f"{'时间':8} {'status':10} {'color':7} {'shape':20} {'LLM':>5} {'prompt':>6} {'cached':>6} {'reason':>6}")
for c in rows:
    r = c.get("r", {})
    if not isinstance(r, dict):
        continue
    tok = c.get("tok", (0, 0, 0, 0))
    print(f"{c['t']:8} {str(r.get('status')):10} {str(r.get('color')):7} {str(r.get('shape')):20} "
          f"{c.get('lat', 0):5.2f} {tok[0]:6} {tok[1]:6} {tok[3]:6}")
    if r.get("shape_description"):
        print(f"         └ {r['shape_description'][:110]}")
