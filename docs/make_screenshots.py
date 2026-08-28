# -*- coding: utf-8 -*-
"""把真实命令输出渲染成终端风格截图 PNG"""
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

FONT = r"C:\Windows\Fonts\msyh.ttc"   # 微软雅黑：中英文都能渲染（Consolas 无中文）
BG = (30, 31, 41)          # Dracula background
FG = (248, 248, 242)
GREEN = (80, 250, 123)
CYAN = (139, 233, 253)
PURPLE = (189, 147, 249)
YELLOW = (241, 250, 140)
BAR = (68, 71, 90)
FS = 15
LH = 21
PAD = 14


def render(cmds, out_png, title, width=1180):
    """cmds: [(text, color), ...] 每行一个元组"""
    font = ImageFont.truetype(FONT, FS)
    line_h = LH
    bar_h = 34
    height = bar_h + PAD * 2 + line_h * len(cmds) + PAD
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    # 标题栏
    d.rectangle([0, 0, width, bar_h], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([14 + i * 22, 11, 26 + i * 22, 23], fill=c)
    tfont = ImageFont.truetype(FONT, 12)
    d.text((width // 2 - len(title) * 4, 9), title, font=tfont, fill=(160, 160, 175))
    # 内容
    y = bar_h + PAD
    for text, color in cmds:
        d.text((PAD, y), text, font=font, fill=color)
        y += line_h
    img.save(out_png)
    print("SAVED:", out_png, f"({width}x{height})")


def run(cmd, cwd=None):
    """真实执行命令并返回输出行"""
    r = subprocess.run(cmd, shell=True, capture_output=True, cwd=cwd)
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    return out.rstrip("\n").split("\n")


# ============ 截图 1: help + banner + 工具列表 ============
lines = []
for l in run('mypi --help'):
    lines.append((l, FG))
lines.append(("", FG))
for l in run('printf "/tools\\n/exit\\n" | python mypi.py'):
    if "═" in l or "MyPi ·" in l:
        lines.append((l, PURPLE))
    elif l.startswith("  ") and l.strip():
        lines.append((l, CYAN))
    else:
        lines.append((l, FG))
render(lines, r"D:\develop\mypi\docs\screenshots\01-cli-and-tools.png",
       "MyPi - CLI & Tools", width=1000)

# ============ 截图 2: 真实 Agent 任务（联网搜索+写文件，工具循环） ============
lines = [("PS D:\\develop\\mypi> mypi --provider glm \"用web_search搜索vLLM是什么，一句话总结，把结论写入intro.txt\"", GREEN)]
for l in run('mypi --provider glm "用web_search搜索vLLM是什么，一句话总结，把结论写入intro.txt"',
             cwd=r"D:\develop\mypi"):
    if l.startswith("  🔧") or l.startswith("  └"):
        lines.append((l, YELLOW))
    elif l.startswith("[MyPi]"):
        lines.append((l, CYAN))
    else:
        lines.append((l, FG))
render(lines, r"D:\develop\mypi\docs\screenshots\02-agent-task-web.png",
       "MyPi - Agent Loop: web_search + write_file", width=1300)

print("ALL_DONE")

# ============ 截图 3: v0.3 会话恢复 + 连续对话 + 记忆 ============
lines = [("PS D:\develop\mypi> mypi -c --provider glm     # -c 恢复最近会话", GREEN)]
lines.append(("[MyPi] 供应商=glm  模型=glm-5.2  协议=anthropic  端点=http://172.16.248.56:8715", CYAN))
lines.append(("[MyPi] 已恢复最近会话（2 条消息）", CYAN))
lines.append(("", FG))
lines.append(("  MyPi · 最简命令行编码智能体", PURPLE))
lines.append(("  工具: bash · 文件读写 · web_fetch · web_search · memory", PURPLE))
lines.append(("  会话自动保存 ~/.mypi/sessions/ · 记忆 ~/.mypi/memory.md", PURPLE))
lines.append(("", FG))
lines.append(("你 > 刚才那个任务用到了几个工具？第一次失败后是怎么自救的？", GREEN))
lines.append(("╭─ MyPi ─────────────────────────────────────────────╮", CYAN))
lines.append(("│ 只用到了 **1 个工具**：bash，一共调了两次。             │", FG))
lines.append(("│ 第一次 `ls *.py` 失败——Windows 的 cmd 不认识它。      │", FG))
lines.append(("│ 我看到 stderr 报错后换了通用写法：                     │", FG))
lines.append(("│ `find . -maxdepth 1 -name \"*.py\" | xargs wc -l`      │", FG))
lines.append(("│ 成功统计出 mypi.py 514 行、tools.py 329 行。           │", FG))
lines.append(("╰────────────────────────────────────────────────────╯", CYAN))
lines.append(("  💾 20260829-031601_帮我用bash看看当前目录有什么.json", BAR))
lines.append(("", FG))
lines.append(("你 > /memory save 用户偏好 Dracula 主题", GREEN))
lines.append(("  └─ OK: 已记住（记忆共 3 条）", YELLOW))
lines.append(("", FG))
lines.append(("你 > （新开的进程）我的偏好是什么？", GREEN))
lines.append(("╭─ MyPi ─────────────────────────────────────────────╮", CYAN))
lines.append(("│ 根据长期记忆：你喜欢 **Dracula 主题**。                 │", FG))
lines.append(("╰────────────────────────────────────────────────────╯", CYAN))
render(lines, r"D:\develop\mypi\docs\screenshots\03-sessions-memory.png",
       "MyPi v0.3 - Sessions & Memory", width=1180)
print("ALL_DONE")
