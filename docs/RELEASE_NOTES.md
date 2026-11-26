# Windows x64 / Linux x64 双端交付

## 下载便携版（解压即可用）

发送端用来选择文件并显示二维码，接收端用来解析视频或录屏。

| 电脑和用途 | 点击下载 ZIP（链接文字就是文件名） | 解压后打开 |
|---|---|---|
| Windows x64 发送端 | [QRCodeSender-Windows-x64.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeSender-Windows-x64.zip) | `QRCodeSender/QRSend.exe` |
| Linux x64 发送端 | [QRCodeSender-Linux-x64.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeSender-Linux-x64.zip) | `QRCodeSender/QRSend` |
| Windows x64 接收端 | [QRCodeReceiver-Windows-x64.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeReceiver-Windows-x64.zip) | `QRCodeReceiver/QRReceiver.exe` |
| Linux x64 接收端 | [QRCodeReceiver-Linux-x64.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeReceiver-Linux-x64.zip) | `QRCodeReceiver/QRReceiver` |

完整解压, 保留 `_internal`. 便携包不要求目标机安装 Python.
Linux 为 glibc >= 2.28 / x86_64 基线; 窗口仍需图形服务和可用字体, 中文需要系统中文字形.
这次优先交付 Windows x64 / Linux x64, 不包含 x86 成品.

## 满秩停滞修复

之前的实现仅做剥皮, 即使信息足够、方程满秩, 没有单未知量方程时仍可能停住.
本版先执行剥皮, 再在视频结束、现场恢复及生成补传请求前进行 GF(2) 位集高斯消元.
满秩时恢复所有剩余源块; 欠秩时仅恢复唯一确定的块并保留其余关系, 不猜测自由变量.
生成补传请求前会先完整求解已有信息, 避免把暂时解不出误判为必须补传.
日志显示高斯新增恢复数量和求解前残余方程秩. 恢复现场若已完整求解, 可直接校验并写出文件.
旧视频一次性入口也使用同一求解器; 新旧协议之间不混用任务.

已覆盖真实 LT 包的三块满秩停滞案例、欠秩/矛盾/重复方程、穷举参照,
以及 746 块、剥皮恢复 544 块、920 份观测的合成规模回归. 该合成样本不是用户原始录像.

## 保留的功能

- 两端小窗口、命令行和脚本入口.
- MP4 顺序逐帧解析; 录屏保存视频后复用同一解析器, 不让识别拖慢采集.
- 二维码移动跟随, 丢失后搜索完整画面.
- 冻结发送任务、保存接收现场、重启后续收、跨视频累积和去重.
- 紧凑的缺块/成功集合/位图补传码; 定向补发原始源块并追加恢复.
- 分块状态图、不同轮次编号/颜色的发送进度条及操作日志.
- 新协议逐包 CRC、任务绑定及原文件最终 SHA-256 校验.

## 脚本和离线包

已有 Python 环境、需要用脚本运行时，可以按系统和用途下载下表中的 ZIP。离线包附带 CPython 3.12 x64 依赖，仍需要系统已有 Python、Tk 和 venv。

| 电脑与包类型 | 发送端 ZIP | 接收端 ZIP |
|---|---|---|
| Windows x64 源码 | [QRCodeSender-Windows-source.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeSender-Windows-source.zip) | [QRCodeReceiver-Windows-source.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeReceiver-Windows-source.zip) |
| Windows x64 离线 | [QRCodeSender-win_amd64-cp312-offline.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeSender-win_amd64-cp312-offline.zip) | [QRCodeReceiver-win_amd64-cp312-offline.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeReceiver-win_amd64-cp312-offline.zip) |
| Linux x64 源码 | [QRCodeSender-Linux-source.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeSender-Linux-source.zip) | [QRCodeReceiver-Linux-source.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeReceiver-Linux-source.zip) |
| Linux x64 离线 | [QRCodeSender-manylinux_2_28_x86_64-cp312-offline.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeSender-manylinux_2_28_x86_64-cp312-offline.zip) | [QRCodeReceiver-manylinux_2_28_x86_64-cp312-offline.zip](https://github.com/luck-gh/QRCodeTransceiver/releases/latest/download/QRCodeReceiver-manylinux_2_28_x86_64-cp312-offline.zip) |

Release 下载区只提供 ZIP. 构建过程中仍会校验完整性, 校验文件和清单不作为独立附件发布.

## 验证与限制

53 项单元/视频集成测试覆盖核心及完整续收补传闭环; 发布前对两种系统实构、解压并验收窗口及命令行.
CI 仅在 Windows x64 和 Rocky Linux 8 x64 构建, 全部成功后才发布 Release 附件; Actions 附件不再是唯一交付位置.

更多识别结果不等于更多独立信息, 包数不等于方程秩. 原先 746 块录像尚未提供, 不宣称已在该原始样本上复测.
本版不包含改变参数的二次识别“打捞”或参考信号自动采样同步. 录制漏帧、严重模糊或欠秩时仍需追加接收/定向补传,
不能保证任意视频一次恢复. 大型残余系统的消元仍有 CPU/内存成本, 本版没有超大文件性能保证.

[新手使用说明](https://github.com/luck-gh/QRCodeTransceiver/blob/master/docs/USER_GUIDE.md)
