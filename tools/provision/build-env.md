# 构建工具链安装清单 v2(176 构建机,Linux,2026-09-21)

> 用途:176(Ubuntu 24.04 Server)照本清单逐步执行重建 /opt/build-env。
> 执行身份:前置目录用 sudo 建,**安装步骤以 autotest 身份跑**(构建用户与安装用户一致,避免权限纠缠);
> **每步验证输出无误再进行下一步**。
> 目录名必须精确:`/opt/build-env/{jdk-17, jdk-21, android, flutter}`——.env.local 覆盖键与全部档案文档按此口径。
> **本清单不含 adb/不接手机**:176 是纯构建机,与设备池彻底解耦,产物登记走 HTTP(DASHBOARD_URL)。
> 参考档案:docs/agent模块档案/打包构建.md(SDK 布局、坑 12 android-37 合成、坑 11 env 显式注入)。

## 0. 前置依赖与目录骨架

```bash
sudo apt-get update
sudo apt-get install -y curl unzip xz-utils zip libglu1-mesa   # git 已按部署计划 §3 装过
sudo mkdir -p /opt/build-env
sudo chown autotest:autotest /opt/build-env
```

## 1. JDK 17 Temurin(17.0.20.1)+ JDK 21(Linux tarball)

**选型依据(选 tarball 不选 apt openjdk)**:①路径钉死 `/opt/build-env/jdk-17`——打包脚本
build_env() 与 .env.local 覆盖键按固定路径拼 JAVA_HOME,apt 版落在
`/usr/lib/jvm/java-17-openjdk-amd64` 且随发行版升级漂移;②Ubuntu 24.04 的 openjdk-17 已移出
main(仅 universe 维护),钉版本组合(Temurin 17.0.20.1 与旧机一致)不受发行版牵连;③与
「便携、不写系统」原则一致(Windows 备料用 zip 版,Linux 对应 tar.gz 版)。
167 Jenkins master 的 Java 不走此清单(那边用 apt,见 jenkins-setup.md)。

国内镜像下载(TUNA,版本号以 https://adoptium.net/temurin/releases/?version=17 为准):

```bash
cd /tmp
curl -fLO https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/linux/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_8.tar.gz
tar -xzf OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_8.tar.gz -C /opt/build-env
mv /opt/build-env/jdk-17.0.20.1+8 /opt/build-env/jdk-17   # 解出目录名必须规整
# JDK21 同法:Adoptium/21/jdk/x64/linux/ 下载解压,规整为 /opt/build-env/jdk-21
```

验证:

```bash
/opt/build-env/jdk-17/bin/java -version   # openjdk version "17.0.20.1"(或 17.0.2x)
/opt/build-env/jdk-21/bin/java -version   # openjdk version "21.x"
```

## 2. env 注入 /etc/profile.d/build-env.sh

创建 `/etc/profile.d/build-env.sh`(root 写入),全文如下:

```sh
# /etc/profile.d/build-env.sh —— 构建环境汇总注入(仅登录 shell 生效,不改全局)
export JAVA_HOME=/opt/build-env/jdk-17
export ANDROID_HOME=/opt/build-env/android
export FLUTTER_HOME=/opt/build-env/flutter
export FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn
export PUB_HOSTED_URL=https://pub.flutter-io.cn
export PATH="$FLUTTER_HOME/bin:$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
```

用法与验证(重新登录或 `source` 后执行后续步骤):

```bash
source /etc/profile.d/build-env.sh
echo "$JAVA_HOME"   # /opt/build-env/jdk-17
```

注:**Jenkins agent 会话与构建脚本不依赖此文件**——build_swan_apk.py 的 build_env()
自行组装构建环境(坑 11),JAVA_HOME 等从 176 的 .env.local 读取(见 jenkins-verify.md
.env.local 分工表);此文件仅供交互式执行 sdkmanager/flutter 用。
Jenkins SSH agent 起 JVM 需要的 java 在节点配置里用绝对路径 JavaPath 指定(见 jenkins-setup.md §3)。

## 3. Android SDK

### 3.1 cmdline-tools

下载 Linux 版(旧机用 build 11076708,可沿用;官网 https://developer.android.com/studio#command-line-tools-only):

```bash
cd /tmp
curl -fLO https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip -q commandlinetools-linux-11076708_latest.zip -d /opt/build-env/android
# 官方 zip 解出顶层 cmdline-tools\ 目录,SDK 布局要求其位于 cmdline-tools/latest/:
mv /opt/build-env/android/cmdline-tools/cmdline-tools /opt/build-env/android/cmdline-tools/latest
```

### 3.2 装包(需 JAVA_HOME;分步执行分步验证)

```bash
source /etc/profile.d/build-env.sh
sdkman=/opt/build-env/android/cmdline-tools/latest/bin/sdkmanager
"$sdkman" "platform-tools"
"$sdkman" "platforms;android-34" "platforms;android-36"
"$sdkman" "platforms;android-37.0" "platforms;android-37.1" "platforms;android-37.2"
"$sdkman" "build-tools;34.0.0" "build-tools;36.0.0"
# license 全部接受
yes | "$sdkman" --licenses
```

验证:`"$sdkman" --list_installed` 列出以上全部包,无 Warning。

### 3.3 android-37 手工合成(Google 已停发 base 37,App 仓 AGP 8.13 要 'android-37';档案坑 12)

创建 `/opt/build-env/fix-android37.py`,全文如下(方法与 Windows 版一致,仅路径换 Linux;
改两个文件,**包身份以 package.xml 为准**,只改 source.properties 会报 inconsistent location):

```python
"""android-37 合成:拷贝 android-37.0 → android-37,同步改 source.properties 与 package.xml。"""
import shutil
from pathlib import Path

sdk = Path("/opt/build-env/android/platforms")
src, dst = sdk / "android-37.0", sdk / "android-37"
if not src.is_dir():
    raise SystemExit("android-37.0 不存在,先按 3.2 安装")
if dst.exists():
    raise SystemExit("android-37 已存在,跳过")
shutil.copytree(src, dst)
# ① source.properties:ApiLevel 37.0 → 37
sp = dst / "source.properties"
sp.write_text(sp.read_text(encoding="utf-8").replace("AndroidVersion.ApiLevel=37.0",
                                                     "AndroidVersion.ApiLevel=37"),
              encoding="utf-8")
# ② package.xml(关键):localPackage path / api-level / display-name 三处 37.0 → 37
px = dst / "package.xml"
px.write_text(px.read_text(encoding="utf-8").replace("37.0", "37"), encoding="utf-8")
print("合成完成:", dst)
```

执行与验证:

```bash
/opt/uiautotest/.venv/bin/python /opt/build-env/fix-android37.py   # PEP 668:不用系统 python
source /etc/profile.d/build-env.sh
/opt/build-env/android/cmdline-tools/latest/bin/sdkmanager --list_installed
```

**验证标准**:`platforms;android-37` 与 `platforms;android-37.0` 同时列出,且**无**
"Observed package id 'platforms;android-37.0' in inconsistent location" 警告。

## 4. Flutter 3.38.7 stable(Linux tarball)——版本跟随开发

> **版本策略(2026-09-28 定,替代原 3.47.4 选型)**:打包机 Flutter 版本**跟随开发环境**
> ——开发用哪个我们用哪个(现为 3.38.7/Dart 3.11.4),开发升级时我们再跟升。
> 背景:3.47.4(Dart 3.13.3)的 `NetworkInterface.addresses` 返回 `List<InterfaceAddress>`,
> 且 `InterfaceAddress implements InternetAddress`,核心仓 upnp2 discovery.dart 的 orElse
> 回退写法在该版本编译不过(3.38.7 中 addresses 元素就是 InternetAddress,无此问题),
> 9-24~9-28 每日构建连挂 4 天即此因。3.47.4 备份保留在 /opt/build-env/flutter-3.47.4.bak。

```bash
# 国内镜像直达(官网 https://docs.flutter.dev/release/archive 为准):
cd /tmp
curl -fLO https://storage.flutter-io.cn/flutter_infra_release/releases/stable/linux/flutter_linux_3.38.7-stable.tar.xz
tar -xf flutter_linux_3.38.7-stable.tar.xz -C /opt/build-env   # 顶层 flutter\ 目录,落位 /opt/build-env/flutter
source /etc/profile.d/build-env.sh   # 先注入镜像变量再首跑
flutter --version                    # 首跑下载 Dart SDK(走 storage.flutter-io.cn)
flutter doctor
```

验证:`flutter --version` 显示 Flutter 3.38.7 stable;`flutter doctor` 无 Android toolchain
缺失项(android-37 合成生效且 licenses 已接受)。doctor 对 176 的合理终态:Android toolchain ✓、
其余(Chrome/Android Studio 类)缺项可忽略——176 无桌面无浏览器属预期。

## 5. 终验清单(全部通过才算 176 工具链就绪)

| 命令 | 期望 |
|---|---|
| `/opt/build-env/jdk-17/bin/java -version` | 17.0.20.1(或 17.0.2x) |
| `/opt/build-env/jdk-21/bin/java -version` | 21.x |
| `source /etc/profile.d/build-env.sh; echo $FLUTTER_STORAGE_BASE_URL` | https://storage.flutter-io.cn |
| `sdkmanager --list_installed` | platform-tools、platforms 34/36/37.0/37.1/37.2/**37(合成)**、build-tools 34.0.0/36.0.0,无 inconsistent 警告 |
| `flutter --version` | Flutter 3.38.7 stable |
| `flutter doctor` | Android toolchain 无 ✗(桌面/浏览器类缺项可忽略) |
| `/opt/build-env/` 属主 | autotest:autotest |

全部通过后按 jenkins-setup.md 搭 176 节点,按 jenkins-verify.md 走验证。
**再次确认:176 不装 adb、不接手机**(部署计划 §5——adb/设备池在 167)。

## 6. 实装勘误 v2.1(2026-09-21 实装回填)

1. **cmdline-tools zip 是单层顶层目录**(cmdline-tools/bin/...):解压目标应为
   `-d /opt/build-env/android/cmdline-tools`,再 mv 内层 cmdline-tools 为 latest(§3 的
   `-d android` + 双层 mv 写法会 ENOENT)。
2. **sdkmanager 直连 dl.google.com 不通时**:sdkmanager 本体支持 HTTP 代理语义——
   `--no_https --proxy=http --proxy_host=mirrors.cloud.tencent.com --proxy_port=80` 走腾讯
   镜像(脚本先 curl repository2-1.xml 探活再选路)。platforms/build-tools/platform-tools
   全部经此装成。
3. **Flutter 700MB tar.xz 大包会中途断流**(curl 18):必须 `--retry 5 --retry-all-errors -C -`
   断点续传落 /tmp 固定路径。
4. **fix-android37.py 的"已存在,跳过"用 SystemExit(字符串) 退出码是 1**——撞 set -e 会杀
   幂等重跑;调用侧先判 `[ -f android-37/android.jar ]` 再执行。
5. **`flutter config --android-sdk` 在 sudo -u autotest 下会读错 HOME**(某些 sudo 配置
   env_keep HOME 时,flutter 校验路径指向调用者家目录报权限错)。构建无碍:打包 job 的
   构建脚本自行注入 ANDROID_HOME(坑 11 口径),不依赖 flutter config 持久化。
6. TUNA Adoptium 的 resolve 自动取最新小版本(17.0.20.1_1/21.0.12.1_1),不再钉死旧包名。
