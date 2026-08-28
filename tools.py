# -*- coding: utf-8 -*-
"""MyPi 工具集模块

契约（mypi.py 按此调用）：
    TOOL_SCHEMAS: list[dict]  OpenAI function-calling 格式的工具定义
    execute_tool(name: str, args: dict) -> str  执行工具并返回字符串结果

工具清单（7 个）：
    bash / read_file / write_file / edit_file / list_dir / web_fetch / web_search

依赖：仅标准库 + requests
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from urllib.parse import quote, unquote, parse_qs, urlparse

import requests

MEMORY_FILE = os.path.expanduser("~/.mypi/memory.md")   # 跨会话记忆文件

MAX_OUTPUT = 6000        # bash 输出截断
MAX_FILE = 10000         # 单文件读取截断
MAX_WEB = 8000           # 网页文本截断
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ---------------------------------------------------------------- 危险命令过滤
_DANGEROUS = [
    r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)",   # rm -rf / -fr
    r"mkfs(\.|\s)",
    r"dd\s+if=.*of=/dev/",
    r":()\{\s*:\|:&\s*\};:",                      # fork bomb
    r"del\s+/[fqs].*c:\\",
    r"rd\s+/s",
    r"format\s+[a-z]:",
    r"diskpart",
    r"reg\s+delete\s+hklm",
]


def _is_dangerous(cmd: str) -> bool:
    low = cmd.lower()
    return any(re.search(p, low) for p in _DANGEROUS)


# ---------------------------------------------------------------- 工具实现
def _bash(args: dict) -> str:
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return "Error: command 不能为空"
    if _is_dangerous(cmd):
        return "Error: 检测到潜在破坏性命令，已拒绝执行。如确需执行请用户手动操作。"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           timeout=60, cwd=os.getcwd())
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        text = (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()
        if not text:
            text = "(无输出, 退出码 %d)" % r.returncode
        if len(text) > MAX_OUTPUT:
            text = text[:MAX_OUTPUT] + f"\n...[截断，原始长度 {len(text)}]"
        return text
    except subprocess.TimeoutExpired:
        return "Error: 命令执行超过 60 秒超时"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _read_file(args: dict) -> str:
    path = (args.get("path") or "").strip()
    if not path:
        return "Error: path 不能为空"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(MAX_FILE)
        if len(data) == MAX_FILE:
            data += f"\n...[截断，仅显示前 {MAX_FILE} 字符]"
        return data or "(空文件)"
    except FileNotFoundError:
        return f"Error: 文件不存在: {path}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _write_file(args: dict) -> str:
    path = (args.get("path") or "").strip()
    content = args.get("content", "")
    if not path:
        return "Error: path 不能为空"
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        size = os.path.getsize(path)
        return f"OK: 已写入 {path}（{size} 字节）"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _edit_file(args: dict) -> str:
    path = (args.get("path") or "").strip()
    old = args.get("old_text", "")
    new = args.get("new_text", "")
    if not path or not old:
        return "Error: path 与 old_text 不能为空"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        if old not in src:
            return ("Error: old_text 在文件中未找到精确匹配。"
                    "请先 read_file 确认原文（注意空格与换行）。")
        src = src.replace(old, new, 1)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        return f"OK: 已替换并保存 {path}"
    except FileNotFoundError:
        return f"Error: 文件不存在: {path}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _list_dir(args: dict) -> str:
    path = (args.get("path") or ".").strip()
    try:
        entries = sorted(os.scandir(path), key=lambda e: (e.is_file(), e.name))
        lines = []
        for i, e in enumerate(entries[:100]):
            kind = "DIR " if e.is_dir() else "FILE"
            size = "" if e.is_dir() else f" {e.stat().st_size:>10,} B"
            lines.append(f"{kind}  {e.name}{size}")
        if len(entries) > 100:
            lines.append(f"...[共 {len(entries)} 项，仅显示前 100]")
        return "\n".join(lines) or "(空目录)"
    except FileNotFoundError:
        return f"Error: 目录不存在: {path}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _html_to_text(html: str) -> str:
    """极简 HTML 转文本：去 script/style/标签/实体，压空行。"""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&") \
               .replace("&lt;", "<").replace("&gt;", ">") \
               .replace("&quot;", '"').replace("&#39;", "'")
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n+", "\n\n", html)
    return html.strip()


def _web_fetch(args: dict) -> str:
    url = (args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Error: url 必须以 http:// 或 https:// 开头"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA})
        if r.status_code >= 400:
            return f"Error: HTTP {r.status_code} - {url}"
        r.encoding = r.encoding or r.apparent_encoding
        text = _html_to_text(r.text)
        if len(text) > MAX_WEB:
            text = text[:MAX_WEB] + f"\n...[截断，全文 {len(text)} 字符]"
        return text or "(页面无文本内容)"
    except requests.exceptions.Timeout:
        return "Error: 请求超时（30 秒）"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _web_search(args: dict) -> str:
    query = (args.get("query") or "").strip()
    max_results = int(args.get("max_results") or 5)
    if not query:
        return "Error: query 不能为空"
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": query}, timeout=30,
                          headers={"User-Agent": UA})
        if r.status_code >= 400:
            return f"Error: 搜索服务返回 HTTP {r.status_code}"
        r.encoding = r.apparent_encoding
        html = r.text

        items = []
        # 每条结果：链接 + 标题 + 摘要
        blocks = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
            r'(?:.*?class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>)?',
            html, re.S)
        for href, title, snippet in blocks:
            # 还原 DDG 重定向里的真实 URL
            if "uddg=" in href:
                try:
                    qs = parse_qs(urlparse(unquote(href)).query)
                    href = qs.get("uddg", [href])[0]
                except Exception:  # noqa: BLE001
                    pass
            clean = lambda s: re.sub(r"<[^>]+>", "", s or "").strip()  # noqa: E731
            items.append({"title": clean(title), "url": href,
                          "snippet": clean(snippet)[:200]})
            if len(items) >= max_results:
                break

        if not items:
            return "Error: 未搜索到结果（搜索引擎可能限流，稍后重试或换关键词）"
        lines = []
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. {it['title']}\n   {it['url']}\n   {it['snippet']}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _memory(args: dict) -> str:
    """长期记忆：save 追加一条 / read 全部读取 / clear 清空"""
    action = (args.get("action") or "read").strip().lower()
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    if action == "save":
        content = (args.get("content") or "").strip()
        if not content:
            return "Error: save 需要 content 参数"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"- [{stamp}] {content}\n")
        n = sum(1 for _ in open(MEMORY_FILE, encoding="utf-8"))
        return f"OK: 已记住（记忆共 {n} 条）"
    if action == "clear":
        open(MEMORY_FILE, "w", encoding="utf-8").close()
        return "OK: 记忆已清空"
    # 默认 read
    if os.path.exists(MEMORY_FILE):
        data = open(MEMORY_FILE, encoding="utf-8").read().strip()
        if len(data) > 6000:
            data = data[:6000] + "\n...[截断]"
        return data or "(记忆为空)"
    return "(记忆为空)"


# ---------------------------------------------------------------- 分发表
_DISPATCH = {
    "bash": _bash,
    "read_file": _read_file,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "list_dir": _list_dir,
    "web_fetch": _web_fetch,
    "web_search": _web_search,
    "memory": _memory,
}


def execute_tool(name: str, args: dict) -> str:
    """执行工具，永远返回字符串（错误也字符串化，不抛异常）。"""
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"Error: 未知工具 {name}，可用: {', '.join(_DISPATCH)}"
    if not isinstance(args, dict):
        return "Error: 参数必须是 JSON 对象"
    try:
        return fn(args)
    except Exception as e:  # noqa: BLE001
        return f"Error: 工具 {name} 执行异常: {e}"


# ---------------------------------------------------------------- Schema 定义
_TOOL_DEFS = [
    ("bash", "在当前工作目录执行 shell 命令并返回输出（超时60秒，禁止破坏性命令）",
     {"type": "object", "properties": {
         "command": {"type": "string", "description": "要执行的命令"}},
      "required": ["command"]}),
    ("read_file", "读取本地文件内容（utf-8，超长截断）",
     {"type": "object", "properties": {
         "path": {"type": "string", "description": "文件路径（相对或绝对）"}},
      "required": ["path"]}),
    ("write_file", "写入/覆盖本地文件（自动创建父目录）",
     {"type": "object", "properties": {
         "path": {"type": "string", "description": "文件路径"},
         "content": {"type": "string", "description": "完整文件内容"}},
      "required": ["path", "content"]}),
    ("edit_file", "精确替换文件中的一段文本（仅替换第一处匹配）",
     {"type": "object", "properties": {
         "path": {"type": "string", "description": "文件路径"},
         "old_text": {"type": "string", "description": "要被替换的原文（需精确匹配）"},
         "new_text": {"type": "string", "description": "替换后的新文本"}},
      "required": ["path", "old_text", "new_text"]}),
    ("list_dir", "列出目录内容（名称/类型/大小）",
     {"type": "object", "properties": {
         "path": {"type": "string", "description": "目录路径，默认当前目录"}},
      "required": []}),
    ("web_fetch", "抓取网页并转为纯文本（自动去标签，截取前8000字符）",
     {"type": "object", "properties": {
         "url": {"type": "string", "description": "完整 URL（http/https）"}},
      "required": ["url"]}),
    ("web_search", "联网搜索（DuckDuckGo，免 API Key），返回标题+链接+摘要",
     {"type": "object", "properties": {
         "query": {"type": "string", "description": "搜索关键词"},
         "max_results": {"type": "integer", "description": "返回条数，默认5"}},
      "required": ["query"]}),
    ("memory", "跨会话长期记忆：save 追加一条值得记住的信息（用户偏好/项目背景/重要决定）；read 读取全部记忆；clear 清空",
     {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["save", "read", "clear"],
                     "description": "操作类型"},
         "content": {"type": "string", "description": "要记住的内容（action=save 时必填）"}},
      "required": ["action"]}),
]

TOOL_SCHEMAS = [
    {"type": "function",
     "function": {"name": n, "description": d, "parameters": p}}
    for n, d, p in _TOOL_DEFS
]


if __name__ == "__main__":
    # 自检：python tools.py
    print("工具数:", len(TOOL_SCHEMAS))
    for t in TOOL_SCHEMAS:
        print(" -", t["function"]["name"], "|", t["function"]["description"][:30])
    print("\n自测 web_fetch(example.com):")
    print(execute_tool("web_fetch", {"url": "https://example.com"})[:200])
