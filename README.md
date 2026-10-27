# netspeed

不虚报、不瞒报的网络 + 游戏节点测速工具。包含 **Windows 桌面版** 和 **Android APK**。

> 所有速度都按实际读写的字节数 ÷ 实际耗时计算；失败、403/429、超时、无数据都会原样显示，不重试取最大值，不拿页面大小冒充下载速度。

## 下载

到 [Releases](https://github.com/ALLALLALLALLALLALL/netspeed/releases) 下载：

| 文件 | 平台 | 说明 |
| --- | --- | --- |
| `netspeed-desktop-v1.0.0-win64.zip` | Windows 10/11 x64 | 解压后运行 `netspeed.exe`，`_internal` 必须保留 |
| `netspeed-android-v1.0.0.apk` | Android 7.0+ | 需允许安装未知来源应用 |

## 功能

### Windows 桌面版

- 通用下载：OVH、Cloudflare（多流）
- 国内节点下载：华为云、网易
- 国内节点延迟：阿里云、腾讯云、中科大、清华、北外、南大
- 游戏/平台：Steam、Epic Games、EA、动视、NexusMods、GitHub
- 上传：Cloudflare
- 延迟 / 抖动 / 丢包，每个节点独立采样
- 总结：规则总结 + 可选 AI 总结
  - 本地模型（Ollama 等，OpenAI 兼容）
  - 云端 API（OpenAI 兼容）
  - 必须手动填 Base URL / Key / 模型；不填就用规则总结
  - 配置在 `%APPDATA%\netspeed\config.json`

### Android APK

- 互联网下载 / 上传 / 延迟：Cloudflare，手机本机实测
- 国内节点下载：华为云、网易（手机直连）
- 节点延迟：Steam、Epic、EA、动视、NexusMods、GitHub、华为云、网易、阿里云、腾讯云、中科大、清华、北外、南大
- 局域网下载：手机 ← 电脑 Wi-Fi，需要电脑运行 `mobile/server.py`
- 云端 API 总结（手机版不支持本地模型）
- Key 存在手机 SharedPreferences

### 手机网页版（电脑起服务）

```bash
cd mobile
python server.py
# 手机访问 http://<电脑局域网IP>:8765/
```

- 手机浏览器直接测 Cloudflare 互联网带宽
- 局域网下载：手机 ← 电脑 50MB 流
- 游戏节点由电脑探测并标注
- 云端 API 总结

## 国内节点

| 节点 | 延迟 | 下载 |
| --- | --- | --- |
| 华为云 | ✅ | ✅ |
| 网易 | ✅ | ✅ |
| 阿里云 | ✅（302） | ❌ |
| 腾讯云 | ✅（404） | ❌ |
| 中科大 | ✅（403） | ❌ |
| 清华 | ✅ | ❌ 大文件 Range 403 |
| 北外 | ✅ | ❌ 同上 |
| 南大 | ✅ | ❌ 302 |

能真实读到字节的下载节点只有华为云、网易；其余保留延迟，失败状态原样显示。

## 为什么没有“本机回环下载”

桌面版本机访问自己的局域网 IP 会走内存回环，能测出 10 Gbps 这类数字，但不代表 Wi-Fi 或宽带。为了不虚高，桌面版不把这个数字放进报告；本地/Wi-Fi 下载请在 **APK 或手机网页版**里测（手机 → 电脑）。

## 构建

### 桌面 EXE

```bash
pip install pyinstaller
cd desktop
pyinstaller --noconfirm --onedir --windowed --name netspeed app.py
```

### 手机服务

```bash
cd mobile
python server.py
```

APK 源码不在本仓库（用 Android SDK 单独构建）；Release 里提供签名 APK。

## 免责声明

测速会消耗真实流量。节点可用性随网络环境变化，失败会在报告中保留，不保证每个节点每次都能测通。

## License

MIT
