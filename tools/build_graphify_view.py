#!/usr/bin/env python3
"""graphify graph.json → 社区聚合视图（工作站 GraphView「代码社区」数据源）。

把 36k 节点的全量图折叠为 745 个社区节点 + 跨社区边：
  社区节点 = {id: "C<编号>", label: 社区名, size: 成员数, files: 涉及文件数, top: 度数最高的 5 个成员符号}
  聚合边   = {s, t, w}（跨社区连接次数）
用法：
  python tools/build_graphify_view.py [graphify-out目录] [输出json]
默认：core 仓 graphify-out → 同目录 community-graph.json
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"E:\SwanApp\repos\flutter\hivi_network_flutter_core\graphify-out")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else SRC / "community-graph.json"

def main() -> int:
    gpath = SRC / "graph.json"
    if not gpath.exists():
        print(f"[FAIL] {gpath} 不存在——先在仓内跑 graphify extract")
        return 1
    d = json.loads(gpath.read_text(encoding="utf-8"))
    nodes, links = d.get("nodes", []), d.get("links", d.get("edges", []))

    members: dict[int, list] = defaultdict(list)
    for n in nodes:
        members[n.get("community")].append(n)

    # 每社区：名称众数、成员数、文件数、内部度数 Top5 符号
    deg: Counter = Counter()
    for e in links:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    node_by_id = {n["id"]: n for n in nodes}

    comm_nodes = []
    for cid, mem in sorted(members.items()):
        if cid is None:
            continue
        names = Counter(m.get("community_name") or f"Community {cid}" for m in mem)
        label = names.most_common(1)[0][0]
        files = {m.get("source_file") for m in mem if m.get("source_file")}
        top = sorted(mem, key=lambda m: -deg[m["id"]])[:5]
        comm_nodes.append({
            "id": f"C{cid}",
            "label": label,
            "size": len(mem),
            "files": len(files),
            "top": [t["label"] for t in top],
        })
    id2c = {n["id"]: n.get("community") for n in nodes}

    agg: Counter = Counter()
    for e in links:
        cs, ct = id2c.get(e["source"]), id2c.get(e["target"])
        if cs is None or ct is None or cs == ct:
            continue
        key = (min(cs, ct), max(cs, ct))
        agg[key] += 1

    out = {
        "schema": "swan-community/0.1",
        "built_at_commit": d.get("built_at_commit"),
        "sourceNodes": len(nodes),
        "sourceLinks": len(links),
        "nodes": comm_nodes,
        "links": [{"s": f"C{a}", "t": f"C{b}", "w": w} for (a, b), w in sorted(agg.items())],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] 社区 {len(comm_nodes)} | 聚合边 {len(out['links'])} | 输出 {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
