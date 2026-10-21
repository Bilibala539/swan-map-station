# swan-map-web 部署手册

## 本地启动（开发机）

```powershell
# 1. 首次：编译数据 + 构建前端
python tools/build_data.py          # 知识库文档 → data/versions/v2/*.json + 编号注册表
cd frontend; npm install; npm run build; cd ..

# 2. 启动（局域网可访问）
python server/serve.py --host 0.0.0.0 --port 8090
# 本机: http://127.0.0.1:8090   局域网: http://<本机IP>:8090
```

## 迁移到服务器（零改动）

整站目录 `swan-map-web/` 拷贝到目标机，然后：

1. 目标机装 Python 3.10+（无需 pip 依赖，纯标准库）
2. `python server/serve.py --host 0.0.0.0 --port 8090`
3. 如需常驻：Windows 用 NSSM/计划任务注册服务，Linux 用 systemd——执行命令同上

**为什么零改动**：数据全部在 `data/` 内、纯相对路径；前端构建产物是静态文件；
服务器代码只依赖 `Path(__file__)` 推导的站点根。没有任何绝对路径或本机配置。

## 数据更新（新一轮探索/测绘收口后）

1. 更新源文档：`功能地图.md`（版本头+变更记录）、`states-matrix.md`、BUG 库
2. 改 `tools/build_data.py` 顶部 `VERSION` / `BUILD_DATE`；轮次目录用命令行指定（C 批：源路径已配置化，无需改代码）
3. `python tools/build_data.py --results "F:\SwanApp-Toolchain\results\<新轮次ID>"`（可选 `--knowledge` / `--vault` 覆盖知识库与 vault 路径，默认本机标准位；迁移机上重建用这三个参数指到实际目录即可，编号注册表保证 ID 稳定，新增自动追加）
4. 前端无需重建（数据与产物分离）；刷新浏览器即见新版本

## 公开前检查单（局域网/服务器）

- [ ] 数据脱敏复核：`report.md`、BUG 库中手机号/凭据已脱敏（当前 137****633 形式）
- [ ] 内网 URL（8.129.106.82、network.hivi.com）出现在页面文本中是否可接受
- [ ] 防火墙放行端口；建议反向代理 + 访问控制（Nginx BasicAuth）
- [ ] 服务器磁盘余量 ≥ 2×data 目录（当前 ~80MB，含证据资产）

## 已知边界（一期）

- 版本间条目级 diff 视图未实现（版本冻结与 update log 数据已就位）
- 搜索历史回采/消费明细等零星采证未覆盖（见轮次报告 §6）
- 编号搜索仅支持功能点/模块级（功能区级跳转走左栏树）
