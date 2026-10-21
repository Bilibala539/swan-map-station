# -*- coding: utf-8 -*-
"""Jenkins 原生页面实拍(登录墙内):表单登录后逐页截图(主会话专用,凭据走环境变量)
用法: JENKINS_BASE=... JENKINS_USER=... JENKINS_PASSWORD=... python scripts/jenkins_shot.py
凭据只从环境变量读,不写入任何文件;输出 docs/竞品截图/auto/jenkins-native/
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ["JENKINS_BASE"].rstrip("/")

PAGES = [
    ("dashboard", "/"),                              # 首页:任务列表
    ("job-build", "/job/swan-build-android/"),         # job 页:构建历史+侧栏操作
    ("build-detail", "/job/swan-build-android/6/"),    # 单次构建详情
    ("console", "/job/swan-build-android/6/console"),  # 控制台日志页
]


def main():
    out_dir = ROOT / "docs" / "竞品截图" / "auto" / "jenkins-native"
    out_dir.mkdir(parents=True, exist_ok=True)

    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.common.by import By

    opts = EdgeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument("--log-level=3")
    driver = webdriver.Edge(options=opts)
    try:
        driver.set_page_load_timeout(45)
        driver.get(BASE + "/login")
        driver.find_element(By.ID, "j_username").send_keys(os.environ["JENKINS_USER"])
        driver.find_element(By.ID, "j_password").send_keys(os.environ["JENKINS_PASSWORD"])
        driver.find_element(By.NAME, "Submit").click()
        import time; time.sleep(1.5)
        if "/loginError" in driver.current_url:
            print("LOGIN-FAILED"); raise SystemExit(2)
        print("LOGIN-OK")

        for name, path in PAGES:
            driver.get(BASE + path)
            time.sleep(2.5)
            driver.get_screenshot_as_file(str(out_dir / f"{name}.png"))
            total = driver.execute_script("return document.body.scrollHeight")
            for y in range(0, min(total, 8000), 1200):
                driver.execute_script(f"window.scrollTo(0, {y})"); time.sleep(0.12)
            driver.execute_script("window.scrollTo(0, 0)")
            driver.set_window_size(1440, min(max(total, 900), 6000))
            time.sleep(0.8)
            driver.get_screenshot_as_file(str(out_dir / f"{name}-full.png"))
            driver.set_window_size(1440, 900)
            print(f"SHOT {name} h={total}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
