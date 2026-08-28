#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MyPi —— 最简命令行编码智能体（模仿 Pi 的核心功能）

用法：
    python mypi.py                     交互 REPL
    python mypi.py "帮我写个脚本"       单次任务
    python mypi.py --provider glm     指定供应商
    python mypi.py --model xxx        指定模型

配置：~/.mypi/config.json（首次运行自动生成示例）
"""
import argparse
import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if sys.stdin and sys.stdin.isatty() is False:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # 管道输入中文
except Exception:  # noqa: BLE001
    pass

from tools import TOOL_SCHEMAS, execute_tool, MEMORY_FILE  # noqa: E402

# ---- 富终端输出（rich 可选，未安装时自动降级纯文本）----
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    console = Console()
    HAS_RICH = True
except ImportError:
    console = None
    HAS_RICH = False

CONFIG_PATH = os.path.expanduser("~/.mypi/config.json")
SESSIONS_DIR = os.path.expanduser("~/.mypi/sessions")
PROTOCOL_TIMEOUT = 300   # LLM 请求超时（秒）
# 循环轮数上限改为配置项 maxRounds（cfg 里），0 = 不限制

DEFAULT_CONFIG = {
    "maxRounds": 0,
    "providers": {
        "autodlgpu": {
            "baseUrl": "http://localhost:8000/v1",
            "apiKey": "EMPTY",
            "api": "openai",
            "model": "gemma-4-12b",
        },
        "glm": {
            "baseUrl": "http://172.16.248.56:8715",
            "apiKey": "sk-替换成你的key",
            "api": "anthropic",
            "model": "glm-5.2",
        },
    },
    "defaultProvider": "autodlgpu",
}

SYSTEM_PROMPT = """你是 MyPi，一个运行在命令行里的极简编码智能体。

能力：通过工具读写文件、执行 shell 命令、联网搜索与浏览网页。
当前工作目录：{cwd}

原则：
1. 需要本地信息或操作文件时，主动调用工具，不要凭空猜测
2. 需要最新信息（新闻/文档/版本号）时，用 web_search 搜索，必要时 web_fetch 阅读原文
3. 回答用中文，简洁自然，像结对工程师对话；完成任务后简短总结即可，不要每次都长篇报告，用户追问时直接回应
4. 完成任务后给出简短总结"""


def build_system_prompt() -> str:
    """系统提示词 + 跨会话记忆注入"""
    prompt = SYSTEM_PROMPT.format(cwd=os.getcwd())
    if os.path.exists(MEMORY_FILE):
        mem = open(MEMORY_FILE, encoding="utf-8").read().strip()
        if mem:
            if len(mem) > 4000:
                mem = mem[:4000] + "\n...[截断]"
            prompt += (f"\n\n【长期记忆】以下是往期会话积累的记忆（memory 工具存档）：\n"
                       f"<memory>\n{mem}\n</memory>\n"
                       "对话中出现值得长期记住的信息（用户偏好、项目背景、重要决定）时，"
                       "调用 memory 工具(action=save)追加记录。")
    return prompt


# ================================================================ 输出层
def show_assistant(text: str):
    if not text:
        return
    if HAS_RICH:
        console.print(Panel(Markdown(text), title="MyPi", title_align="left",
                            border_style="cyan", padding=(0, 1)))
    else:
        print(f"\n{text}\n")


def show_tool_call(name: str, args: dict):
    preview = json.dumps(args, ensure_ascii=False)
    if len(preview) > 80:
        preview = preview[:80] + "…"
    if HAS_RICH:
        console.print(f"  [yellow]🔧 {name}[/][dim]({preview})[/]")
    else:
        print(f"  🔧 {name}({preview})")


def show_tool_result(result: str):
    one = result[:100].replace("\n", " ⏎ ") + ("…" if len(result) > 100 else "")
    if HAS_RICH:
        console.print(f"  [dim]└─ {one}[/]")
    else:
        print(f"  └─ {one}")


def spinner(text: str):
    import contextlib
    if HAS_RICH:
        return console.status(f"[cyan]{text}[/]")
    return contextlib.nullcontext()


def ask_prompt() -> str:
    if HAS_RICH and sys.stdin.isatty():
        return console.input("[bold green]你 > [/]")
    return input("你 > ")


# ================================================================ 会话持久化
def save_session(history: list, pname: str = "", model: str = ""):
    msgs = [m for m in history if m["role"] != "system"]
    if not msgs:
        return None
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    first = next((str(m.get("content"))[:30] for m in history
                  if m["role"] == "user"), "session")
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", first)
    path = os.path.join(SESSIONS_DIR, f"{ts}_{slug}.json")
    with open(path, "w", encoding="utf-8") as f:
        # ensure_ascii=True：非 ASCII 全部转义，彻底免疫代理字符/编码问题
        json.dump({"created": ts, "provider": pname, "model": model,
                   "history": history}, f, ensure_ascii=True, indent=2)
    return path


def list_sessions() -> list:
    if not os.path.isdir(SESSIONS_DIR):
        return []
    return [os.path.join(SESSIONS_DIR, n) for n in sorted(os.listdir(SESSIONS_DIR))
            if n.endswith(".json")]


def load_session(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("history", []), data.get("provider", ""), data.get("model", "")
    except Exception as e:  # noqa: BLE001
        print(f"[MyPi] 会话文件损坏，跳过: {e}")
        return [], "", ""


# ================================================================ 配置
def load_config(create: bool = True) -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    if not create:
        sys.exit(f"[MyPi] 配置不存在: {CONFIG_PATH}")
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    print(f"[MyPi] 已生成示例配置: {CONFIG_PATH}")
    print("[MyPi] 请编辑其中的 apiKey / baseUrl 后重新运行；"
          "本次将以示例配置继续尝试。\n")
    return json.loads(json.dumps(DEFAULT_CONFIG))


# ================================================================ LLM 客户端
class LLMError(Exception):
    pass


def _post(url: str, headers: dict, body: dict) -> dict:
    import requests
    try:
        r = requests.post(url, headers=headers, json=body,
                          timeout=PROTOCOL_TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise LLMError(f"无法连接 {url} —— 检查服务是否启动、"
                       f"baseUrl 是否正确、SSH 隧道是否开启")
    except requests.exceptions.Timeout:
        raise LLMError(f"请求超时（{PROTOCOL_TIMEOUT}s）: {url}")
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def chat_openai(base_url: str, api_key: str, model: str,
                messages: list) -> dict:
    """OpenAI 协议。返回统一结构 {text, tool_calls:[{id,name,args}]}"""
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    data = _post(url, {"Authorization": f"Bearer {api_key}",
                       "Content-Type": "application/json"},
                 {"model": model, "messages": messages, "tools": TOOL_SCHEMAS})
    msg = data["choices"][0]["message"]
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function", {})
        raw = fn.get("arguments", "{}")
        try:
            args = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            args = {"command": str(raw)}
        calls.append({"id": c.get("id", f"call_{len(calls)}"),
                      "name": fn.get("name", ""), "args": args})
    return {"text": msg.get("content"), "tool_calls": calls}


def chat_anthropic(base_url: str, api_key: str, model: str,
                   messages: list) -> dict:
    """Anthropic 协议。messages 为内部统一格式，在此做转换。"""
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if not url.endswith("/v1/messages"):
        url += "/v1/messages"

    system = ""
    conv = []
    pending_calls = {}  # tool_call_id -> (name, args) 供 tool 消息配对
    for m in messages:
        role, content = m["role"], m.get("content")
        if role == "system":
            system = content if isinstance(content, str) else str(content)
        elif role == "user":
            conv.append({"role": "user", "content": content})
        elif role == "assistant":
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content})
            for c in m.get("_tool_calls", []):
                blocks.append({"type": "tool_use", "id": c["id"],
                               "name": c["name"],
                               "input": c["args"]})
                pending_calls[c["id"]] = c
            conv.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            tr_id = m.get("tool_call_id", "")
            conv.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": tr_id,
                "content": str(content)}]})

    tools = [{"name": t["function"]["name"],
              "description": t["function"]["description"],
              "input_schema": t["function"]["parameters"]}
             for t in TOOL_SCHEMAS]

    data = _post(url, {"x-api-key": api_key,
                       "anthropic-version": "2023-06-01",
                       "Content-Type": "application/json"},
                 {"model": model, "max_tokens": 8192,
                  "system": system, "messages": conv, "tools": tools})

    text, calls = "", []
    for b in data.get("content", []):
        if b.get("type") == "text":
            text += b.get("text", "")
        elif b.get("type") == "tool_use":
            calls.append({"id": b.get("id", ""), "name": b.get("name", ""),
                          "args": b.get("input", {})})
    return {"text": text or None, "tool_calls": calls}


def chat(provider: dict, model: str, messages: list) -> dict:
    api = provider.get("api", "openai")
    if api == "anthropic":
        return chat_anthropic(provider["baseUrl"], provider.get("apiKey", ""),
                              model, messages)
    return chat_openai(provider["baseUrl"], provider.get("apiKey", ""),
                       model, messages)


# ================================================================ Agent 循环
def run_task(provider: dict, model: str, task: str, history: list,
             max_rounds: int = 0) -> list:
    """执行一次用户任务（可多轮工具循环），返回更新后的 history。
    max_rounds: 循环轮数上限，0 = 不限制。"""
    if not history:
        history.append({"role": "system", "content": build_system_prompt()})
    history.append({"role": "user", "content": task})

    rounds = 0
    while True:
        if max_rounds and rounds >= max_rounds:
            print(f"\n[MyPi] 已达最大循环轮数（{max_rounds}），强制结束本轮任务。"
                  f"可在配置中调大 maxRounds 或设为 0（不限制）。")
            return history
        rounds += 1
        try:
            resp = chat(provider, model, history)
        except LLMError as e:
            print(f"\n[MyPi] 请求失败：{e}")
            return history

        text, calls = resp["text"], resp["tool_calls"]

        if calls:
            for c in calls:
                arg_preview = json.dumps(c["args"], ensure_ascii=False)
                if len(arg_preview) > 80:
                    arg_preview = arg_preview[:80] + "…"
                print(f"  🔧 {c['name']}({arg_preview})")
            # 记录 assistant 消息（含 tool_calls 元信息，anthropic 转换时用）
            a_msg = {"role": "assistant", "content": text,
                     "_tool_calls": calls}
            history.append(a_msg)
            if text:
                print(f"\n{text}\n")
            # 执行并回填结果
            for c in calls:
                result = execute_tool(c["name"], c["args"])
                print(f"  └─ {result[:100].replace(chr(10), ' ⏎ ')}"
                      + ("…" if len(result) > 100 else ""))
                history.append({"role": "tool", "tool_call_id": c["id"],
                                "content": result})
            continue  # 继续循环，让模型基于工具结果继续

        # 无工具调用 → 最终回答
        print(f"\n{text}\n")
        return history

    # max_rounds 为 0 时永远不会到达这里（while True 由 return 退出）


# ================================================================ CLI / REPL
BANNER = """
══════════════════════════════════════════
  MyPi · 最简命令行编码智能体
  工具: bash · 文件读写 · web_fetch · web_search · memory
  会话自动保存 ~/.mypi/sessions/ · 记忆 ~/.mypi/memory.md
  命令: /help 查看 | /exit 退出
══════════════════════════════════════════"""


def resolve(cfg, args) -> tuple:
    pname = args.provider or cfg.get("defaultProvider")
    providers = cfg.get("providers", {})
    if pname not in providers:
        sys.exit(f"[MyPi] 供应商 '{pname}' 不存在。可用: {', '.join(providers)}")
    p = providers[pname]
    model = args.model or p.get("model")
    if not model:
        sys.exit(f"[MyPi] 供应商 '{pname}' 未配置 model，请用 --model 指定")
    return pname, p, model


def repl_loop(cfg, pname, provider, model, history, max_rounds):
    """交互对话主循环（自动存档）"""
    print(BANNER)
    while True:
        try:
            line = ask_prompt().strip()
        except (EOFError, KeyboardInterrupt):
            path = save_session(history, pname, model)
            if path:
                print(f"\n💾 会话已保存: {os.path.basename(path)}")
            print("再见！")
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            path = save_session(history, pname, model)
            if path:
                print(f"💾 会话已保存: {os.path.basename(path)}")
            print("再见！")
            break
        if line == "/reset":
            history.clear()
            print("[MyPi] 对话已清空。")
            continue
        if line == "/help":
            print("""命令:
  /reset            清空对话历史
  /save             手动保存会话
  /sessions         列出历史会话
  /load <序号>       恢复指定会话（1=最近）
  /provider [名称]   查看或切换供应商
  /model [名称]      查看或切换模型
  /tools            列出可用工具
  /exit             退出（自动保存）
其他输入都会作为任务发给模型（可多轮工具调用）。""")
            continue
        if line == "/save":
            path = save_session(history, pname, model)
            print(f"💾 {path}" if path else "[MyPi] 没有可保存的内容")
            continue
        if line == "/sessions":
            sessions = list_sessions()
            if not sessions:
                print("(没有历史会话)")
            for i, s in enumerate(reversed(sessions), 1):
                print(f"  {i}. {os.path.basename(s)}")
            continue
        if line.startswith("/load"):
            parts = line.split(maxsplit=1)
            sessions = list_sessions()
            if len(parts) < 2 or not parts[1].isdigit():
                print("用法: /load <序号>（/sessions 查看，1=最近）")
                continue
            idx = len(sessions) - int(parts[1])
            if not (0 <= idx < len(sessions)):
                print("序号超出范围")
                continue
            history, sp, sm = load_session(sessions[idx])
            if sp and sp in cfg["providers"]:
                pname, provider = sp, cfg["providers"][sp]
            if sm:
                model = sm
            print(f"[MyPi] 已恢复会话（{len(history)} 条消息）："
                  f"{os.path.basename(sessions[idx])}")
            continue
        if line == "/tools":
            for t in TOOL_SCHEMAS:
                fn = t["function"]
                print(f"  {fn['name']:12s} {fn['description']}")
            continue
        if line.startswith("/provider"):
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1] in cfg["providers"]:
                pname, provider = parts[1], cfg["providers"][parts[1]]
                model = provider.get("model", model)
                print(f"[MyPi] 已切换到 {pname} / {model}")
            else:
                print(f"可用供应商: {', '.join(cfg['providers'])}"
                      f"（当前 {pname}）")
            continue
        if line.startswith("/model"):
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                model = parts[1]
                print(f"[MyPi] 模型已切换为 {model}")
            else:
                print(f"当前模型: {model}")
            continue

        history = run_task(provider, model, line, history,
                           max_rounds=max_rounds)
        path = save_session(history, pname, model)
        if path:
            print(f"  💾 {os.path.basename(path)}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="mypi",
                                 description="MyPi · 最简命令行编码智能体")
    ap.add_argument("task", nargs="*",
                    help="任务（交互终端下完成后自动进入连续对话）")
    ap.add_argument("--provider", help="供应商名（config 里的 key）")
    ap.add_argument("--model", help="模型 ID（覆盖配置）")
    ap.add_argument("-c", "--continue", dest="cont", action="store_true",
                    help="继续最近一次会话")
    ap.add_argument("--no-chat", dest="no_chat", action="store_true",
                    help="任务完成后直接退出，不进入连续对话")
    args = ap.parse_args()

    cfg = load_config()
    pname, provider, model = resolve(cfg, args)
    max_rounds = int(cfg.get("maxRounds", 0) or 0)

    if HAS_RICH:
        console.print(f"[dim][MyPi] 供应商={pname}  模型={model}  "
                      f"协议={provider.get('api')}  端点={provider['baseUrl']}[/]")
    else:
        print(f"[MyPi] 供应商={pname}  模型={model}  协议={provider.get('api')}"
              f"  端点={provider['baseUrl']}")

    history = []
    if args.cont:
        sessions = list_sessions()
        if sessions:
            history, sp, sm = load_session(sessions[-1])
            if sp and sp in cfg["providers"]:
                pname, provider = sp, cfg["providers"][sp]
            if sm:
                model = sm
            msg = f"[MyPi] 已恢复最近会话（{len(history)} 条消息）"
            console.print(f"[dim]{msg}[/]") if HAS_RICH else print(msg)
        else:
            print("[MyPi] 没有历史会话，从头开始。")

    if args.task:
        history = run_task(provider, model, " ".join(args.task), history,
                           max_rounds=max_rounds)
        path = save_session(history, pname, model)
        if path:
            print(f"  💾 {os.path.basename(path)}")
        if args.no_chat or not sys.stdin.isatty():
            return  # 脚本/管道场景：直接退出
        print("（已进入连续对话，/exit 退出）")

    repl_loop(cfg, pname, provider, model, history, max_rounds)


if __name__ == "__main__":
    main()
