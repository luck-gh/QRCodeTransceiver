# QRCodeTransceiver

通过动态二维码传文件, 支持 Windows/Linux 双端窗口、命令行、MP4 逐帧接收、保存续收和定向补传.
远端仓库: https://github.com/luck-gh/QRCodeTransceiver

新传输协议 QT2 每包绑定具体任务、分块参数和校验. 每轮先覆盖全部源块, 再发新的 LT 喷泉修复包.
漏包不导致后续编码包失效; 译码器先剥皮, 再用 GF(2) 高斯消元求解残余方程. 只有完整编码数据和最终文件 SHA-256 校验都通过才标记完成.

## 第一步：按电脑系统下载

在 [GitHub Release](https://github.com/luck-gh/QRCodeTransceiver/releases) 选择对应系统的便携包。每个包都包含发送端和接收端。

| 电脑系统 | 下载文件 |
|---|---|
| Windows x64 | `QRCodeTransceiver-Windows-x64.zip` |
| Linux x64 | `QRCodeTransceiver-Linux-x64.zip` |

## 第二步：解压并打开程序

完整解压后进入 `QRCodeTransceiver` 文件夹，保留各端目录内的 `_internal`。

| 用途 | Windows | Linux |
|---|---|---|
| 发送文件，显示二维码 | `QRCodeSender/QRSend.exe` | `QRCodeSender/QRSend` |
| 接收文件，解析录屏或视频 | `QRCodeReceiver/QRReceiver.exe` | `QRCodeReceiver/QRReceiver` |

操作详情见 [使用说明](docs/USER_GUIDE.md)。需要命令行、源码或 Python 离线安装时，参阅 [构建与部署说明](docs/BUILDING.md)。实际验收范围见 [验证记录](docs/VALIDATION.md)。

## 使用

1. 发送端选择文件，冻结并保存 `.qrtx`，开始发送。播放器显示轮次、码序和真实源块编号或修复包。
2. 接收端导入 MP4, 或录制屏幕、停止并保存后解析. 二维码可移动, 局部定位失败会搜索同一完整帧.
3. 不完整时自动保存 `.qrrx`。可以“继续现场”或追加视频，从自适应恢复图查看仍待补传的真实块号。
4. 生成单行补传码, 复制/导出到发送端, 打开原 `.qrtx` 校验后只补发所需源块.
5. 接收端追加补传视频，完成校验并保存文件。后续可重复续收与补传。

```bash
python qrcode_tx_gui.py
python qrcode_rx_gui.py
# 命令行创建并冻结任务
python qrcode_tx.py myfile.zip --task myfile.qrtx
# 接收后即使不完整也保存现场; 不完整退出码为 2
python qrcode_rx.py --video first.mp4 --task myfile.qrrx --request-out repair.qrreq
python qrcode_tx.py --task myfile.qrtx --repair repair.qrreq
python qrcode_rx.py --video repair.mp4 --task myfile.qrrx
# 旧格式视频的独立一次性入口
python qrcode_rx.py --legacy --video old.mp4
```

开发依赖: `python -m pip install -r requirements.txt`; 测试: `python -m unittest discover -s tests -v`.
新发送端统一为单文件任务, 多文件先打成 ZIP; 删除旧的多文件交织/预生成循环契约. 用户要求的旧 MP4 一次性解析入口保留, 不合并新任务.
[协议说明及后续节奏同步讨论](docs/PROTOCOL_NOTES.md) 区分已实现内容与研究方向.
正式交付为 Windows x64 和 Linux x64 双端便携包。推送 v* 标签后，CI 构建并验收两个合包，随后发布到 GitHub Release。
