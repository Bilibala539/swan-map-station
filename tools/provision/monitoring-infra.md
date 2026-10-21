# 监控栈安装清单 v1(167+176,Prometheus+Node Exporter+Grafana,2026-09-21)

> 用途:部署计划 §7.5 拍板栈的安装侧备料——**采集存储用标准件,展示进平台**(平台"机器监控"页签
> 由平台模块另做,本清单不含);Grafana :3000 作独立深度大盘。
> 身份约定:apt/ufw/systemctl 等系统操作以 **sudo** 执行;Grafana 改密/数据源为浏览器操作;
> .env.local 编辑在 167(ssh autotest 或主会话)。服务进程用户由包自建(prometheus/grafana
> 系统用户),**不改成 autotest**(最小化,不动包的默认安全边界)。
> **最小化边界:只装本清单三件;不装 alertmanager/pushgateway/cadvisor,不装 Grafana 插件,不配 SSO**。
> apt 口径:Prometheus/Node Exporter 走 Ubuntu 官方仓库(系统源应已配国内镜像,`apt policy` 确认);
> Grafana 走 TUNA 镜像源(与 jenkins-setup.md 同口径),官方源/itarball 备选见 §3.4。

## 1. Node Exporter(167+176 两台都做)

```bash
sudo apt-get install -y prometheus-node-exporter   # 24.04 官方仓库自带
systemctl is-enabled prometheus-node-exporter       # enabled
systemctl is-active  prometheus-node-exporter       # active
curl -s localhost:9100/metrics | grep -c '^node_'   # 大量输出(>100),有 node_cpu_seconds_total 等
```

**176 的 ufw:仅对 167 放行 9100**(确切命令;167 上 9100 只被本机 Prometheus 抓,不放行):

```bash
# 在 176 执行
sudo ufw allow from 192.168.12.167 to any port 9100 proto tcp comment 'node-exporter scraped by 167'
sudo ufw status numbered | grep 9100                # 仅一条,来源 192.168.12.167
```

注:ufw 若尚未启用(部署计划 §3 统一启用),规则先预置,enable 后生效——从 167 测
`curl -s --max-time 3 http://192.168.12.176:9100/metrics | head -1` 通、从其他机不通为最终标准。

## 2. Prometheus(仅 167)

```bash
sudo apt-get install -y prometheus
systemctl is-enabled prometheus && systemctl is-active prometheus   # enabled + active
```

**/etc/prometheus/prometheus.yml 整文件替换为如下内容**(sudo 编辑;三 job:自监控+两台 node,
间隔 1s(2026-09-22 由 15s 上调,见勘误 v1.2);retention 用包默认 15d 不动,`systemctl cat prometheus` 无 retention 参数即默认):

```yaml
global:
  scrape_interval: 1s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["127.0.0.1:9090"]
  - job_name: node-167
    static_configs:
      - targets: ["127.0.0.1:9100"]
  - job_name: node-176
    static_configs:
      - targets: ["192.168.12.176:9100"]
```

重启并验证(9100/9090 均**仅本机**,ufw 不放行、平台代理走 localhost):

```bash
promtool check config /etc/prometheus/prometheus.yml   # SUCCESS
sudo systemctl restart prometheus
systemctl status prometheus --no-pager                 # active (running)
curl -s 'http://localhost:9090/api/v1/query?query=up' | grep -c '"job":"node-'   # 2(两台都在抓)
# 目检返回 JSON:node-167 与 node-176 两条,对应 value 均为 1(启动后约 15-30s 内就绪)
```

**9090 端口纪律**:确认 ufw 无 9090 放行规则(`sudo ufw status | grep 9090` 应无输出);
_targets 页仅经 ssh 隧道查看:`ssh -L 9090:127.0.0.1:9090 192.168.12.167` 后开
http://localhost:9090/targets,两 node 目标绿色 UP。

## 3. Grafana(仅 167)

### 3.1 apt 源接入(TUNA 镜像,国内首选)

```bash
curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/grafana/gpg.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/grafana-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/grafana-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/grafana/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt-get update    # 无 NO_PUBKEY/404 警告;若 404 按 TUNA 帮助页 grafana 条目校正路径,或换 3.4 备选
sudo apt-get install -y grafana     # OSS 版
sudo systemctl enable --now grafana-server
systemctl is-active grafana-server  # active
```

### 3.2 端口与首登

```bash
sudo ufw allow 3000/tcp comment 'grafana ui lan'    # 对局域网放行(不限源,与 9090 纪律相反)
sudo ufw status | grep 3000
```

浏览器开 http://192.168.12.167:3000 → 默认账号 admin/admin → **首登强制改密**:
新密码记入 **167:/opt/uiautotest/.env.local 新键 `GRAFANA_ADMIN_PASSWORD=<明文>`**
(与 JENKINS_* 同目录同纪律,不进命令行明文/日志/git)。

### 3.3 数据源与最小验证

1. Connections → Data sources → Add data source → **Prometheus** → URL `http://localhost:9090`
   → Save & Test(绿 "Successfully queried the Prometheus API")
2. Explore → 选该数据源 → 查询 CPU 使用率(两机对比曲线):

```
100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)
```

legend 按 instance 分组,node-167:9100 与 node-176:9100 两条曲线实时可画即通过。

### 3.4 备选:apt 源不通时用 tarball(仅此场景,恢复原状原则下不双装)

```bash
# 版本号以 https://grafana.com/grafana/download 为准(国内直连慢属正常,分步下载)
curl -fLO https://dl.grafana.com/oss/release/grafana_11.6.3_linux_amd64.tar.gz
sudo tar -xzf grafana_11.6.3_linux_amd64.tar.gz -C /opt    # 解出 /opt/grafana-v11.6.3
sudo mv /opt/grafana-v11.6.3 /opt/grafana
sudo chown -R autotest:autotest /opt/grafana
```

systemd unit `/etc/systemd/system/grafana-server.service`:

```ini
[Unit]
Description=Grafana (tarball fallback)
After=network.target

[Service]
User=autotest
ExecStart=/opt/grafana/bin/grafana server --homepath=/opt/grafana --config=/opt/grafana/conf/defaults.ini
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now grafana-server
```

后续 3.2/3.3 步骤完全一致(端口/改密/.env.local/数据源)。

## 4. 验证清单(全部通过才算监控栈就绪)

| # | 项 | 命令/操作 | 期望 |
|---|---|---|---|
| 1 | node exporter 两台自启 | `systemctl is-enabled prometheus-node-exporter`(167/176) | enabled+active |
| 2 | 9100 metrics 冒烟 | `curl -s localhost:9100/metrics \| grep -c '^node_'`(两台) | >100 |
| 3 | 176 ufw 收口 | `sudo ufw status numbered`(176) | 9100 仅来源 192.168.12.167 |
| 4 | 167 ufw 纪律 | `sudo ufw status \| grep -E '9090\|9100'`(167) | 无输出(仅 3000 放行) |
| 5 | Prometheus 抓取 | `curl -s 'http://localhost:9090/api/v1/query?query=up'`(167) | node-167/node-176 两条 up=1 |
| 6 | targets 页 | ssh 隧道 http://localhost:9090/targets | 两 node 目标 UP(绿) |
| 7 | Grafana 登录 | http://192.168.12.167:3000 改密后重登 | 进入首页 |
| 8 | 数据源 | Save & Test | Successfully queried |
| 9 | CPU 曲线 | §3.3 PromQL | 两实例曲线实时可画 |
| 10 | 密码落位 | `grep -c '^GRAFANA_ADMIN_PASSWORD=' /opt/uiautotest/.env.local`(167) | 1 |

**本清单外(依赖项,不属于安装侧)**:平台"机器监控"页签 = 平台模块在 server.py 加
/api/metrics/* 代理(仅 localhost:9090)+page.html 新页签(部署计划 §7.5 第四行);
代理通后再补验"167 页签代理通"一条——页签能画两机 CPU/内存/磁盘/网络实时图即为端到端闭环。

## 实装勘误 v1.1(2026-09-21 实装回填)

1. **TUNA grafana 的 gpg.key 404**(与 jenkins 同病)但 dists 元数据在;稳妥起见直接拉 pool
   .deb:`https://mirrors.tuna.tsinghua.edu.cn/grafana/apt/pool/main/g/grafana/grafana_12.1.0_amd64.deb`
   (目录列表取最新)+ `apt-get install -y /tmp/xxx.deb`。prometheus/node-exporter 走 Ubuntu
   官方仓库无障碍。
2. **grafana-cli 改密必须以 grafana 用户跑**(root 跑会把 /var/lib/grafana 的 sqlite 弄成
   root 属主,服务起不来 "unable to open database file: permission denied")。已踩坑后修复:
   `chown -R grafana:grafana /var/lib/grafana /var/log/grafana` 后 start 即愈;新装机直接
   `sudo -u grafana grafana-cli --homepath /usr/share/grafana admin reset-admin-password <pw>`。
3. Grafana 健康检查 sleep 8s 不够(首启建库慢),改 15s 或轮询 /api/health 至 200。
4. 密码落 .env.local 键 GRAFANA_ADMIN_PASSWORD;数据源用 provisioning 文件
   (/etc/grafana/provisioning/datasources/prometheus.yaml)免浏览器配置,与 §3.3 浏览器口径二选一。
5. 实测终态:prometheus targets 三条全 up(node-167=127.0.0.1:9100/node-176=192.168.12.176:9100
   /prometheus 自监控);grafana :3000 health 200;ufw 167 增放 3000。

## 实装勘误 v1.2(2026-09-22 采集提速)

6. 用户要求两机采集 1s 一次:167 `/etc/prometheus/prometheus.yml` 全局 `scrape_interval`
   15s→1s(改前备份 .bak-1s,promtool SUCCESS 后 restart,targets 全 up)。两机规模代价:exporter
   单次抓取毫秒级、每机每秒 1 次,TSDB 入库量×15 仍为个位数 GB/天级,15d 保留期可承受;
   页面 summary 轮询同步 5s→1s(page.html MC_POLL_MS),数字粒度与页面刷新对齐 1s。

## 实装勘误 v1.3(2026-09-22 CPU 型号展示)

7. 监控页需展示 CPU 型号(node_exporter 标准采集器无此指标):两机 textfile collector 各落
   `/var/lib/prometheus/node-exporter/cpu_model.prom`(lscpu Model name 生成,静态不变),
   指标 `node_cpu_model_info{model=...}`(167=AMD Ryzen 7 4700G,176=i5-13400F);
   server /api/metrics/summary 增 cpu_model 字段,前端 CPU 格"核数 · 型号"展示。
