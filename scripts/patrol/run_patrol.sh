#!/bin/bash
# 巡检入口(cron 拉起,每日 08:41/19:43,见 docs/agent模块档案/巡检机制.md §6):
# flock 防重入 → 无头 claude 按 PATROL_PROMPT.md 单次执行 → 日志/报告留档 14 天自动清理。
# 部署位置 /opt/patrol;改动须同步档案并在汇报中说明。
set -u
PATH=/usr/local/bin:/usr/bin:/bin   # cron 缺省 PATH 不含 /usr/local/bin(claude/wecom-cli 在此)
PATROL_DIR="$(cd "$(dirname "$0")" && pwd)"

exec 9>/tmp/patrol.lock || exit 0
flock -n 9 || exit 0                # 上一轮未跑完则直接跳过本轮

mkdir -p "$PATROL_DIR/logs" "$PATROL_DIR/reports"
TS=$(date +%Y%m%d_%H%M%S)
LOG="$PATROL_DIR/logs/patrol_$TS.log"

# Jenkins 凭据仅进本进程环境变量(.env.local 属 autotest 600,借 sudo awk 取键值;
# 不落日志、不落文件、不得出现在任何报告输出)
JENKINS_USER=$(sudo awk -F= '/^JENKINS_USER=/{print $2}' /opt/uiautotest/.env.local 2>/dev/null)
JENKINS_API_TOKEN=$(sudo awk -F= '/^JENKINS_API_TOKEN=/{print $2}' /opt/uiautotest/.env.local 2>/dev/null)
export JENKINS_USER JENKINS_API_TOKEN

{
  echo "[$(date '+%F %T')] patrol start (claude headless, 900s timeout)"
  timeout 900 claude -p --dangerously-skip-permissions < "$PATROL_DIR/PATROL_PROMPT.md"
  RC=$?
  echo "[$(date '+%F %T')] patrol done rc=$RC"
} >> "$LOG" 2>&1

find "$PATROL_DIR/logs" -name 'patrol_*.log' -mtime +14 -delete 2>/dev/null
find "$PATROL_DIR/reports" -name 'L2_*.md' -mtime +14 -delete 2>/dev/null
