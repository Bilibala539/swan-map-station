"""用例定义同步(用例管理功能):把仓库里的自动化脚本用例导入平台 case_defs。

注(2026-10-13,用例域整合 P4):文字用例权威已在共库——工作站(:8090)与测试
平台(:8080)直连同一 case_defs 表,文字用例在工作站创作/维护,本脚本不再从
knowledge/swan/cases/ YAML 同步文字用例(load_text_cases 保留仅作 dry-run 统计,
实跑默认跳过,--include-text 显式启用)。旧"从 API 拉取"管线已退役。

自动化用例两路来源(都 PUT /api/case-defs/{id} 幂等 upsert):
  1. cases/driver/** YAML(load_auto_cases,frontmatter 锚点规则不变)
  2. tests/flutter/test_*.py 一用例一文件(load_py_auto_cases,新增):
     - case_id = "aw-" + 文件主干,与文件头 `# CASE-ID:` 标记一致
       (pytest_plugin 上报执行历史同源,执行结果按脚本聚合)
     - 挂接:文件头 `# LINKED-CASE: REG-001,REG-002` 显式声明 > docstring
       "回归用例 N[-M]" 锚点自动展开 > 不挂;须在库内精确命中,未命中丢弃并 warn
     - linked_case_id 逗号多值(一个脚本覆盖多条回归用例)

关联规则(平台UI设计规范 8.2):
  - 自动化用例(cases/driver/**): 关联优先级为 frontmatter 显式声明 > 推断 > 不挂
    1. frontmatter `linked_case_id`(完整文字用例编号)显式声明映射,strong
    2. frontmatter `case_id` 显式锚点(strong),回退现有推断 —— source 字段里的
       用例编号后缀匹配文字用例,写入 linked_case_id(如 POS-002 -> LOGIN-L2-POS-002),
       并标 link_strength=weak(弱关联)
  - bug 来源: tags/标题/source 里出现的 SWAN-BUG-YYYYMMDD-NNN 编号写入 source_bug

用法:
  python scripts/sync_cases.py                       # 同步 auto 到 http://127.0.0.1:8080
  python scripts/sync_cases.py --url http://192.168.12.167:8080 --dry-run
  python scripts/sync_cases.py --annotate            # 只补 py 文件头标记行,不写平台
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import yaml

PROJ_ROOT = Path(__file__).parent.parent
CASES_DIR = PROJ_ROOT / "knowledge" / "swan" / "cases"
PY_TESTS_DIR = PROJ_ROOT / "tests" / "flutter"
BUG_PATTERN = re.compile(r"SWAN-BUG-\d{8}-\d+")
ID_TOKEN_PATTERN = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
DRIVER_PREFIX = "knowledge/swan/cases/driver/"
# py 用例:docstring"回归用例 N"或"回归用例 N-M"锚点(与 docs/Swan回归用例AW实现方案.md 映射)
PY_ANCHOR_RE = re.compile(r"回归用例\s*([0-9]+)(?:\s*[-~]\s*([0-9]+))?")
PY_MARK_CASE = "CASE-ID:"
PY_MARK_LINKED = "LINKED-CASE:"
# 平台 YAML 用例执行载体,非独立用例,不入库
PY_EXCLUDE_FILES = {"test_case_driver.py"}


def _post_or_put(url: str, method: str, payload: dict, retries: int = 3) -> dict:
    last_error: Exception | None = None
    # 中文 case_id 直接出现在路径里(aw-冷启动落页 等),路径段须编码为 ASCII
    url = quote(url, safe=":/?&=%")
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method=method,
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except OSError as error:  # 端口瞬时耗尽等可重试错误
            last_error = error
            time.sleep(0.5 * (attempt + 1))
    raise last_error


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    """拆 YAML frontmatter(首行 --- 包裹的块)。返回 (frontmatter字典, 正文文本)。

    无 frontmatter / 解析失败时返回 ({}, 原文),调用方按现状处理。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            try:
                fm = yaml.safe_load("\n".join(lines[1:i])) or {}
            except yaml.YAMLError:
                return {}, raw  # frontmatter 坏了不硬解析,按无锚点走,不阻断同步
            return (fm if isinstance(fm, dict) else {}), "\n".join(lines[i + 1:])
    return {}, raw  # 只有开 --- 没闭合,视作无 frontmatter


def load_text_cases() -> list[dict]:
    """cases/ 下除 driver 外的 YAML,逐条 cases: 列表导入为文字用例。"""
    items: list[dict] = []
    for path in sorted(CASES_DIR.glob("**/*.yaml")):
        rel = path.relative_to(PROJ_ROOT).as_posix()
        if rel.startswith(DRIVER_PREFIX):
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as error:
            print(f"  [warn] YAML 解析失败 {rel}: {error}")
            continue
        file_module = str(doc.get("module") or "").split("(")[0].strip()
        level = doc.get("level")
        level = level[0] if isinstance(level, list) and level else str(level or "")
        for entry in doc.get("cases") or []:
            case_id = str(entry.get("case_id") or "").strip()
            if not case_id:
                continue
            tags = entry.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            tags_text = " ".join(str(t) for t in tags)
            bugs = sorted(set(BUG_PATTERN.findall(
                f"{case_id} {entry.get('name') or ''} {tags_text}")))
            items.append({
                "case_id": case_id,
                "case_type": "text",
                "title": str(entry.get("name") or case_id).strip(),
                "module": file_module,
                "priority": str(entry.get("priority") or level).strip(),
                "source": rel,
                "source_bug": bugs[0] if bugs else "",
                "file_path": rel,
                "content": yaml.dump(entry, allow_unicode=True, sort_keys=False),
                "link_strength": "strong",
            })
    return items


def load_auto_cases(text_cases: list[dict]) -> tuple[list[dict], set[str]]:
    """cases/driver/** 的 YAML 整文件导入为自动化用例。

    关联锚点优先级(M5 二期): frontmatter 显式声明 > 推断 > 不挂。
      1. frontmatter `linked_case_id`(完整文字用例编号)显式声明,strong;
         须在文字用例集合中全名精确命中,未命中/脏值打 [warn] 丢弃声明回退推断
      2. 无显式声明回退现行推断 —— source 路径锁定 + 编号后缀唯一命中,weak
      3. frontmatter `case_id` 仍只作 auto 自身 ID 强锚点,不参与关联推断
    返回 (用例列表, 显式声明命中的 case_id 集合,dry-run 据此标 [explicit])。
    """
    items: list[dict] = []
    explicit_ids: set[str] = set()
    text_ids = {c["case_id"] for c in text_cases}
    for path in sorted(CASES_DIR.glob("driver/**/*.yaml")):
        rel = path.relative_to(PROJ_ROOT).as_posix()
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
        try:
            doc = yaml.safe_load(body) or {}
        except yaml.YAMLError as error:
            print(f"  [warn] YAML 解析失败 {rel}: {error}")
            continue
        if not isinstance(doc, dict):
            doc = {}
        fm_case_id = str(frontmatter.get("case_id") or "").strip()
        case_id = fm_case_id or str(doc.get("case_id") or "").strip()
        if not case_id:
            continue
        # 显式映射声明: key 存在才校验(未声明静默走推断);全名精确命中才采纳
        explicit_linked = ""
        fm_linked = frontmatter.get("linked_case_id")
        if fm_linked is not None:
            cand = str(fm_linked).strip()
            if cand in text_ids:
                explicit_linked = cand
            else:
                print(f"  [warn] {rel}: frontmatter linked_case_id={fm_linked!r} "
                      f"未在文字用例中精确命中,丢弃声明回退推断")
        # 推断(兜底,现行规则): source 里的 YAML 路径锁定来源文件,再用编号后缀匹配;
        # 无路径时全量后缀匹配(唯一命中才挂,避免歧义)
        haystack = " ".join(str(doc.get(k) or "") for k in ("case_id", "title", "source"))
        linked = ""
        path_match = re.search(r"knowledge/[\w/.\-]+\.yaml", haystack)
        candidates = [c for c in text_cases]
        if path_match:
            candidates = [c for c in text_cases
                          if c["file_path"].endswith(path_match.group(0))]
        for token in ID_TOKEN_PATTERN.findall(haystack):
            if "-" not in token:
                continue
            matches = [c["case_id"] for c in candidates
                       if c["case_id"].endswith(token)]
            if len(matches) == 1:
                linked = matches[0]
                break
        if explicit_linked:
            # 显式与推断不一致时以显式为准(推断本就弱语义),打 info 供核查
            if linked and linked != explicit_linked:
                print(f"  [info] {rel}: 显式 linked_case_id={explicit_linked} "
                      f"覆盖推断结果 {linked}")
            linked = explicit_linked
            explicit_ids.add(case_id)
        bugs = sorted(set(BUG_PATTERN.findall(
            f"{doc.get('title') or ''} {doc.get('source') or ''} {raw[:2000]}")))
        items.append({
            "case_id": case_id,
            "case_type": "auto",
            "title": str(doc.get("title") or case_id).strip(),
            "module": str(doc.get("module") or "").strip(),
            "priority": str(doc.get("priority") or "").strip(),
            "source": str(doc.get("source") or "").strip(),
            "source_bug": bugs[0] if bugs else "",
            "file_path": rel,
            "content": raw,
            "linked_case_id": linked,
            # frontmatter 显式锚点(case_id 或 linked_case_id)= strong;纯推断 = weak
            "link_strength": "strong" if (fm_case_id or explicit_linked) else "weak",
        })
    return items, explicit_ids


def _py_case_id(path: Path) -> str:
    """文件 -> auto case_id:"aw-" + 文件主干(test_ 前缀与 .py 去除,中文保留)。"""
    return "aw-" + path.stem.removeprefix("test_")


def _py_docstring_head(src: str) -> str:
    """模块 docstring(首个三引号块)全文;取不到返回空串。"""
    start = src.find('"""')
    if start < 0:
        return ""
    end = src.find('"""', start + 3)
    if end < 0:
        return ""
    return src[start + 3:end]


def _py_parse_marks(path: Path) -> tuple[str, list[str]]:
    """读文件头标记行 -> (CASE-ID 值, LINKED-CASE 列表);无标记返回 ("", [])。"""
    head = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:10])
    case_id = ""
    linked: list[str] = []
    for line in head.splitlines():
        text = line.strip()
        if text.startswith("#") and PY_MARK_CASE in text:
            case_id = text.split(PY_MARK_CASE, 1)[1].strip()
        elif text.startswith("#") and PY_MARK_LINKED in text:
            linked = [t.strip() for t in text.split(PY_MARK_LINKED, 1)[1].split(",") if t.strip()]
    return case_id, linked


def load_py_auto_cases(text_index: dict[str, dict]) -> list[dict]:
    """tests/flutter/test_*.py 一用例一文件 -> auto 用例(2026-10-13 P4 共库挂接)。

    text_index = 库内 text 用例 {case_id: {module, priority}}(export 拉取),
    挂接锚点必须精确命中;module/priority 顺延首个挂接的文字用例。
    挂接来源:文件头 LINKED-CASE: 标记 > docstring"回归用例 N[-M]"锚点展开。
    """
    items: list[dict] = []
    for path in sorted(PY_TESTS_DIR.glob("test_*.py")):
        if path.name in PY_EXCLUDE_FILES:
            continue
        rel = path.relative_to(PROJ_ROOT).as_posix()
        src = path.read_text(encoding="utf-8", errors="replace")
        doc = _py_docstring_head(src)
        mark_case, mark_linked = _py_parse_marks(path)
        case_id = mark_case or _py_case_id(path)
        if mark_case and mark_case != _py_case_id(path):
            print(f"  [warn] {rel}: CASE-ID 标记 {mark_case!r} 与文件名推导 "
                  f"{_py_case_id(path)!r} 不一致,以标记为准")
        linked: list[str] = []
        if mark_linked:
            linked = mark_linked
        else:
            anchor = PY_ANCHOR_RE.search(doc)
            if anchor:
                lo, hi = int(anchor.group(1)), int(anchor.group(2) or anchor.group(1))
                linked = [f"REG-{n:03d}" for n in range(lo, hi + 1)]
        missing = [lid for lid in linked if lid not in text_index]
        if missing:
            print(f"  [warn] {rel}: 挂接 {missing} 未在库内命中,丢弃"
                  f"(保留 {len(linked) - len(missing)} 条)")
            linked = [lid for lid in linked if lid in text_index]
        first = text_index[linked[0]] if linked else {}
        items.append({
            "case_id": case_id,
            "case_type": "auto",
            "title": doc.splitlines()[0].strip().rstrip("。") if doc else case_id,
            "module": str(first.get("module") or ""),
            "priority": str(first.get("priority") or ""),
            "source": "py-tests",
            "source_bug": "",
            "file_path": rel,
            "content": doc.strip(),
            "linked_case_id": ",".join(linked),
            "link_strength": "strong" if linked else "",
        })
    return items


def annotate_py_marks(text_ids: set[str]) -> int:
    """给缺标记的 py 用例文件写回头部两行(2026-10-13 P4):

        # CASE-ID: aw-<文件主干>
        # LINKED-CASE: REG-0xx,...

    LINKED-CASE 从 docstring"回归用例"锚点展开;已有标记的文件跳过。
    返回补写文件数。
    """
    count = 0
    for path in sorted(PY_TESTS_DIR.glob("test_*.py")):
        if path.name in PY_EXCLUDE_FILES:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if PY_MARK_CASE in src[:2000]:
            continue
        case_id = _py_case_id(path)
        anchor = PY_ANCHOR_RE.search(_py_docstring_head(src))
        linked = ""
        if anchor:
            lo, hi = int(anchor.group(1)), int(anchor.group(2) or anchor.group(1))
            reg_ids = [f"REG-{n:03d}" for n in range(lo, hi + 1)]
            hit = [r for r in reg_ids if r in text_ids]
            missed = [r for r in reg_ids if r not in text_ids]
            if missed:
                print(f"  [warn] {path.name}: 锚点 {missed} 未在库内命中,LINKED-CASE 只写命中部分")
            if hit:
                linked = f"# LINKED-CASE: {','.join(hit)}\n"
        lines = src.splitlines(keepends=True)
        lines.insert(0, f"# {PY_MARK_CASE} {case_id}\n" + linked)
        path.write_text("".join(lines), encoding="utf-8", newline="")
        count += 1
        print(f"  + {path.name}: {case_id}" + (f" <- {linked.strip()}" if linked else ""))
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="同步自动化用例定义到平台(共库版)")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--annotate", action="store_true",
                        help="只给 tests/flutter/test_*.py 补 CASE-ID/LINKED-CASE 标记行,不写平台")
    parser.add_argument("--include-text", action="store_true",
                        help="过渡开关:连同 knowledge/swan/cases/ YAML 文字用例一起同步"
                             "(共库后文字用例权威在工作站,默认跳过)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    base = args.url.rstrip("/")

    text_cases = load_text_cases()
    auto_yaml, explicit_ids = load_auto_cases(text_cases)
    print(f"解析到 文字用例 {len(text_cases)} 条(共库后默认不同步), "
          f"YAML 自动化用例 {len(auto_yaml)} 条")

    # 库内 text 用例索引(挂接校验 + module/priority 顺延);export 端点免登录
    text_index: dict[str, dict] = {}
    try:
        exported = _post_or_put(f"{base}/api/case-defs/export", "GET", {})
        for row in exported.get("cases") or []:
            text_index[row.get("case_id") or ""] = {
                "module": row.get("module") or "",
                "priority": row.get("priority") or "",
            }
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
        print(f"  [warn] 拉取库内文字用例失败({error}),py 用例挂接校验跳过(全量保留)")

    if args.annotate:
        count = annotate_py_marks(set(text_index))
        print(f"标记行补写完成:{count} 个文件")
        return 0

    auto_py = load_py_auto_cases(text_index)
    print(f"py 自动化用例 {len(auto_py)} 条"
          f"(挂接 {sum(1 for c in auto_py if c['linked_case_id'])} 条)")
    auto_cases = auto_yaml + auto_py

    if args.dry_run:
        for case in text_cases + auto_cases:
            link = f" -> {case['linked_case_id']}" if case.get("linked_case_id") else ""
            if case["case_id"] in explicit_ids:
                link += " [explicit]"
            bug = f" [bug:{case['source_bug']}]" if case['source_bug'] else ""
            strength = "" if case["case_type"] == "text" else f" [{case['link_strength']}]"
            mark = "" if case["case_type"] != "text" or args.include_text else "[跳过] "
            print(f"{mark}[{case['case_type']}{strength}] {case['case_id']}: {case['title']}{link}{bug}")
        weak = sum(1 for c in auto_cases if c.get("link_strength") == "weak")
        print(f"弱关联(auto 无 frontmatter 锚点) {weak} 条,建议在 YAML 补 case_id frontmatter")
        return 0

    # 共库版:文字用例默认跳过(权威在工作站),--include-text 显式启用才写
    to_sync = auto_cases + (text_cases if args.include_text else [])
    ok = failed = 0
    for case in to_sync:
        try:
            _post_or_put(f"{base}/api/case-defs/{case['case_id']}", "PUT", case)
            ok += 1
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            failed += 1
            print(f"  [fail] {case['case_id']}: {error}")
    print(f"同步完成: 成功 {ok}, 失败 {failed}"
          + ("(文字用例未同步:共库权威在工作站,--include-text 可显式启用)"
             if not args.include_text else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
