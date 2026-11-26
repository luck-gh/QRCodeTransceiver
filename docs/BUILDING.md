# 构建与部署

## 目标和形式

`build_exe.py --target` 接受四个值:
`windows-sender`、`windows-receiver`、`linux-sender`、`linux-receiver`.
不加 `--source` 构建便携成品, 加 `--source` 生成源码包.
源码包可附加 `--wheel-dir` 形成离线库包. 旧的仅 Linux 发送端安装/打包入口已删除.

| 系统 | 便携合包 |
|---|---|
| Windows x64 | `QRCodeTransceiver-Windows-x64.zip` |
| Linux x64 | `QRCodeTransceiver-Linux-x64.zip` |

完整解压后进入 `QRCodeTransceiver`，发送时打开 `QRCodeSender/QRSend.exe`，接收时打开 `QRCodeReceiver/QRReceiver.exe`。Linux 使用相同路径中无 `.exe` 后缀的程序。
每端的 GUI 和 CLI 共用该端自己的 `_internal`。合包保留两个运行库目录。
示例输出使用 `--output-dir dist/current`。

## 本机构建 Windows 成品

使用 CPython 3.12, 位数与交付目标一致:
```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\.venv-build\Scripts\python.exe build_exe.py --target windows-sender --arch x64 --output-dir dist/current
.\.venv-build\Scripts\python.exe build_exe.py --target windows-receiver --arch x64 --output-dir dist/current
.\.venv-build\Scripts\python.exe build_exe.py --bundle windows --output-dir dist/current
```

构建 x86 时用 `py -3.12-32` 创建另一个环境, 并传 `--arch x86`.
不能用 64 位 Python 仅修改输出文件名来生成 32 位程序.
打包流程检查固定依赖版本, 不会偷偷自动安装或降级依赖.
源码及依赖版本记录在 BUILD-INFO.json. 本地构建与 Actions 内部保留每个 ZIP 的 SHA-256 用于验证;
Release 下载区只发布 ZIP, 不额外上传校验文件或清单.

## 构建 Linux 便携包

须在与目标兼容的 Linux 机器/容器构建, 不能从 Windows 直接交叉打包.
推荐 x64 基线为 Rocky Linux 8 + glibc 2.28 + CPython 3.12, 让较新的 Ubuntu、Debian 和 RHEL 系统更可能共用.
这不是“所有 Linux 通用”; glibc 2.17 的 RHEL/CentOS 7、musl/Alpine、其他 CPU 或禁止执行程序的机器不在此基线内.

在构建机安装 Python 3.12/Tk、图形库和构建工具后:
```bash
python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install -r requirements.txt -r requirements-build.txt
.venv-build/bin/python build_exe.py --target linux-sender --arch x64 --output-dir dist/current
.venv-build/bin/python build_exe.py --target linux-receiver --arch x64 --output-dir dist/current
.venv-build/bin/python build_exe.py --bundle linux --output-dir dist/current
```

目标机解压便携包即可运行, 不需要安装 Python/Tk/pip, 不需要 sudo.
但需要合适的 glibc、CPU、动态加载器, GUI 还需图形服务和可用系统字体/字体配置; MP4 CLI 可不显示窗口.
x86 32 位 Linux 当前的 Pillow/OpenCV/NumPy 固定版本缺少现成 wheels.
构建器能检查 32 位宿主/输出标签, 但没有提供或验证 Linux x86 二进制.
若必须支持这种旧环境, 需要额外的 32 位依赖源码编译、打包和目标机验证, 不能退回旧库冒充兼容.

## 用已有成品合包

在同一个目录放入对应系统的发送端和接收端 x64 ZIP。合包可以在 Windows 或 Linux 上执行。

```bash
python build_exe.py --bundle windows --portable-dir dist/release-v0.1.0 --output-dir dist/combined
python build_exe.py --bundle linux --portable-dir dist/release-v0.1.0 --output-dir dist/combined
```

省略 `--portable-dir` 时，从输出目录读取两份单端 ZIP。

## 自动构建工程

`.github/workflows/packages.yml` 配置 Windows x64 双角色和 Rocky Linux 8 x64 双角色.
使用系统自带的 Python/Tk 在构建环境中打包, 并在 Xvfb/Windows 中运行 GUI 和文件内容验收.
每个系统构建两端，合包后验收 GUI、CLI 和数据收发。
`master` 更新生成两个 Actions 构建附件。推送 `v*` 标签后，发布任务验证两个合包的 SHA-256，并用 `docs/RELEASE_NOTES.md` 中对应版本的下载说明发布两个 ZIP。
源码和 Python 离线依赖包按下节命令单独生成。

## 四种源码和离线库包

任意操作系统都能生成四种源码包:
```bash
python build_exe.py --target windows-sender --source --output-dir dist/current
python build_exe.py --target windows-receiver --source --output-dir dist/current
python build_exe.py --target linux-sender --source --output-dir dist/current
python build_exe.py --target linux-receiver --source --output-dir dist/current
```

离线库必须匹配角色、目标系统、CPU 和 Python. 例如 Linux 接收端 CPython 3.12 / glibc >= 2.28 / x64:
```bash
python download_packages.py --role receiver --platform manylinux_2_28_x86_64 --python-version 3.12
python build_exe.py --target linux-receiver --source --wheel-dir offline_packages/receiver-manylinux_2_28_x86_64-cp312 --output-dir dist/current
```

Windows x64 用 `--platform win_amd64`, x86 用 `win32`; 发送端用 `--role sender` 和对应 target.
Linux ARM 平台参数可用于下载可获得的 wheels, 但不能把 x64 本机生成的可执行文件当作 ARM 成品.
下载器要求空目录, pip 失败不写成功清单. 新格式清单区分收发角色和系统, 不兼容旧的仅发送端清单.
离线安装用 `install.cmd --offline` / `bash install.sh --offline`, 不联网, 不做管理员安装.
wheels 不含系统 Python/Tk/venv; 这些也无法安装时, 应选便携成品.
源码运行需要匹配的 Python 3.10-3.12，窗口需要 Tk，Linux 录屏需要 X11。
安装后 Windows 用 `send.cmd` / `receive.cmd`，Linux 用 `bash send.sh` / `bash receive.sh`。
命令行对应 `send_cli`、`receive_cli` 启动脚本。

## 验收

```bash
python -m unittest discover -s tests -v
python tests/smoke_package.py dist/current/QRCodeTransceiver-Windows-x64.zip
```

Linux 改成对应 ZIP, GUI 验收需要图形环境或 Xvfb.
成品验收会解压到新目录、移除 Python 环境变量和开发 PATH, 验证两端 GUI/CLI 启动、
发送端生成码的数据内容及接收端的 MP4 逐字节恢复和失败状态.
`--self-test` 是 GUI 的维护入口; 普通用户直接打开窗口即可.

[验证范围](VALIDATION.md) 区分实构/实测与未运行的目标.
参考: [PyInstaller 的 glibc 限制](https://pyinstaller.org/en/stable/usage.html),
[pip 的跨平台下载参数](https://pip.pypa.io/en/stable/cli/pip_download/).

## 固定的 Linux 构建环境

可复用仓库的 Dockerfile (只在开发构建机准备依赖, 不在用户电脑安装软件):
Dockerfile 的构建上下文需要四份 requirements 文件. 镜像构建后将不含 .git/密钥的源码工作副本挂到 /work;
默认命令执行 packaging/build_linux.sh, 包括测试、双端打包和成品验收. 也可追加 bash packaging/build_linux.sh receiver 只构建接收端.


Linux spec 明确收集 XCB/OpenGL 分派客户端库; 接收端 runtime hook 使 MSS 在冻结目录定位 XCB, 不依赖目标机的 ldconfig 开发库发现.
不携带 glibc、显卡驱动或图形服务器. GUI 在无字体环境中不能工作, 中文字体应由目标桌面提供.
离线包 install.cmd 会选择清单匹配的 Python 版本和 32/64 位, Linux install.sh 默认选择清单对应的 python3.x, 也可通过 PYTHON_BIN 指定解释器.

自动工程参考: [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), [upload-artifact](https://github.com/actions/upload-artifact).
