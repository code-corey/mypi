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
