"""统一表格交互组件 v1.1(runs/dl)结构测试。

页面交互逻辑全部在 dashboard/page.html 内联 JS,测试环境无浏览器,这里按
《平台列表列设计规范》3.x/4.1/4.2 的验收点对页面源码做结构断言,
锁定「撤行内按钮/解除 pin/三态排序/列设置守则/页大小三档/列偏好键」等关键行为不回退。
"""
from __future__ import annotations

import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "page.html"


def page_source() -> str:
    return PAGE.read_text(encoding="utf-8")


def script_source() -> str:
    """提取 page.html 内联 <script> 块(末块),与浏览器实际执行的 JS 一致。"""
    html = page_source()
    match = re.search(r"<script>\n(.*)\n  </script>", html, re.S)
    assert match, "page.html 内联 script 块未找到"
    return match.group(1)


# ---------- 第一张卡:执行会话 runs(列规范 4.1) ----------

def test_runs_row_detail_button_removed():
    """P0:行内「查看详情」icon-btn 撤销——行点击已开详情抽屉,按钮冗余且表头/行错位。"""
    html = page_source()
    assert 'title="查看详情"' not in html
    # 深链复核:全站无 hash 路由,行内按钮无外部引用,撤除无深链破坏
    assert "location.hash" not in html
    assert "hashchange" not in html


def test_runs_unpinned_last_column():
    """P0:runs 末列是开始时间(数据列)非操作列 → 解除吸右 pin(列规范 3.2 判据)。"""
    script = script_source()
    assert "['runs','tw-runs',true]" not in script  # 已撤出 makeTableResizable 名单
    assert "fixedLast: false" in script  # 统一组件配置显式声明不吸右


def test_runs_sortable_columns_per_spec():
    """P1:可排列按 §4.1 裁决=通过率/用例数/耗时/开始时间;平台/包体/设备/状态不排。"""
    script = script_source()
    runs_block = re.search(r"const RUNS_COLUMNS = \[(.*?)\];", script, re.S).group(1)
    for key in ("rate", "cases", "duration", "started"):
        entry = re.search(rf"key: '{key}',[^\n]*", runs_block).group(0)
        assert "sortable: true" in entry, f"{key} 应可排序"
    for key in ("platform", "apk", "device", "status"):
        entry = re.search(rf"key: '{key}',[^\n]*", runs_block).group(0)
        assert "sortable" not in entry, f"{key} 不应排序"
    # 用例数/耗时两 th 补 num(§4.1 P1);通过率原本就 num
    for key in ("rate", "cases", "duration"):
        entry = re.search(rf"key: '{key}',[^\n]*", runs_block).group(0)
        assert "num: true" in entry, f"{key} 应为数字列"


def test_runs_sort_keys_semantics():
    """排序键按列语义:时间列用时间戳、数值列按数值(列规范 3.3)。"""
    script = script_source()
    assert "started: r => Date.parse(r.started_at || '') || 0" in script
    assert "cases: r => Number(r.total_cases) || 0" in script
    assert "duration: r => Number(r.duration_seconds) || 0" in script


def test_runs_column_prefs_key_and_migration():
    """列偏好 localStorage 键 tw-runs-cols-v1(列规范 3.4);旧拖宽档 tw-runs 一次性迁移。"""
    script = script_source()
    assert "RUNS_COLS_KEY = 'tw-runs-cols-v1'" in script
    assert "'tw-runs', RUNS_COLUMNS.length" in script  # 旧档按默认列序迁移


# ---------- 公共组件守则(列规范 3.1/3.3/3.4) ----------

def test_shared_component_functions_exist():
    """runs/dl 共用同一套实现:三态排序/列加载保存/表头渲染与事件/列设置弹层/弹性补位。"""
    script = script_source()
    for fn in ("twVisibleCols", "twLoadCols", "twSaveCols", "twSortItems",
               "twFitLast", "twRenderHead", "twBindThEvents", "twRenderColsPop", "twBindColsBtn"):
        assert f"function {fn}(" in script, fn


def test_three_state_sort_cycle():
    """三态循环:点一次升序 → 再点降序 → 第三点恢复默认业务序(指示器隐没)。"""
    script = script_source()
    assert "cfg.sortDir = (cfg.sortDir + 1) % 3" in script


def test_cols_pop_rules():
    """列设置守则:固定列不在名单、至少留一列、恢复默认=清存档(列规范 3.4)。"""
    script = script_source()
    pop_block = re.search(r"function twRenderColsPop\(cfg\) \{(.*?)\n    \}", script, re.S).group(1)
    assert "!c.fixed" in pop_block  # 固定列不进名单
    assert "cfg.cols.some(c => c.visible)" in pop_block  # 至少留一列
    assert "removeItem(cfg.colsKey)" in pop_block  # 恢复默认=removeItem
    assert "恢复默认列" in pop_block


def test_width_clamp_and_persist_on_drop():
    """列宽钳制 56-1200,松手即写偏好档(列规范 3.1)。"""
    script = script_source()
    assert "Math.min(Math.max(startW + ev.clientX - startX, 56), 1200)" in script
    assert "twSaveCols(cfg.cols, cfg.colsKey);  // 松手即写" in script


# ---------- 第二张卡:包管理 dl(列规范 4.2) ----------

def test_dl_pagesize_unified():
    """P0:页大小三档统一 10/35/80,默认 35(原 30)。"""
    html = page_source()
    entry = re.search(r'id="dl-pagesize"[^>]*>(.*?)</select>', html, re.S).group(1)
    assert '<option>10</option><option selected>35</option><option>80</option>' in entry
    script = script_source()
    assert "dlPageSize = 35" in script  # 默认值与回退值都不再是 30


def test_dl_footer_in_card():
    """分页器迁入表格卡 footer(cd-tfoot 标准件):信息条+弹性空+分页器,≤30% 数据隐藏。"""
    html = page_source()
    tfoot = re.search(r'<div class="cd-tfoot">\s*<span class="meta" id="dl-page-info">(.*?)</section>', html, re.S).group(1)
    assert 'id="dl-pager-box"' in tfoot and 'id="dl-prev"' in tfoot and 'id="dl-next"' in tfoot
    script = script_source()
    assert "$('dl-pager-box').hidden = items.length <= Math.max(1, Math.round(dlPageSize * 0.3))" in script


def test_dl_column_prefs_and_sort():
    """dl 列偏好键 tw-dl-cols-v1(旧档 tw-dl 一次性迁移);三态排序挂打包时间(时间戳)。"""
    script = script_source()
    assert "DL_COLS_KEY = 'tw-dl-cols-v1'" in script
    assert "'tw-dl', DL_COLUMNS.length" in script
    assert "DL_SORT_VAL = { time: a => Date.parse(dlTime(a) || '') || 0 }" in script
    time_entry = re.search(r"key: 'time',[^\n]*", script).group(0)
    assert "sortable: true" in time_entry
    actions_entry = re.search(r"key: 'actions',[^\n]*", script).group(0)
    assert "sortable" not in actions_entry and "fixed: true" in actions_entry  # 操作列不排序、恒显恒最右


def test_dl_keeps_actions_pinned():
    """dl 末列是操作列(4 枚 icon-btn)→ 保留吸右 pin(列规范 3.2 判据)。"""
    script = script_source()
    assert "fixedLast: true" in script
    assert "['dl-rows','tw-dl',true]" not in script  # 已撤出 makeTableResizable 名单,改走统一组件
    html = page_source()
    assert 'id="dl-cols-btn"' in html and 'id="dl-cols-pop"' in html  # 列设置标准件入口


# ---------- 第四张卡:流水线 pl / 最近执行 pljob(列规范 2.3/2.4/4.3/4.4) ----------

def test_pl_columns_per_spec():
    """pl 列定义:8 列齐全,仅下次执行可排(§2.3),操作列固定恒最右(3.2)。"""
    src = script_source()
    assert re.search(r"const PL_COLUMNS = \[", src)
    for key in ["name", "target", "build", "sched", "lastrun", "nextrun", "enabled", "actions"]:
        assert re.search(rf"key: '{key}',", src), key
    assert re.search(r"key: 'nextrun',[^\n]*sortable: true", src)
    assert re.search(r"key: 'actions',[^\n]*fixed: true", src)
    # 其他列不得可排(§2.3 只有下次执行一列)
    for key in ["name", "target", "build", "sched", "lastrun", "enabled"]:
        m = re.search(rf"key: '{key}',[^\n]*", src)
        assert "sortable" not in m.group(0), key


def test_pl_prefs_key_and_footer():
    """列偏好键 tw-pipe-cols-v1+旧档 tw-plcfg 迁移(3.4);footer 收进卡片,页大小 10/35/80 默认 35(3.5)。"""
    src = script_source()
    html = page_source()
    assert "const PL_COLS_KEY = 'tw-pipe-cols-v1';" in src
    assert "'tw-plcfg'" in src  # 旧键一次性迁移
    assert re.search(r'id="pl-pagesize"[^>]*>\s*<option>10</option><option selected>35</option><option>80</option>', html)
    assert 'id="pl-page-info"' in html and 'id="pl-pager-box"' in html
    assert "let plPage = 1, plPageSize = 35;" in src
    assert re.search(r"\$\('pl-pager-box'\)\.hidden = items\.length <= Math\.max\(1, Math\.round\(plPageSize \* 0\.3\)\)", src)
    # 至少留一列守则 + 固定列不入弹层名单(复用 twRenderColsPop,断言 pl 接线)
    assert "twRenderColsPop(plCfg)" in src and "twBindThEvents(plCfg)" in src


def test_pljob_unpinned_and_sort():
    """pljob 末列 Jenkins 任务号是数据列 → 解除 pin(§4.4 P1);创建时间可排(§2.4);键 tw-pipejob-cols-v1+旧档 tw-pljob。"""
    src = script_source()
    assert re.search(r"const pljobCfg = \{[\s\S]{0,400}?fixedLast: false", src)
    assert re.search(r"key: 'created',[^\n]*sortable: true", src)
    assert "const PLJOB_COLS_KEY = 'tw-pipejob-cols-v1';" in src
    assert "'tw-pljob'" in src
    assert "twRenderColsPop(pljobCfg)" in src and "twBindThEvents(pljobCfg)" in src
    # 取消按钮内联是 6.3 已裁定例外,必须保留
    assert 'data-act="cancel"' in src


def test_pl_fitkey_elastic_column():
    """弹性列:pl=名称、pljob=执行目标(fitKey,§2.3/2.4 默认宽"弹性");twFitLast 读取 cfg.fitKey。"""
    src = script_source()
    assert "fitKey: 'name'" in src and "fitKey: 'target'" in src
    assert re.search(r"function twFitLast\(cfg, dragKey\) \{[\s\S]{0,300}?cfg\.fitKey", src)


def test_pipeline_wiring_migrated():
    """pl/pljob 撤出旧 makeTableResizable 名单;resize 补位接入 twFitLast。"""
    src = script_source()
    assert "'pl-rows','tw-plcfg'" not in src and "'pl-job-rows','tw-pljob'" not in src
    assert "twFitLast(plCfg, null)" in src and "twFitLast(pljobCfg, null)" in src
    # 列设置弹层接入点外收起名单
    assert "['pl-cols-pop', 'pl-cols-btn']" in src and "['pljob-cols-pop', 'pljob-cols-btn']" in src


def test_no_hash_dollar_calls():
    """10-15 生产事故防线:$=getElementById 裸 id 约定,全脚本禁止 $('#…') 井号调用。"""
    src = script_source()
    assert not re.findall(r"\$\(\s*['\"` ]{0,2}#", src), "出现井号调用($=getElementById 应传裸 id)"


# ---------- 任务 013-B:短信验证码原生页签(iframe 版退役) ----------

def test_otpc_iframe_section_retired():
    """013-B:iframe 版 section 退役——gw-frame/gw-offline/captcha-card/loadGateway 全部移除,nav 入口保留。"""
    src = script_source()
    page = page_source()
    assert 'gw-frame' not in src and 'gw-frame' not in page
    assert 'gw-offline' not in src and 'gw-offline' not in page
    assert 'captcha-card' not in page and 'loadGateway' not in src
    assert 'data-page="captcha">短信验证码' in page  # nav 入口同名保留
    assert '<iframe' not in page  # 页面不再有任何 iframe


def test_otpc_columns_and_prefs():
    """013-B:列=手机号/验证码/到期时间/状态/来源;偏好键 tw-otpc-cols-v1;纯查看不 pin;弹性列=来源。"""
    src = script_source()
    assert "const OTPC_COLS_KEY = 'tw-otpc-cols-v1';" in src
    assert "key: 'expire'" in src and re.search(r"key: 'expire',[^\n]*sortable: true", src)
    assert re.search(r"const otpcCfg = \{[\s\S]{0,400}?fixedLast: false", src)  # 无操作列不 pin
    assert "fitKey: 'source'" in src
    assert '操作' not in re.search(r"const OTPC_COLUMNS = \[[\s\S]*?\];", src).group(0)  # 纯查看无操作列
    assert "twRenderColsPop(otpcCfg)" in src and "twBindThEvents(otpcCfg)" in src
    assert "['otpc-cols-pop', 'otpc-cols-btn']" in src  # 点外收起名单
    assert "twFitLast(otpcCfg, null)" in src  # resize 弹性补位接线


def test_otpc_endpoints_and_poll():
    """013-B:数据直连网关(recent 5s 轮询/refresh-all 全部刷新),失败提示「取码服务不可达」,轮询仅激活期间。"""
    src = script_source()
    assert "gatewayUrl + '/api/test/otp/recent?limit=100'" in src
    assert "gatewayUrl + '/api/test/otp/refresh-all', { method: 'POST' }" in src
    assert '取码服务不可达' in src
    assert 'setInterval(loadOtpCodes, 5000)' in src
    assert "if (btn.dataset.page === 'captcha') otpcStart(); else otpcStop();" in src
    assert "available: ['有效', 'passed']" in src and '已过期' in src and '已消费' in src
