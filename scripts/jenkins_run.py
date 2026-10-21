"""Jenkins 入口脚本 - 读取 Jenkins 环境变量，调用 run_parallel.py"""
import os
import subprocess
import sys

PYTHON = sys.executable
PROJECT = r"E:\UIAutotest"

def main():
    mode      = os.environ.get("EXECUTION_MODE", "并行")
    n         = os.environ.get("N_INSTANCES", "4")
    reinstall = os.environ.get("REINSTALL", "是") == "是"
    branch    = os.environ.get("BRANCH", "latest_online")
    run_tests = os.environ.get("RUN_TESTS", "是") == "是"
    test_dir  = os.environ.get("TEST_DIR", "tests/checklist")

    cmd = [PYTHON, "run_parallel.py"]
    cmd += ["-n", "1"] if mode == "串行" else ["-n", n]

    if reinstall:
        cmd += ["--branch", branch]  # latest_online 由 run_parallel.py 内部处理

    if not run_tests:
        # 只重装不跑测试：传 -t 0 限制文件数为 0
        cmd += ["-t", "0"]
    elif test_dir != "tests/checklist":
        cmd += ["--test-dir", test_dir]

    print(f"REINSTALL={reinstall}  RUN_TESTS={run_tests}")
    print(f"Running: {' '.join(cmd)}")

    os.chdir(PROJECT)
    ret = subprocess.call(cmd)
    # 发群通知已下沉到 run_parallel.py 内部（无论 Jenkins 还是本地跑统一发），此处不再重复调用

    sys.exit(ret)

if __name__ == "__main__":
    main()
