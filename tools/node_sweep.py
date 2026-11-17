"""逐个节点实测「能不能真的连上 OpenAI」，输出可用节点清单。

背景：OpenAI 有两道门——
  1) 官方支持名单（香港 / 中国大陆 / 俄罗斯 明确不支持，直接 403）；
  2) 出口 IP 信誉（机房 IP、被滥用过的共享 IP，即使地区支持也会 403/429/Cloudflare）。
第二道门只能实测，所以本脚本做的就是：把每个节点切出去，打一次 api.openai.com，
按返回码分类。

用法（板上，项目根目录；先停主程序，脚本会临时把 mihomo 切到 global 模式）：

    sudo systemctl stop wave-vision-llm
    conda activate llm
    python tools/node_sweep.py                     # 全部节点
    python tools/node_sweep.py --grep "日本S|新加坡"
    python tools/node_sweep.py --timeout 6 --csv sweep.csv

判读：
    401 = 可用（没带 key，OpenAI 正常回 401 而不是地区 403）
    403 = 被地区限制或风控拦截
    429 = 被限流/风控（IP 信誉问题，换节点）
    000 = 连不上（节点本身不通）

结束时自动恢复原来的 mode 和 GLOBAL 选择。中途 Ctrl+C 也会尝试恢复。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.dotenv import load_dotenv  # noqa: E402

load_dotenv()

API = "http://127.0.0.1:9090"
PROXY = "http://127.0.0.1:7890"
TARGET = "https://api.openai.com/v1/models"


def _api(path, payload=None, method=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def _get(path):
    return _api(path, method="GET")


def _openai_code(timeout):
    """经 127.0.0.1:7890 请求 OpenAI，返回 (状态码字符串, 毫秒)。"""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    started = time.monotonic()
    try:
        with opener.open(TARGET, timeout=timeout) as response:
            code = str(response.status)
    except urllib.error.HTTPError as error:
        code = str(error.code)
    except Exception:  # noqa: BLE001 —— 超时/连接错误统一记 000
        code = "000"
    return code, int((time.monotonic() - started) * 1000)


def _node_names(pattern=None):
    data = _get("/providers/proxies/ikuuu")
    items = data.get("proxies") or []
    names = [item["name"] for item in items] if isinstance(items, list) else list(items.keys())
    if pattern:
        import re
        names = [name for name in names if re.search(pattern, name)]
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grep", default="", help="只测名字匹配这个正则的节点")
    parser.add_argument("--timeout", type=float, default=6.0, help="单节点超时（秒）")
    parser.add_argument("--csv", default="", help="把结果写到 CSV")
    parser.add_argument("--sleep", type=float, default=0.15, help="切节点后的等待（秒）")
    args = parser.parse_args()

    try:
        names = _node_names(args.grep or None)
    except Exception as error:  # noqa: BLE001
        raise SystemExit("读不到 mihomo 节点（sudo systemctl status mihomo 看一下）: %s" % error)
    if not names:
        raise SystemExit("没有匹配的节点：%r" % args.grep)

    config = _get("/configs")
    old_mode = config.get("mode", "rule")
    try:
        old_global = _get("/proxies/GLOBAL").get("now")
    except Exception:  # noqa: BLE001
        old_global = None

    print("共 %d 个节点，模式 %s -> global，单节点超时 %.0fs" % (len(names), old_mode, args.timeout))
    print("提示：这会临时让所有流量走 GLOBAL，别在跑主程序时用。\n")

    rows = []
    try:
        _api("/configs", {"mode": "global"}, method="PATCH")
        for index, name in enumerate(names, start=1):
            _api("/proxies/GLOBAL", {"name": name}, method="PUT")
            if args.sleep:
                time.sleep(args.sleep)
            code, ms = _openai_code(args.timeout)
            rows.append((name, code, ms))
            mark = {"401": "OK  ", "403": "403 ", "429": "429 ", "000": "FAIL"}.get(code, code)
            print("%3d/%3d  %-32s %5s  %5dms" % (index, len(names), name, mark, ms))
    except KeyboardInterrupt:
        print("\n中断，正在恢复配置…")
    finally:
        try:
            _api("/configs", {"mode": old_mode}, method="PATCH")
            if old_global:
                _api("/proxies/GLOBAL", {"name": old_global}, method="PUT")
            print("已恢复 mode=%s，GLOBAL=%s" % (old_mode, old_global))
        except Exception as error:  # noqa: BLE001
            print("!! 恢复失败，请手动执行：curl -X PATCH -H 'Content-Type: application/json' "
                  "-d '{\"mode\":\"rule\"}' http://127.0.0.1:9090/configs  (%s)" % error)

    usable = [row for row in rows if row[1] == "401"]
    print("\n可用节点（401）%d / %d：" % (len(usable), len(rows)))
    for name, code, ms in sorted(usable, key=lambda item: item[2]):
        print("  %-32s %5dms" % (name, ms))

    if usable:
        import re
        regions = sorted({re.sub(r"\d+.*$", "", name.split(" ")[0]) for name, _, _ in usable})
        print("\n可用的地区前缀：", "、".join(regions))
        print("建议把 filter 收窄到实测通过的节点，例如（按实际名字调整）：")
        print('    filter: "(?i)(日本S0[1-9]|新加坡S0[1-3]|美国S0[12])"')

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig") as stream:
            stream.write("node,code,ms\n")
            for name, code, ms in rows:
                stream.write('"%s",%s,%d\n' % (name, code, ms))
        print("\n已写 %s" % args.csv)

    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
