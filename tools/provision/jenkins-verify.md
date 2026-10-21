# Jenkins 装后验证单 v2(master 167 + 节点 176,Linux,2026-09-21)

> 用途:jenkins-setup.md 完成后逐项打勾;全部通过才允许触发真构建。
> 凭据纪律:**新管理员密码/API token 只写 167:/opt/uiautotest/.env.local,不进命令行明文/日志/git**。

## 1. API Token 生成(167 master)

UI 路线:Manage Jenkins → Users → admin → Security → API Token → Add new Token(名称如
`platform-call`)→ Generate → **立即复制**(仅显示一次)。

写入 **167** /opt/uiautotest/.env.local(更新以下键):

```ini
JENKINS_ADMIN_PASSWORD=<新管理员密码>
JENKINS_USER=admin
JENKINS_API_TOKEN=<新 token>
JENKINS_BASE_URL=http://127.0.0.1:8082
JENKINS_PUBLIC_URL=http://192.168.12.167:8082
```

## 2. 基础验证(token 冒烟,在 167)

```bash
token=$(grep '^JENKINS_API_TOKEN=' /opt/uiautotest/.env.local | cut -d= -f2-)
curl -s -o /dev/null -w '%{http_code}\n' -u "admin:$token" http://127.0.0.1:8082/api/json     # 200
curl -s -u "admin:$token" "http://127.0.0.1:8082/pluginManager/api/json?tree=plugins[shortName]" \
  | grep -o '"git"'    # 有输出(git 插件在)
curl -s -u "admin:$token" "http://127.0.0.1:8082/computer/build-176/api/json?tree=offline"    # {"offline":false}
```

服务与端口:

```bash
systemctl is-active jenkins        # active
ss -tlnp | grep 8082               # LISTEN(autotest)
```

## 3. 任务与参数核对(不触发构建,在 167)

```bash
token=$(grep '^JENKINS_API_TOKEN=' /opt/uiautotest/.env.local | cut -d= -f2-)
curl -s -u "admin:$token" "http://127.0.0.1:8082/job/swan-build-android/config.xml" | grep -E '<name>|<assignedNode>'
```

**期望 4 个参数且与平台参数字典键完全一致**(server.py /api/builds 拼装侧:
BRANCH/SERVER_TYPE/COMMIT/NOTES),且任务钉扎 build-176:

| name | defaultValue |
|---|---|
| BRANCH | dev |
| SERVER_TYPE | test |
| COMMIT | (空) |
| NOTES | (空) |
| assignedNode | build-176(canRoam=false) |

### 176 会话依赖预检(构建实际运行视角,ssh 到 176 执行,任一缺失先补)

```bash
/opt/uiautotest/.venv/bin/python --version        # Python 3.12(PEP 668:不用系统 python3)
git --version                                     # agent 非登录 shell PATH 可见
grep -c '^GITLAB_BOT_USER=\|^GITLAB_BOT_PASSWORD=' /opt/uiautotest/.env.local   # 2
grep '^JAVA_HOME=\|^ANDROID_HOME=\|^FLUTTER_HOME=' /opt/uiautotest/.env.local
# 三行必须为 /opt/build-env/jdk-17 / /opt/build-env/android / /opt/build-env/flutter
# ——脚本内置缺省是 Windows 路径(C:\build-env),不覆盖则 flutter 直接找不到 SDK
grep '^DASHBOARD_URL=' /opt/uiautotest/.env.local # http://192.168.12.167:8080(缺省 127.0.0.1 在 176 上没有平台)
```

## 4. .env.local 两机分工(对照表)

| 键 | 167(平台机) | 176(构建机) |
|---|---|---|
| JENKINS_ADMIN_PASSWORD/JENKINS_USER/JENKINS_API_TOKEN/JENKINS_BASE_URL/JENKINS_PUBLIC_URL | **全套**(BASE_URL=http://127.0.0.1:8082/PUBLIC_URL=http://192.168.12.167:8082) | 不需要 |
| GITLAB_BOT_USER/GITLAB_BOT_PASSWORD | 需要(平台仓克隆) | **需要**(app/core 仓克隆,打包凭据) |
| DASHBOARD_URL | http://127.0.0.1:8080 | **http://192.168.12.167:8080**(跨机登记必配) |
| JAVA_HOME/ANDROID_HOME/FLUTTER_HOME | 不需要 | **必须**(/opt/build-env/…,覆盖 Windows 内置缺省) |
| FLUTTER_STORAGE_BASE_URL/PUB_HOSTED_URL | 不需要 | 可不写(脚本内置缺省即国内镜像) |
| SWAN_PHONE/SWAN_PASSWORD/ZENTAO_* | 需要(SWAN_PHONE=13555555555) | 不需要 |

## 5. 触发 dry-run(两档,在 167 UI 操作)

**档一:环境自检(推荐先做,不真打包)**——Build with Parameters 触发 `BRANCH=dev & SERVER_TYPE=test`,
构建实际跑在 176(控制台在 master UI 看,含 agent 日志):
- 检出行出现 `checkout branch dev` + commit 号 → 176 git/GITLAB_BOT 凭据 OK;
- `flutter build apk --profile` 命令行出现且**没有** "No Android SDK found"/
  "Failed to find target with hash string" → .env.local 构建键与 android-37 合成 OK;
- 看到以上两关即可 **Abort**(不浪费 5-14 分钟完整构建;残留半删除源码无碍——
  build_swan_apk 的 git 自证(坑 14)下次自动重建)。

**档二:完整链路(部署计划 §10.5)**——正常触发 `dev/test` 跑完:
1. 构建结果 SUCCESS(176 上构建,总耗时约 5-14 分钟,首跑含 pub get 更久);
2. `curl -s http://192.168.12.167:8080/api/apks?limit=5` 出现新行:`source=auto-build`、
   `flavor=profile`、`server_type=test`、`jenkins_job=swan-build-android`、
   `jenkins_build=<构建号>`、`file_path=/opt/swan-build/apks/…`;
3. 167 包管理页新行可下载(产物在 176,下载链路按平台实现)、「Jenkins 日志」按钮跳
   http://192.168.12.167:8082/job/swan-build-android/<构建号>/ 正常;
4. 平台页触发入口(POST /api/builds)同样成功——顺带验证注入封堵(坑 15)在新机生效
   (部署计划 §11)。

## 6. 常见失败对照

| 现象 | 根因方向 |
|---|---|
| 节点 build-176 离线 | ssh key 未配/JavaPath 未指 /opt/build-env/jdk-17/bin/java/known_hosts 未预热(jenkins-setup.md §3/§6) |
| 控制台 "No Android SDK found" | 176 .env.local 未写 JAVA_HOME/ANDROID_HOME/FLUTTER_HOME(内置缺省是 Windows 路径,必须覆盖) |
| "Failed to find target with hash string 'android-37'" | android-37 未合成或 package.xml 未同步改(档案坑 12) |
| git 401/404 | 176 .env.local GITLAB_BOT 两键缺失/错;日志 URL 应为脱敏形态(git.hivi.com/... 无凭据) |
| 登记失败 connection refused/超时 | 176 .env.local DASHBOARD_URL 未指 167(缺省 127.0.0.1:8080 在 176 本机没有平台) |
| ModuleNotFoundError | 没走 venv:job 必须调 /opt/uiautotest/.venv/bin/python;系统 python3 受 PEP 668 锁,勿改 |
| 任务参数与上表不符 | config.xml 版本不对;重新 createItem 前先删旧任务 |
| 8082 起不来 | 端口占用/Java 版本;`systemctl status jenkins` + `journalctl -u jenkins` 看输出 |
