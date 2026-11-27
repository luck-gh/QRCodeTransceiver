# v0.2.0 收发窗口改版

- 发送端和接收端支持亮色、暗色主题。常用操作留在主窗口，创建、播放和录屏参数可按次调整或保存为默认值。
- 接收端扫到首个匹配的二维码前显示已扫描帧数，识别后显示任务和真实源块进度。恢复图按任务大小选择 10、100 或 1000 档，支持查看具体缺块编号。
- 恢复图的绿色表示整组已恢复，黄色表示组内部分恢复。红色表示本轮结束仍有待补块，或检测到源块数据冲突，可打开详情查看原因。
- 接收端可为整个任务或选中区域生成补传码。发送端校验后显示待补块号、数量与载荷字节数，编辑请求后须重新校验。已有冻结任务和接收现场可继续使用。
- Windows 和 Linux 各提供一个便携 ZIP，每个包内都有发送端和接收端。

## 第一步：按电脑系统下载

每个 ZIP 都包含发送端和接收端。两台电脑可以分别下载各自系统的版本。

| 电脑系统 | 下载 ZIP |
|---|---|
| Windows x64 | [QRCodeTransceiver-Windows-x64.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeTransceiver-Windows-x64.zip) |
| Linux x64 | [QRCodeTransceiver-Linux-x64.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeTransceiver-Linux-x64.zip) |

## 第二步：解压并打开程序

完整解压后进入 `QRCodeTransceiver` 文件夹，保留两端各自的 `_internal`。便携包自带 Python 运行库。

| 用途 | Windows | Linux |
|---|---|---|
| 发送文件，显示二维码 | `QRCodeSender/QRSend.exe` | `QRCodeSender/QRSend` |
| 接收文件，解析录屏或视频 | `QRCodeReceiver/QRReceiver.exe` | `QRCodeReceiver/QRReceiver` |

Linux 要求 x86_64、glibc 2.28 或更高版本。窗口需要图形服务和可用字体。

## 命令行、源码与离线安装

发送命令位于 `QRCodeSender/QRSendCLI.exe`，接收命令位于 `QRCodeReceiver/QRReceiveCLI.exe`。Linux 使用对应的 `QRSendCLI` 和 `QRReceiveCLI`。
需要自行构建源码包或准备 Python 离线依赖的用户，可参阅 [构建与部署说明](https://github.com/luck-gh/QRCodeTransceiver/blob/master/docs/BUILDING.md)。

## 验证与限制

发布流程在 Windows x64 和 Rocky Linux 8 x64 上运行单元测试、视频集成测试和双端窗口验收，解压便携包检查窗口、命令行与定向补传，再校验两个 ZIP 的 SHA-256。

录制漏帧或画面严重模糊时，可打开原任务继续接收或定向补传。本版尚未实测 1 GiB 文件传输。

[新手使用说明](https://github.com/luck-gh/QRCodeTransceiver/blob/master/docs/USER_GUIDE.md)
