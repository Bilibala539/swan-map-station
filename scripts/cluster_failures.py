"""
聚合 35 条失败的根因。
输出按 '错误签名' 聚类的报告。
"""
import datetime
import json
import re
from collections import defaultdict
from pathlib import Path

today = datetime.date.today().isoformat()
data = json.load(open(rf'E:\UIAutotest\logs\regression_failures_{today}.json', encoding='utf-8'))

# 抽 root cause（最后一行 ERROR/FAILED）
ROOT_RE = re.compile(r'^(?:ERROR|FAILED) [^\s]+ - (.+)$', re.MULTILINE)
ASSERT_RE = re.compile(r'AssertionError[:\s]*(.+)|assert ([^\n]+)')


def extract_root(stdout):
    matches = ROOT_RE.findall(stdout or "")
    return matches[-1].strip() if matches else "(no root extracted)"


groups = defaultdict(list)
for f in data:
    root = extract_root(f.get('stdout_tail', ''))
    # 归一化：去掉路径/具体文件名
    sig = re.sub(r"图片[^']*", "图片*", root)
    sig = re.sub(r"\d{4,}", "<NUM>", sig)
    groups[sig].append({
        'row': f['row'],
        'test_file': f['test_file'],
        '测试点': f.get('测试点', ''),
        'duration': f.get('duration', 0),
        'py_path': f.get('py_path', ''),
        'root': root,
    })

# 排序: 失败用例数倒序
sorted_groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))

out = [f'# 失败用例聚类报告 — 共 {len(data)} 条失败  / {len(groups)} 类根因\n']
for sig, lst in sorted_groups:
    out.append(f'## [{len(lst)} 条] {sig}\n')
    for c in lst:
        out.append(f'- row{c["row"]} `{c["test_file"]}` — 测试点: {c["测试点"]}')
        out.append(f'  py: {c["py_path"]}')
        out.append(f'  raw: {c["root"][:200]}')
    out.append('')

Path(rf'E:\UIAutotest\logs\fail_cluster_{today}.md').write_text("\n".join(out), encoding='utf-8')
print(f'共 {len(data)} 条失败，{len(groups)} 类根因，写入 logs/fail_cluster_{today}.md')
print()
for sig, lst in sorted_groups:
    print(f'  [{len(lst):2}] {sig[:120]}')
