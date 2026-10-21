#!/usr/bin/env python3
"""swan 知识库 vault → wikilink 图谱（GraphView「知识图谱」模式数据源，Obsidian 同款形态）。

节点 = vault 内 md 文件（按目录/前缀分类：页面/feature 枢纽/flow/索引）
边   = [[wikilink]]（解析到文件名 stem，悬空链接丢弃）
用法：
  python tools/build_vault_graph.py [vault目录] [输出json]
默认：中心机快照（G 盘）→ 同目录 vault-graph.json
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"G:\SwanApp-Data\snapshots\swan-kb-remote-20260914\knowledge\swan")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else SRC / "vault-graph.json"

WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")


def classify(rel: str, stem: str) -> str:
    if rel.startswith("pages/"):
        return "页面"
    if rel.startswith("digest/features/"):
        return "功能枢纽"
    if rel.startswith("digest/flows/"):
        return "流程"
    if "索引" in stem:
        return "索引"
    return "元文档"


def main() -> int:
    if not SRC.exists():
        print(f"[FAIL] vault 目录不存在: {SRC}")
        return 1
    files = sorted(SRC.rglob("*.md"))
    # stem → 节点 id（vault 内 wikilink 以文件名互链；重名用首个）
    stem2id: dict[str, str] = {}
    nodes = []
    for f in files:
        rel = f.relative_to(SRC).as_posix()
        stem = f.stem
        nid = rel
        stem2id.setdefault(stem, nid)
        stem2id.setdefault(stem.replace("swan_", "", 1), nid)  # 宽松匹配：swan_ 前缀可省
        nodes.append({
            "id": nid,
            "name": stem,
            "group": classify(rel, stem),
            "rel": rel,
        })
    id_set = {n["id"] for n in nodes}

    edges = []
    deg: Counter = Counter()
    seen_pairs = set()
    unresolved: Counter = Counter()
    for f in files:
        rel = f.relative_to(SRC).as_posix()
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in WIKI_RE.findall(text):
            target = m.split("|")[0].split("#")[0].replace(".md", "").strip()
            tid = stem2id.get(target)
            if tid is None or tid == rel:
                if tid != rel:
                    unresolved[target] += 1
                continue
            key = (min(rel, tid), max(rel, tid))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            edges.append({"s": rel, "t": tid})
            deg[rel] += 1
            deg[tid] += 1

    for n in nodes:
        n["deg"] = deg[n["id"]]

    out = {
        "schema": "swan-vault-graph/0.1",
        "source": str(SRC),
        "nodes": nodes,
        "links": edges,
        "unresolved": dict(unresolved.most_common(10)),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    by_group = Counter(n["group"] for n in nodes)
    print(f"[OK] 节点 {len(nodes)} {dict(by_group)} | 边 {len(edges)} | 悬空链接 {sum(unresolved.values())} | {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
