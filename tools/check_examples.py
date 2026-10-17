"""用 ast 解析 SHAPE_EXAMPLES（不导入项目代码，PC 上没有 cv2/serial/openai 也能跑）。"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

src = open("llm/empty_examples.py", encoding="utf-8").read()
tree = ast.parse(src)

# SHAPE_EXAMPLES 里可能引用模块级字符串常量（如 _TOP / _BOTTOM）：
# 先把这些纯字符串常量收集成命名空间，再做一次受限求值（不导入项目代码）。
namespace = {"__builtins__": {}}
shape_node = None
for node in tree.body:
    if not isinstance(node, ast.Assign):
        continue
    names = [target.id for target in node.targets if isinstance(target, ast.Name)]
    if not names:
        continue
    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        for name in names:
            namespace[name] = node.value.value
    elif names == ["SHAPE_EXAMPLES"]:
        shape_node = node.value

assert shape_node is not None, "没找到 SHAPE_EXAMPLES"
examples = eval(compile(ast.Expression(shape_node), "<SHAPE_EXAMPLES>", "eval"), namespace)

const_src = open("shared/const.py", encoding="utf-8").read()
vocab = ast.literal_eval(
    next(n.value for n in ast.parse(const_src).body
         if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SHAPE_CLASSES"))

print(f"示例数 = {len(examples)}（词表 {len(vocab)} 类）")
bad = []
for e in examples:
    ok_f = os.path.isfile(os.path.join("llm/example", e["filename"]))
    ok_s = e["shape"] in vocab
    print(f"  {'OK ' if ok_f and ok_s else '!! '}{e['filename']:32} {e['color']:7} "
          f"{e['shape']:18} 文件={ok_f} 词表内={ok_s}")
    if not (ok_f and ok_s):
        bad.append(e["filename"])

used = {e["filename"] for e in examples}
skip = {"pallet_empty.jpg", "empty_top.jpg", "empty_bottom.jpg"}
files = {f for f in os.listdir("llm/example") if f.lower().endswith(".jpg")}
print()
print("目录里有、但没有被引用的图 :", sorted(files - used - skip) or "无")
print("被引用、但目录里缺文件     :", sorted(used - files) or "无")
print("问题项                    :", bad or "无")

print()
print("形状覆盖：")
shapes = {}
for e in examples:
    shapes.setdefault(e["shape"], []).append(e["color"])
for s in sorted(shapes):
    print(f"  {s:18} {'/'.join(sorted(set(shapes[s])))}")
print("词表里尚无示例的形状:", sorted(set(vocab) - set(shapes)))
