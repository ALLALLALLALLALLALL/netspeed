#!/usr/bin/env python3
"""netspeed mobile server.

Serves a phone-friendly page and a tiny JSON API:

  GET  /                 page
  GET  /api/config       cloud API settings (key never returned)
  POST /api/config       save cloud API settings
  POST /api/nodes        server-side latency probe for game services
  POST /api/summary      rule summary + optional cloud AI summary

Bandwidth is measured **in the phone browser** against Cloudflare (CORS
enabled), so the numbers describe the phone's link. Game-service latency is
probed from this PC because those sites do not allow cross-origin reads; the
page labels that clearly instead of pretending it is the phone's latency.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "netspeed-mobile")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
UA = "netspeed-mobile/1.0"

NODES = [
    ("Steam", "https://store.steampowered.com/"),
    ("Epic Games", "https://www.epicgames.com/"),
    ("EA", "https://www.ea.com/"),
    ("动视", "https://www.activision.com/"),
    ("NexusMods", "https://www.nexusmods.com/"),
    ("GitHub", "https://github.com/"),
]

DEFAULT_CONFIG = {"api": {"base_url": "", "api_key": "", "model": "", "timeout": 90}}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data.get("api"), dict):
                cfg["api"].update(data["api"])
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def _conn(url: str, timeout: float = 8.0):
    from urllib.parse import urlsplit
    u = urlsplit(url)
    host = u.hostname or ""
    port = u.port or 443
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    c = urllib.request.HTTPSHandler  # keep import explicit
    import http.client
    conn = http.client.HTTPSConnection(host, port=port, timeout=timeout,
                                       context=ssl.create_default_context())
    conn.connect()
    return conn, path


def probe_node(name: str, url: str, attempts: int = 2) -> dict:
    connect, ttfb, statuses, errors = [], [], [], []
    for _ in range(attempts):
        t0 = time.perf_counter()
        conn = None
        try:
            conn, path = _conn(url)
            c_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            conn.request("GET", path, headers={"User-Agent": UA, "Accept-Encoding": "identity",
                                               "Range": "bytes=0-0", "Connection": "close"})
            resp = conn.getresponse()
            resp.read(1)
            t_ms = (time.perf_counter() - t1) * 1000
            statuses.append(resp.status)
            connect.append(round(c_ms, 1))
            ttfb.append(round(t_ms, 1))
            resp.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return {
        "name": name,
        "status": statuses[-1] if statuses else None,
        "connect_ms": statistics.median(connect) if connect else None,
        "ttfb_ms": statistics.median(ttfb) if ttfb else None,
        "jitter_ms": round(statistics.pstdev(ttfb), 1) if len(ttfb) > 1 else 0.0,
        "loss_pct": round((attempts - len(ttfb)) * 100 / attempts, 1),
        "errors": errors,
    }


def rule_summary(report: dict) -> str:
    dl = report.get("download") or {}
    ul = report.get("upload") or {}
    lat = report.get("latency") or {}
    lines = ["规则总结："]
    d = dl.get("mbps")
    if d:
        level = "偏低" if d < 10 else "入门" if d < 30 else "日常够用" if d < 100 else "比较快" if d < 300 else "很快"
        lines.append(f"- 手机下载 {d:.2f} Mbps（{level}），{dl.get('megabytes', 0):.2f} MB / {dl.get('seconds', 0):.2f}s。")
    else:
        lines.append("- 手机下载没有拿到有效数据。")
    u = ul.get("mbps")
    if u:
        lines.append(f"- 手机上传 {u:.2f} Mbps，{ul.get('megabytes', 0):.2f} MB / {ul.get('seconds', 0):.2f}s。")
    else:
        lines.append("- 手机上传没有拿到有效数据。")
    loc = report.get("local") or {}
    if loc.get("mbps"):
        lines.append(f"- 局域网下载 {loc['mbps']:.2f} Mbps（手机到电脑 Wi-Fi，不走互联网）。")
    else:
        lines.append("- 局域网下载没有拿到有效数据。")
    if lat.get("median") is not None:
        lines.append(f"- Cloudflare 延迟中位数 {lat['median']:.1f} ms，抖动 {lat.get('jitter', 0):.1f} ms。")
    else:
        lines.append("- 延迟没有有效样本。")
    bad = []
    if dl.get("error"):
        bad.append("下载: " + str(dl["error"]))
    if ul.get("error"):
        bad.append("上传: " + str(ul["error"]))
    if bad:
        lines.append("- 失败项：" + "；".join(bad))
    lines.append("- 以上只依据手机浏览器实测字节与耗时，未估算。")
    return "\n".join(lines)


def cloud_summary(report: dict, cfg: dict) -> tuple[str | None, str | None]:
    api = cfg.get("api", {})
    base = (api.get("base_url") or "").rstrip("/")
    if not base:
        return None, None
    if not base.endswith("/v1"):
        base += "/v1"
    model = (api.get("model") or "").strip()
    if not model:
        return None, "已配置 Base URL，但没有填写模型名"
    if "embed" in model.lower():
        return None, f"模型 {model} 是 embedding 模型，不能聊天；请换对话模型"
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": "你是网络测速分析员。只能使用用户给出的实测数字，不许编造或估算。用中文 5-8 行总结手机下载/上传、延迟和失败项。"},
            {"role": "user", "content": "实测 JSON：\n" + json.dumps(report, ensure_ascii=False)[:10000]},
        ],
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + (api.get("api_key") or "none")},
            method="POST")
        with urllib.request.urlopen(req, timeout=float(api.get("timeout") or 90)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return (text.strip() or None), None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        return None, f"HTTP {exc.code} @ {url} model={model}: {body or exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{exc} @ {url} model={model}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.address_string(), fmt % args), flush=True)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            path = os.path.join(BASE_DIR, "index.html")
            try:
                data = open(path, "rb").read()
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/localfile"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                mb = int(qs.get("mb", ["50"])[0])
            except Exception:
                mb = 50
            mb = max(1, min(200, mb))
            size = mb * 1024 * 1024
            chunk = b"\0" * (1024 * 1024)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            left = size
            try:
                while left > 0:
                    n = min(len(chunk), left)
                    self.wfile.write(chunk[:n])
                    left -= n
            except Exception:
                pass
            return
        if self.path == "/api/config":
            cfg = load_config()
            self._json({"base_url": cfg["api"].get("base_url", ""),
                        "model": cfg["api"].get("model", ""),
                        "has_key": bool(cfg["api"].get("api_key"))})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        try:
            body = self._body()
            if self.path == "/api/config":
                cfg = load_config()
                cfg["api"]["base_url"] = (body.get("base_url") or "").strip()
                cfg["api"]["model"] = (body.get("model") or "").strip()
                if body.get("api_key"):
                    cfg["api"]["api_key"] = body["api_key"].strip()
                save_config(cfg)
                self._json({"ok": True})
                return
            if self.path == "/api/nodes":
                self._json({"nodes": [probe_node(n, u) for n, u in NODES]})
                return
            if self.path == "/api/summary":
                report = body.get("report") or {}
                cfg = load_config()
                rule = rule_summary(report)
                ai, err = cloud_summary(report, cfg)
                self._json({"rule": rule, "ai": ai, "error": err})
                return
            self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 400)


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("netspeed mobile 已启动")
    print(f"电脑本机: http://127.0.0.1:{PORT}/")
    print(f"手机访问: http://{lan_ip()}:{PORT}/   （同一 Wi-Fi）")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
