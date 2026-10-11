"""agent 分组记忆：储物盒分组状态（本次运行，跨轮次累积）。

不复用 llm/shape_memory.py。组号 1~6 由程序分配；
模型只表达「归入已有组」或「新建组」，程序负责编号与计数。
"""
import json
import logging
import os

logger = logging.getLogger("wave.vision.agent")


class GroupMemory:
    def __init__(self, max_groups=6, max_items_per_group=4, save_path=None):
        self.max_groups = max_groups
        self.max_items_per_group = max_items_per_group
        self.save_path = save_path
        self.groups = []  # [{id, name, exemplar_path, count}]

    def describe(self):
        """生成注入提示词的分组清单。"""
        if not self.groups:
            return "（尚无分组，这是第一件货物）"
        lines = []
        for g in self.groups:
            if g["count"] >= self.max_items_per_group:
                lines.append(f"第{g['id']}组：{g['name']}（已满 {g['count']} 件，不能再放入）")
            else:
                lines.append(f"第{g['id']}组：{g['name']}（已 {g['count']} 件）")
        return "\n".join(lines)

    def is_full(self):
        return len(self.groups) >= self.max_groups

    def valid(self, gid):
        return any(g["id"] == gid for g in self.groups)

    def group_is_full(self, gid):
        """分组是否已满（达到每盒件数上限）。"""
        for g in self.groups:
            if g["id"] == gid:
                return g["count"] >= self.max_items_per_group
        return False

    def add(self, name, exemplar_path=None):
        """新建一组，返回组 id（1..max_groups）；已满返回 0。"""
        if self.is_full():
            logger.error("储物盒已满(>=%d)，无法新建分组", self.max_groups)
            return 0
        gid = len(self.groups) + 1
        self.groups.append({"id": gid, "name": name, "exemplar_path": exemplar_path, "count": 0})
        self._persist()
        logger.info("新建分组 %d -> %s", gid, name)
        return gid

    def incr(self, gid):
        """分组计数 +1，返回该组当前数量；组不存在或已满返回 0。"""
        for g in self.groups:
            if g["id"] == gid:
                if g["count"] >= self.max_items_per_group:
                    return 0
                g["count"] += 1
                self._persist()
                return g["count"]
        return 0

    def _persist(self):
        if not self.save_path:
            return
        try:
            parent = os.path.dirname(os.path.abspath(self.save_path))
            os.makedirs(parent, exist_ok=True)
            with open(self.save_path, "w", encoding="utf-8") as f:
                json.dump({"groups": self.groups}, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.warning("分组记忆落盘失败: %s", e)
