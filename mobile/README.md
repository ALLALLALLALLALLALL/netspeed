# netspeed mobile · 手机测速

手机浏览器打开电脑起的这个小网页，直接测**手机自己的网络**；游戏节点由电脑主机探测；AI 总结只支持云端 API（手机版不做本地模型）。

## 启动

1. 手机和电脑连**同一个 Wi-Fi**
2. 双击 `启动手机测速.bat`
3. 窗口会打印：

```text
电脑本机: http://127.0.0.1:8765/
手机访问: http://192.168.x.x:8765/
```

4. 手机浏览器输入 `http://192.168.x.x:8765/`（不要用 127.0.0.1）

如果手机打不开：Windows 防火墙放行 Python/8765 端口；确认路由器没有开“AP 隔离”。

## 手机端测什么

| 项目 | 在哪里测 | 说明 |
| --- | --- | --- |
| 下载 | 手机浏览器 | `fetch` Cloudflare `__down`，读取实际收到的字节，4 秒窗口 |
| 上传 | 手机浏览器 | POST Cloudflare `__up`，只统计服务器确认完成的请求体 |
| 延迟/抖动 | 手机浏览器 | 对 Cloudflare 连续 5 次 1 字节请求 |
| Steam / Epic / EA / 动视 / NexusMods / GitHub | **电脑主机** | 这些站点不允许跨域读取，手机浏览器拿不到真实字节；页面明确标注“电脑主机探测”，不冒充手机延迟 |

手机带宽部分走 Cloudflare（CORS 允许 `*`），所以能真实读到字节；OVH、Steam、GitHub 等没有 CORS 头，浏览器读不到响应体，强行测只能编数字——所以不做。

## AI 总结（仅云端）

页面底部「云端 API 设置」：

- Base URL：`https://api.deepseek.com/v1` 或 `https://api.openai.com/v1`
- API Key：你的 key
- 模型：`deepseek-chat` / `gpt-4o-mini` 等

保存后 key 只存在**电脑**的 `%APPDATA%\netspeed-mobile\config.json`，网页不会回显；手机只收到总结文本。

没有配置或调用失败时，仍会显示规则总结，并明确写出 AI 失败原因。

## 不虚报 / 不瞒报

- Mbps = 浏览器实际读取/发送的字节 × 8 ÷ 实际耗时
- 失败、CORS 拦截、超时都显示在总结的“失败项”里
- 游戏节点只报延迟，不编造下载速度
- 页面会注明哪些数字来自手机、哪些来自电脑

## 文件

```text
netspeed-mobile/
├── server.py            # 网页 + JSON API（标准库）
├── index.html           # 手机页面（测速 / 节点 / 云端设置）
├── 启动手机测速.bat
└── README.md
```

## 依赖

电脑端 Python 3.9+，只用标准库。

## APK（安卓）

文件：`netspeed.apk`（约 20 KB，debug 签名，直接侧载）

- 最低 Android 7.0（API 24），targetSdk 34
- 安装：把 APK 传到手机 → 允许“安装未知来源应用” → 安装
- 功能：手机原生下载/上传/延迟 + Steam/Epic/EA/动视/NexusMods/GitHub 直连延迟 + 云端 API 总结
- 没有本地模型选项；API key 存在手机 SharedPreferences
- 手机带宽用 `HttpURLConnection` 实际读写字节计数，失败会显示 0/错误，不编数字

## 国内节点

- **安卓 APK**：内置华为云、网易两个国内镜像的下载测速（手机直连，Range 读取，3 秒窗口）
- **桌面版**：同样内置这两个节点
- **手机浏览器版**：受 CORS 限制，镜像不返回 `Access-Control-Allow-Origin`，浏览器读不到字节，所以不测国内下载，只测 Cloudflare，避免编数字
