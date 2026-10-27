#!/usr/bin/env python3
"""netspeed — honest network + game-service node speed test.

Honesty rules:
  * only bytes actually read/written are counted;
  * every failed request is recorded and shown;
  * services without a public large file are marked "latency only" instead of
    inventing a download number;
  * the AI summary (if configured) only restates the measured numbers; if the
    API fails, the rule-based summary is shown and the error is reported.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import random
import socket
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional

UA = "netspeed/2.0 (honest speed test)"
CHUNK = 256 * 1024
UPLOAD_CHUNK = 1024 * 1024
CF_BYTES = 25 * 1024 * 1024


@dataclass
class Target:
    key: str
    name: str
    category: str            # "general" | "service"
    latency_url: str
    download_url: Optional[str] = None
    download_kind: str = "range"   # "range" | "cloudflare"
    streams: int = 1
    offset: int = 0
    note: str = ""


TARGETS: List[Target] = [
    # 通用带宽
    Target("ovh", "OVH", "general",
           "https://proof.ovh.net/files/1Gb.dat",
           "https://proof.ovh.net/files/1Gb.dat", "range", 1, 64 * 1024 * 1024),
    Target("cloudflare", "Cloudflare", "general",
           "https://speed.cloudflare.com/__down?bytes=1",
           "https://speed.cloudflare.com/__down?bytes={bytes}", "cloudflare", 4, 0),
    # 国内节点
    Target("huawei", "华为云 (CN)", "domestic",
           "https://mirrors.huaweicloud.com/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           "https://mirrors.huaweicloud.com/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           "range", 1, 64 * 1024 * 1024, "国内镜像"),
    Target("netease", "网易 (CN)", "domestic",
           "https://mirrors.163.com/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           "https://mirrors.163.com/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           "range", 1, 64 * 1024 * 1024, "国内镜像"),
    Target("aliyun", "阿里云 (CN)", "domestic",
           "https://mirrors.aliyun.com/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           None, note="仅延迟：302 跳转到 HTTP，下载不可达"),
    Target("tencent", "腾讯云 (CN)", "domestic",
           "https://mirrors.cloud.tencent.com/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           None, note="仅延迟：当前路径 404"),
    Target("ustc", "中科大 (CN)", "domestic",
           "https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           None, note="仅延迟：403 防护"),
    Target("tuna", "清华 (CN)", "domestic",
           "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           None, note="仅延迟：403 防护"),
    Target("bfsu", "北外 (CN)", "domestic",
           "https://mirrors.bfsu.edu.cn/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           None, note="仅延迟：403 防护"),
    Target("nju", "南大 (CN)", "domestic",
           "https://mirrors.nju.edu.cn/ubuntu-releases/24.04/ubuntu-24.04.4-desktop-amd64.iso",
           None, note="仅延迟：302 重定向"),
    # 游戏平台 / 社区 / 开发
    Target("steam", "Steam", "service",
           "https://store.steampowered.com/",
           "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe",
           "range", 1, 0, "安装包约 2-3MB，短时吞吐"),
    Target("epic", "Epic Games", "service",
           "https://www.epicgames.com/", None, note="无公开大文件，仅节点延迟"),
    Target("ea", "EA", "service",
           "https://www.ea.com/", None, note="无公开大文件，仅节点延迟"),
    Target("activision", "动视", "service",
           "https://www.activision.com/", None, note="无公开大文件，仅节点延迟"),
    Target("nexus", "NexusMods", "service",
           "https://www.nexusmods.com/", None, note="无公开大文件，仅节点延迟"),
    Target("github", "GitHub", "service",
           "https://github.com/",
           "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.3/Git-2.56.0.3-64-bit.exe",
           "range", 1, 0, "官方 Release 资源"),
]

UPLOAD_URL = "https://speed.cloudflare.com/__up"

DEFAULT_CONFIG: Dict = {
    "api": {
        "enabled": False,
        "mode": "local",  # local | cloud
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "model": "",
        "timeout": 90,
    },
    "test": {
        "duration": 4.0,
        "streams": 3,
        "latency_attempts": 3,
    },
}


def start_local_server(port: int = 8768):
    """Serve a 50MB zero file on the LAN so the local/NIC path can be measured."""
    size = 50 * 1024 * 1024
    chunk = b"\0" * (1024 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/localfile"):
                self.send_error(404)
                return
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

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def measure_local(duration: float, progress=None) -> Dict:
    srv = None
    try:
        srv = start_local_server()
        t = Target("lan", "本机/局域网", "local",
                   f"http://{lan_ip()}:8768/localfile", f"http://{lan_ip()}:8768/localfile",
                   "local", 1, 0, "本机文件服务，测本机/局域网路径")
        return measure_download(t, duration, 1, progress).to_dict()
    except Exception as exc:  # noqa: BLE001
        return DownloadResult("本机/局域网", "local", None, 0, 0, 1, 1, [str(exc)]).to_dict()
    finally:
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass


def config_path() -> str:
    # Always store the user config in a writable per-user directory; the exe
    # may live in a read-only or temporary folder.
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "netspeed")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    path = os.path.join(folder, "config.json")
    # Migrate an older config.json sitting next to the exe/script.
    if not os.path.exists(path):
        legacy = os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                                            else os.path.abspath(__file__)), "config.json")
        try:
            if os.path.exists(legacy):
                with open(legacy, "r", encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
        except Exception:
            pass
    return path


def load_config() -> Dict:
    path = config_path()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for section in ("api", "test"):
                if isinstance(data.get(section), dict):
                    cfg[section].update(data[section])
        except Exception:
            pass
    return cfg


def save_config(cfg: Dict) -> None:
    with open(config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- HTTP
def _split(url: str):
    u = urllib.parse.urlsplit(url)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return u.hostname or "", u.port or 443, path


def _connect(url: str, timeout: float = 10.0) -> http.client.HTTPConnection:
    host, port, _ = _split(url)
    if url.startswith("http://"):
        if not urllib.parse.urlsplit(url).port:
            port = 80
        conn = http.client.HTTPConnection(host, port=port, timeout=timeout)
    else:
        conn = http.client.HTTPSConnection(host, port=port, timeout=timeout,
                                           context=ssl.create_default_context())
    conn.connect()
    return conn


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    base = {"User-Agent": UA, "Accept-Encoding": "identity", "Connection": "keep-alive"}
    if extra:
        base.update(extra)
    return base


def _download_path(t: Target, offset: int) -> str:
    if t.download_kind == "cloudflare":
        return f"/__down?bytes={CF_BYTES}"
    _, _, path = _split(t.download_url or "")
    return path


def _download_headers(t: Target, offset: int) -> Dict[str, str]:
    if t.download_kind == "cloudflare":
        return _headers()
    return _headers({"Range": f"bytes={offset}-"})


# --------------------------------------------------------------------------- measure
@dataclass
class LatencyResult:
    target: str
    category: str
    attempts: int = 0
    failures: int = 0
    connect_ms: List[float] = field(default_factory=list)
    ttfb_ms: List[float] = field(default_factory=list)
    statuses: List[int] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        def stats(values: List[float]) -> Dict:
            if not values:
                return {"min": None, "median": None, "p95": None, "jitter": None}
            ordered = sorted(values)
            p95 = ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))]
            return {
                "min": round(min(values), 2),
                "median": round(statistics.median(values), 2),
                "p95": round(p95, 2),
                "jitter": round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0,
            }

        return {
            "target": self.target,
            "category": self.category,
            "attempts": self.attempts,
            "failures": self.failures,
            "loss_pct": round(0 if not self.attempts else self.failures * 100 / self.attempts, 1),
            "status": self.statuses[-1] if self.statuses else None,
            "connect_ms": stats(self.connect_ms),
            "ttfb_ms": stats(self.ttfb_ms),
            "errors": self.errors,
        }


@dataclass
class DownloadResult:
    target: str
    category: str
    mbps: Optional[float]
    megabytes: float
    seconds: float
    streams: int
    failed_streams: int = 0
    errors: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


def measure_latency(t: Target, attempts: int = 3) -> LatencyResult:
    res = LatencyResult(target=t.name, category=t.category)
    for _ in range(attempts):
        res.attempts += 1
        t0 = time.perf_counter()
        conn = None
        try:
            conn = _connect(t.latency_url, timeout=10)
            conn_ms = (time.perf_counter() - t0) * 1000
            path = "/__down?bytes=1" if "speed.cloudflare.com" in t.latency_url else _split(t.latency_url)[2]
            headers = _headers({"Range": "bytes=0-0"})
            t1 = time.perf_counter()
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            resp.read(1)
            ttfb = (time.perf_counter() - t1) * 1000
            res.statuses.append(resp.status)
            res.connect_ms.append(conn_ms)
            res.ttfb_ms.append(ttfb)
            resp.close()
        except Exception as exc:  # noqa: BLE001
            res.failures += 1
            res.errors.append(str(exc))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return res


def _resolve_url(url: str, timeout: float = 10.0) -> str:
    """Follow redirects (GitHub release assets redirect to objects.githubusercontent.com)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except Exception:  # noqa: BLE001
        return url


def measure_download(t: Target, duration: float, streams: int,
                     progress: Optional[Callable[[str, float], None]] = None) -> DownloadResult:
    streams = max(1, min(streams, t.streams))
    state = {"bytes": 0, "start": 0.0, "deadline": 0.0}
    lock = threading.Lock()
    errors: List[str] = []
    failed = {"n": 0}
    barrier = threading.Barrier(streams + 1)
    resolved_url = t.download_url or ""
    if t.download_kind == "range" and resolved_url:
        resolved_url = _resolve_url(resolved_url)
    resolved_path = (f"/__down?bytes={CF_BYTES}" if t.download_kind == "cloudflare"
                     else _split(resolved_url)[2])

    def worker(idx: int) -> None:
        conn = None
        try:
            conn = _connect(resolved_url, timeout=10)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"stream {idx}: connect: {exc}")
                failed["n"] += 1
            try:
                barrier.abort()
            except Exception:
                pass
            return
        try:
            barrier.wait(timeout=20)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            conn.sock.settimeout(5.0)
            offset = t.offset + idx * 64 * 1024 * 1024 if t.offset else 0
            while time.perf_counter() < state["deadline"]:
                conn.request("GET", resolved_path, headers=_download_headers(t, offset))
                resp = conn.getresponse()
                if resp.status not in (200, 206):
                    raise RuntimeError(f"HTTP {resp.status}")
                got = 0
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    got += len(chunk)
                    with lock:
                        state["bytes"] += len(chunk)
                    if time.perf_counter() >= state["deadline"]:
                        break
                resp.close()
                if got == 0:
                    break
                offset += got
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"stream {idx}: {exc}")
                failed["n"] += 1
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(streams)]
    for th in threads:
        th.start()
    state["start"] = time.perf_counter()
    state["deadline"] = state["start"] + duration
    try:
        barrier.wait(timeout=20)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"barrier: {exc}")
        failed["n"] = streams
    last = -10.0
    while any(th.is_alive() for th in threads):
        time.sleep(0.2)
        pct = min(100.0, (time.perf_counter() - state["start"]) / duration * 100)
        if pct - last >= 10:
            if progress:
                progress(f"download:{t.name}", pct)
            last = pct
    for th in threads:
        th.join(timeout=3)
    elapsed = max(0.001, time.perf_counter() - state["start"])
    with lock:
        total = state["bytes"]
    if total == 0:
        failed["n"] = max(failed["n"], streams)
        errors.append("no data received")
    mbps = round(total * 8 / elapsed / 1e6, 2) if total else 0.0
    return DownloadResult(
        target=t.name, category=t.category,
        mbps=mbps if total else None,
        megabytes=round(total / 1e6, 2),
        seconds=round(elapsed, 2),
        streams=streams,
        failed_streams=failed["n"],
        errors=errors,
        note=t.note,
    )


def _upload_payload() -> bytes:
    return bytes(random.getrandbits(8) for _ in range(UPLOAD_CHUNK))


def measure_upload(duration: float, streams: int,
                   progress: Optional[Callable[[str, float], None]] = None) -> DownloadResult:
    state = {"bytes": 0, "start": 0.0, "deadline": 0.0}
    lock = threading.Lock()
    errors: List[str] = []
    failed = {"n": 0}
    barrier = threading.Barrier(streams + 1)
    payload = _upload_payload()

    def worker(idx: int) -> None:
        conn = None
        try:
            conn = _connect(UPLOAD_URL, timeout=10)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"stream {idx}: connect: {exc}")
                failed["n"] += 1
            try:
                barrier.abort()
            except Exception:
                pass
            return
        try:
            barrier.wait(timeout=20)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            while time.perf_counter() < state["deadline"]:
                conn.request("POST", "/__up", body=payload, headers=_headers({
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(payload)),
                }))
                resp = conn.getresponse()
                resp.read()
                if resp.status not in (200, 201, 204):
                    raise RuntimeError(f"HTTP {resp.status}")
                resp.close()
                with lock:
                    state["bytes"] += len(payload)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"stream {idx}: {exc}")
                failed["n"] += 1
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(streams)]
    for th in threads:
        th.start()
    state["start"] = time.perf_counter()
    state["deadline"] = state["start"] + duration
    try:
        barrier.wait(timeout=20)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"barrier: {exc}")
        failed["n"] = streams
    last = -10.0
    while any(th.is_alive() for th in threads):
        time.sleep(0.2)
        pct = min(100.0, (time.perf_counter() - state["start"]) / duration * 100)
        if pct - last >= 10:
            if progress:
                progress("upload", pct)
            last = pct
    for th in threads:
        th.join(timeout=3)
    elapsed = max(0.001, time.perf_counter() - state["start"])
    with lock:
        total = state["bytes"]
    if total == 0:
        failed["n"] = max(failed["n"], streams)
        errors.append("no data received")
    mbps = round(total * 8 / elapsed / 1e6, 2) if total else 0.0
    return DownloadResult(
        target="Cloudflare __up", category="general",
        mbps=mbps if total else None,
        megabytes=round(total / 1e6, 2),
        seconds=round(elapsed, 2),
        streams=streams,
        failed_streams=failed["n"],
        errors=errors,
    )


# --------------------------------------------------------------------------- summary
def rule_summary(report: Dict) -> str:
    downloads = [d for d in report.get("downloads", []) if d.get("mbps")]
    services = [s for s in downloads if s.get("category") == "service"]
    general = [g for g in downloads if g.get("category") == "general"]
    domestic = [d for d in downloads if d.get("category") == "domestic"]
    latency = report.get("latency", [])
    lines = ["总结（规则生成，只读本次实测数字）："]

    def level(mbps: float) -> str:
        if mbps < 10:
            return "偏低（可能被限速、Wi-Fi 或线路拥塞）"
        if mbps < 30:
            return "入门，网页/1080p 够用"
        if mbps < 100:
            return "日常够用"
        if mbps < 300:
            return "比较快"
        return "很快"

    if general:
        best = max(general, key=lambda d: d["mbps"])
        lines.append(f"- 通用下载：最高 {best['mbps']:.2f} Mbps（{best['target']}），{level(best['mbps'])}。")
    else:
        lines.append("- 通用下载：没有拿到有效数据。")

    if services:
        svc = max(services, key=lambda d: d["mbps"])
        lines.append(f"- 平台节点下载：最高 {svc['mbps']:.2f} Mbps（{svc['target']}）。")
    else:
        lines.append("- 平台节点：没有可用的下载样本。")

    if domestic:
        dom = max(domestic, key=lambda d: d["mbps"])
        lines.append(f"- 国内节点下载：最高 {dom['mbps']:.2f} Mbps（{dom['target']}）。")
    else:
        lines.append("- 国内节点：没有可用的下载样本。")

    up = report.get("upload") or {}
    if up.get("mbps"):
        lines.append(f"- 上传：{up['mbps']:.2f} Mbps，{level(up['mbps'])}。")
    else:
        lines.append("- 上传：没有拿到有效数据。")

    valid_lat = [x for x in latency if x.get("ttfb_ms", {}).get("median") is not None]
    if valid_lat:
        best_lat = min(valid_lat, key=lambda x: x["ttfb_ms"]["median"])
        lines.append(
            f"- 节点延迟：最低 {best_lat['ttfb_ms']['median']:.1f} ms（{best_lat['target']}），"
            f"抖动 {best_lat['ttfb_ms']['jitter']:.1f} ms，丢包 {best_lat['loss_pct']:.1f}%。"
        )
    else:
        lines.append("- 节点延迟：没有有效样本。")

    failed = [d["target"] for d in report.get("downloads", []) if d.get("failed_streams") or d.get("errors")]
    if failed:
        lines.append(f"- 失败/异常：{'、'.join(failed)}（保留在报告里，未删除）。")
    lines.append("- 无公开大文件的平台只测延迟，不会编造下载速度。")
    return "\n".join(lines)


def llm_summary(cfg: Dict, report: Dict) -> tuple[Optional[str], Optional[str]]:
    api = cfg.get("api", {})
    if not api.get("enabled"):
        return None, None
    base = (api.get("base_url") or "").rstrip("/")
    if base and not base.endswith("/v1"):
        base += "/v1"
    model = (api.get("model") or "").strip()
    if not base:
        return None, "已启用 AI 总结，但 Base URL 为空"
    if not model:
        return None, "已启用 AI 总结，但没有填写模型名"
    if "embed" in model.lower():
        return None, f"模型 {model} 是 embedding 模型，不能用于聊天总结；请改选 qwen2.5:14b 等对话模型"
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": "你是网络测速报告分析员。只能使用用户提供的实测数字，绝对不许编造、估算或美化。用中文给出 5-8 行简短总结，指出下载/上传水平、最好与最差的节点、失败项。不要重复原始表格。"},
            {"role": "user", "content": "以下是本次测速的原始结果 JSON：\n" + json.dumps(report, ensure_ascii=False)[:12000]},
        ],
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {api.get('api_key') or 'none'}"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=float(api.get("timeout") or 90)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return (text.strip() or None), None
    except urllib.error.HTTPError as exc:  # noqa: BLE001
        try:
            body = exc.read().decode("utf-8", "replace")[:600]
        except Exception:
            body = ""
        return None, f"HTTP {exc.code} @ {url} model={model}: {body or exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{exc} @ {url} model={model}"


def list_models(cfg: Dict) -> tuple[List[str], Optional[str]]:
    api = cfg.get("api", {})
    base = (api.get("base_url") or "").rstrip("/")
    if base and not base.endswith("/v1"):
        base += "/v1"
    if not base:
        return [], "base_url 为空"
    url = base + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api.get('api_key') or 'none'}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        names = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return names, None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def test_api(cfg: Dict) -> tuple[bool, str]:
    names, err = list_models(cfg)
    if err:
        return False, err
    return True, "连接成功，模型: " + (", ".join(names[:8]) if names else "(未返回列表)")


# --------------------------------------------------------------------------- runner
def run_all(cfg: Dict, progress: Optional[Callable[[str, float], None]] = None) -> Dict:
    test = cfg.get("test", {})
    duration = float(test.get("duration") or 4)
    streams = int(test.get("streams") or 3)
    attempts = int(test.get("latency_attempts") or 3)
    report: Dict = {
        "tool": "netspeed",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {"duration_s": duration, "streams": streams, "latency_attempts": attempts},
        "latency": [],
        "downloads": [],
        "upload": None,
        "summary_rule": "",
        "summary_ai": None,
        "summary_ai_error": None,
    }

    for i, t in enumerate(TARGETS):
        if progress:
            progress(f"latency:{t.name}", i / len(TARGETS) * 100)
        report["latency"].append(measure_latency(t, attempts).to_dict())

    downloadable = [t for t in TARGETS if t.download_url]
    for i, t in enumerate(downloadable):
        if progress:
            progress(f"download:{t.name}", i / max(1, len(downloadable)) * 100)
        report["downloads"].append(measure_download(t, duration, streams, progress).to_dict())

    if progress:
        progress("upload", 0)
    report["upload"] = measure_upload(duration, streams, progress).to_dict()

    report["summary_rule"] = rule_summary(report)
    text, err = llm_summary(cfg, report)
    report["summary_ai"] = text
    report["summary_ai_error"] = err
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return report


def format_report(report: Dict) -> str:
    lines = ["netspeed — 真实节点测速", f"{report['started_at']} → {report['finished_at']}", ""]
    lines.append("节点延迟")
    lines.append(f"{'目标':<14}{'状态':>6}{'连接ms':>9}{'首字节ms':>10}{'抖动':>7}{'丢包':>7}  说明")
    for item in report["latency"]:
        c = item["connect_ms"]["median"]
        t = item["ttfb_ms"]["median"]
        j = item["ttfb_ms"]["jitter"]
        note = "、".join(item["errors"][:1])
        lines.append(
            f"{item['target']:<14}{str(item['status'] or '-'):>6}"
            f"{('-' if c is None else f'{c:.1f}'):>9}"
            f"{('-' if t is None else f'{t:.1f}'):>10}"
            f"{('-' if j is None else f'{j:.1f}'):>7}"
            f"{item['loss_pct']:>6.1f}%  {note}"
        )

    lines.append("")
    lines.append("下载 / 节点吞吐")
    lines.append(f"{'目标':<14}{'Mbps':>9}{'MB':>8}{'秒':>7}{'流':>4}{'失败':>5}  说明")
    for d in report["downloads"]:
        mbps = "-" if d["mbps"] is None else f"{d['mbps']:.2f}"
        note = d.get("note") or "; ".join(d["errors"][:1])
        lines.append(
            f"{d['target']:<14}{mbps:>9}{d['megabytes']:>8.2f}{d['seconds']:>7.2f}"
            f"{d['streams']:>4}{d['failed_streams']:>5}  {note}"
        )

    up = report.get("upload") or {}
    lines.append("")
    lines.append("上传")
    if up.get("mbps"):
        lines.append(f"Cloudflare __up   {up['mbps']:.2f} Mbps   {up['megabytes']:.2f} MB / {up['seconds']:.2f}s")
    else:
        lines.append("未获得有效数据")

    lines.append("")
    lines.append(report.get("summary_rule", ""))
    if report.get("summary_ai"):
        lines.append("")
        lines.append("AI 总结：")
        lines.append(report["summary_ai"])
    elif report.get("summary_ai_error"):
        lines.append("")
        lines.append(f"AI 总结失败（已保留规则总结）：{report['summary_ai_error']}")
    lines.append("")
    lines.append("说明: Mbps = 实际读写字节 × 8 ÷ 实际耗时；失败和重连都保留。")
    return "\n".join(lines)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="netspeed honest node speed test")
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--streams", type=int, default=None)
    parser.add_argument("--latency-attempts", type=int, default=None)
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    cfg = load_config()
    if args.duration:
        cfg["test"]["duration"] = args.duration
    if args.streams:
        cfg["test"]["streams"] = args.streams
    if args.latency_attempts:
        cfg["test"]["latency_attempts"] = args.latency_attempts

    def progress(phase: str, pct: float) -> None:
        print(f"\r{phase:<24} {pct:5.1f}%", end="", flush=True)

    report = run_all(cfg, progress)
    print("\r" + " " * 50 + "\r", end="")
    print(format_report(report))
    if args.json:
        import json as _json
        with open(args.json, "w", encoding="utf-8") as fh:
            _json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {os.path.abspath(args.json)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
