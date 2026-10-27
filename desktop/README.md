# netspeed · 节点测速

不虚报、不瞒报的网络 + 游戏平台节点测速工具。支持手动接入本地或云端模型来生成总结。

## 功能

- **通用带宽**：OVH、Cloudflare 下载 + Cloudflare 上传
- **平台节点**：Steam、Epic Games、EA、动视、NexusMods、GitHub
  - 有公开大文件的测 **节点吞吐**（Steam 安装包、GitHub Release 资源）
  - 没有公开大文件的只测 **连接/首字节延迟**，并明确标注“仅节点延迟”，不会编造下载速度
- **延迟 / 抖动 / 丢包**：每个节点独立采样
- **AI 总结**：本地模型或云端 API，**必须手动配置**；不配置就用规则总结
- **EXE**：`dist\netspeed\netspeed.exe`（onedir，旁边 `_internal` 必须保留）

## EXE 运行方式（防止闪退）

1. 先把 `netspeed-portable.zip` **完整解压**，得到 `netspeed` 文件夹
2. 进入该文件夹，双击 `启动.bat` 或 `netspeed.exe`
3. `netspeed.exe`、`_internal`、`config.json` 必须在一起；只拷 exe 会闪退
4. 不要在压缩软件的预览窗口里直接双击 exe
5. 若仍启动失败，同目录会生成 `crash.log`，里面是完整报错

## 为什么不会虚报 / 瞒报

1. Mbps = `实际读写字节 × 8 ÷ 实际耗时`，不按 `Content-Length`、不按套餐估算。
2. 每个目标只测一轮，不重试取最大值。
3. 失败、HTTP 403/429、超时、`no data received` 全部写进报告的 `errors`，并统计失败流。
4. 没有公开大文件的平台只报延迟，不拿 HTML 页面大小冒充下载速度。
5. 报告 JSON 含原始字节、秒数、并发数，可自行复核。

## API 总结（手动接入）

打开 GUI 的 **API 设置**：

| 类型 | Base URL 示例 | Key | 模型 |
| --- | --- | --- | --- |
| 本地模型（Ollama） | `http://127.0.0.1:11434/v1` | `ollama` | 你本地已安装的模型名，如 `qwen2.5:14b` |
| 云端 OpenAI 兼容 | `https://api.openai.com/v1` | 你的 key | 如 `gpt-4o-mini` |
| 云端 DeepSeek | `https://api.deepseek.com/v1` | 你的 key | 如 `deepseek-chat` |

- 必须勾选「启用 AI 总结」才会调用。
- 「测试连接」会请求 `/models` 验证地址和 key。
- 不配置 / 调用失败时，仍会显示规则总结，并明确写出 AI 失败原因。
- 配置保存在 `%APPDATA%\netspeed\config.json`（不受 exe 所在目录权限影响）。
- 「测试连接」会自动选一个非 embedding 的模型填入；「测试总结」可以不跑测速直接验证 API。

总结提示词要求模型**只能使用报告里的数字**，不许编造；但 AI 输出仍可能有偏差，所以原始表格始终保留在总结上方。

## 使用

```bat
:: EXE
dist\netspeed\netspeed.exe
:: 或
启动测速.bat

:: 源码
python app.py
python engine.py --duration 4 --streams 3 --latency-attempts 3
```

CLI 参数：`--duration`、`--streams`、`--latency-attempts`、`--json`。

## 节点说明

| 节点 | 测什么 | 说明 |
| --- | --- | --- |
| OVH | 延迟 + 下载 | 1GB 测试文件，欧洲 |
| Cloudflare | 延迟 + 下载 + 上传 | `__down` / `__up` |
| Steam | 延迟 + 下载 | `cdn.akamai.steamstatic.com` 安装包，短时吞吐 |
| GitHub | 延迟 + 下载 | Release 资源，自动跟随 302 到 objects.githubusercontent.com |
| Epic Games | 仅延迟 | 首页/接口有 403 防护，无公开大文件 |
| EA | 仅延迟 | 首页可达，无公开大文件 |
| 动视 | 仅延迟 | 首页可达，无公开大文件 |
| NexusMods | 仅延迟 | 403 防护，无公开大文件 |

要换成你自己的 CDN，改 `engine.py` 里的 `TARGETS`。

## 常见错误

| 错误 | 原因 / 处理 |
| --- | --- |
| `HTTP 400: Bad Request` | 最常见是模型名填成了 `nomic-embed-text` 这类 embedding 模型；点「测试连接」自动选对话模型，或手动改成 `qwen2.5:14b` |
| `HTTP 404` | Base URL 路径不对；只填到域名（如 `https://api.deepseek.com`），程序会自动补 `/v1` |
| `HTTP 401/403` | API Key 错误或没有权限 |
| `连接失败: ...` | 本地 Ollama 没启动，或云端地址/网络不通 |

## 文件

```text
netspeed/
├── engine.py              # 测速 + 规则总结 + LLM API
├── app.py                 # tkinter GUI（测速 / API 设置）
├── config.json            # 运行时生成，保存 API 配置
├── 启动测速.bat
├── netspeed-portable.zip
├── dist/netspeed/         # netspeed.exe + _internal
└── README.md
```

## 依赖

源码需要 Python 3.9+（标准库）；EXE 不需要 Python。

## 国内节点（v1.2 新增）

- **华为云**：`mirrors.huaweicloud.com`，Range 下载，实测可跑满
- **网易**：`mirrors.163.com`，Range 下载

桌面版把这两个节点放在 `domestic` 类别，单独一行统计“国内节点下载最高”；失败照样显示。

手机浏览器版受跨域限制，国内镜像没有 CORS 头，无法读字节，所以只保留 Cloudflare；**安卓 APK 和桌面版**会直接测这两个国内节点。
