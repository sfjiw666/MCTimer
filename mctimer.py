# -*- coding: utf-8 -*-
"""
上班激励计时器 (MCTimer)
========================
桌面小组件式激励计时器：
  - 无边框圆角小窗，可拖拽移动、始终置顶（苹果风格：白卡片、细分隔线、SF 蓝绿配色）
  - 实时显示：距上/下班倒计时、今日进度条
  - 已赚工资：今日（精确到秒实时跳动）、本月、总计
  - 设置与主页在同一窗口内左右滑动切换（iOS 推入式转场）：
    上下班时间（支持跨天夜班）、日薪、每周工作日

数据保存在 %APPDATA%\\MCTimer\\（config.json + history.json）。
总计自首次使用之日起累计；应用未运行的工作日按日薪补记。
"""

import json
import math
import os
import re
import sys
import types
import ctypes
from datetime import date, datetime, time, timedelta

import tkinter as tk
from tkinter import font as tkfont

# ----------------------------------------------------------------------------
# 常量 / 配置
# ----------------------------------------------------------------------------

APP_TITLE = "上班激励计时器"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MCTimer")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")

MAGIC = "#FF01FF"  # 透明mask色（窗口圆角外区域）

# 苹果系配色
C_TEXT = "#1D1D1F"        # 主文字
C_SUB = "#86868B"         # 次要文字
C_HAIR = "#E5E5EA"        # 细分隔线
C_CARD = "#F5F5F7"        # 卡片底色
C_BLUE = "#007AFF"        # 系统蓝
C_BLUE_DK = "#0066D6"     # 蓝色按下/悬停加深
C_GREEN = "#28A745"       # 金额绿（今日实时）
C_ORANGE = "#FF9500"      # 待上班
C_GRAY = "#C7C7CC"        # 休息
C_TRACK = "#E9E9EB"       # 进度条底
C_RED = "#FF3B30"         # 错误

DEFAULT_CFG = {
    "start": "09:00",
    "end": "18:00",
    "salary": 300.0,
    "workdays": [0, 1, 2, 3, 4],   # 周一~周五（0=周一 … 6=周日）
    "first_use": None,             # ISO 日期，总计从这天起算
    "window": None,                # {"x":..,"y":..} 记住窗口位置
    "ball": False,                 # 是否处于小圆球模式
}

WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"]
NOTE_TEXT = "总计自首次使用起累计，未运行的工作日按日薪计入"

# ----------------------------------------------------------------------------
# 数据存取
# ----------------------------------------------------------------------------


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Store:
    """配置 + 历史工资记录（按日冻结）"""

    def __init__(self):
        self.cfg = dict(DEFAULT_CFG)
        self.cfg.update(_load_json(CONFIG_PATH, {}))
        self.history = _load_json(HISTORY_PATH, {})  # {"2026-11-05": 300.0}
        if not self.cfg.get("first_use"):
            self.cfg["first_use"] = date.today().isoformat()

    def save_cfg(self):
        _save_json(CONFIG_PATH, self.cfg)

    def save_history(self):
        _save_json(HISTORY_PATH, self.history)


# ----------------------------------------------------------------------------
# 核心计算（纯函数，便于自测）
# ----------------------------------------------------------------------------


def parse_hhmm(s):
    m = re.match(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$", str(s))
    if not m:
        raise ValueError(f"时间格式应为 HH:MM，收到 {s!r}")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("时间超出范围（时 0-23，分 0-59）")
    return time(hh, mm)


def shift_span(d, t_start, t_end):
    """某天的班次 [start, end)；结束<=开始 视为跨天夜班（如 22:00-06:00）"""
    start = datetime.combine(d, t_start)
    end = datetime.combine(d, t_end)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def is_workday(cfg, d):
    return d.weekday() in set(cfg["workdays"])


def backfill(store, now):
    """把「未运行期间的工作日」按当前日薪补记进 history（不覆盖已有记录）"""
    cfg = store.cfg
    try:
        first = date.fromisoformat(cfg["first_use"])
    except Exception:
        first = now.date()
    if first > now.date():
        first = now.date()
    t_start, t_end = parse_hhmm(cfg["start"]), parse_hhmm(cfg["end"])
    salary = float(cfg["salary"])
    d = first
    changed = False
    while d <= now.date():
        key = d.isoformat()
        if key not in store.history and is_workday(cfg, d):
            _, e = shift_span(d, t_start, t_end)
            if d < now.date() or now >= e:  # 整天已结束才补记
                store.history[key] = round(salary, 2)
                changed = True
        d += timedelta(days=1)
    if changed:
        store.save_history()


def compute(cfg, history, now):
    """根据配置/历史/当前时刻，算出界面所需的全部状态"""
    today = now.date()
    key = today.isoformat()
    wd = is_workday(cfg, today)
    t_start, t_end = parse_hhmm(cfg["start"]), parse_hhmm(cfg["end"])
    s, e = shift_span(today, t_start, t_end)
    total = (e - s).total_seconds()

    if not wd:
        phase = "off"
    elif now < s:
        phase = "pre"
    elif now < e:
        phase = "in"
    else:
        phase = "post"

    frozen = key in history
    salary = float(cfg["salary"])
    rate = salary / total if total > 0 else 0.0  # 每秒进账

    if frozen:
        today_earned = history[key]
    elif not wd:
        today_earned = 0.0
    elif phase == "pre":
        today_earned = 0.0
    elif phase == "in":
        today_earned = salary * (now - s).total_seconds() / total
    else:  # post，尚未冻结（下一次 tick 会冻结）
        today_earned = salary

    if phase == "pre":
        remaining = (s - now).total_seconds()
        elapsed, pct = 0.0, 0.0
    elif phase == "in":
        remaining = (e - now).total_seconds()
        elapsed = (now - s).total_seconds()
        pct = elapsed / total if total > 0 else 0.0
    elif phase == "post":
        remaining, elapsed, pct = 0.0, total, 1.0
    else:  # off
        remaining, elapsed, pct = 0.0, 0.0, 0.0

    prefix = today.strftime("%Y-%m")
    month_sum = sum(v for k, v in history.items() if k[:7] == prefix)
    total_sum = sum(history.values())
    if not frozen:
        month_sum += today_earned
        total_sum += today_earned

    return {
        "phase": phase,          # off / pre / in / post
        "workday": wd,
        "frozen": frozen,
        "start": s, "end": e,
        "total": total, "elapsed": elapsed, "remaining": remaining, "pct": pct,
        "rate": rate,
        "today_earned": today_earned,
        "month_sum": month_sum,
        "total_sum": total_sum,
    }


def fmt_hms(seconds):
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_money(v, dec=2):
    return f"¥{v:,.{dec}f}"


# ----------------------------------------------------------------------------
# 高 DPI 支持（必须在创建 Tk 之前）
# ----------------------------------------------------------------------------


def enable_dpi_awareness():
    # 注意：SetProcessDpiAwareness 失败时返回负 HRESULT 而非抛异常，必须检查返回值
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:  # PER_MONITOR_AWARE
            return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # 兜底：系统级 DPI 感知
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 无边框圆角窗口基础设施
# ----------------------------------------------------------------------------


def rr_pts(x1, y1, x2, y2, r):
    """圆角矩形的平滑多边形顶点（半径自动钳制，r=宽高一半时即圆）"""
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """用平滑多边形近似圆角矩形"""
    return cv.create_polygon(rr_pts(x1, y1, x2, y2, r), smooth=True, **kw)


def draw_gear(cv, cx, cy, items, color, s=1.0, tags=()):
    """绘制极简齿轮图标（外圈 + 8 齿 + 中心孔）"""
    px = lambda v: round(v * s)
    r = px(5)
    items.append(cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                                outline=color, width=px(1.4), tags=tags))
    for k in range(8):
        a = math.pi * 2 * k / 8
        x1, y1 = cx + math.cos(a) * px(6), cy + math.sin(a) * px(6)
        x2, y2 = cx + math.cos(a) * px(8.2), cy + math.sin(a) * px(8.2)
        items.append(cv.create_line(x1, y1, x2, y2, width=px(2.6),
                                    fill=color, capstyle="round", tags=tags))
    rh = px(1.9)
    items.append(cv.create_oval(cx - rh, cy - rh, cx + rh, cy + rh,
                                outline=color, width=px(1.4), tags=tags))


def draw_chevron(cv, cx, cy, items, color, s=1.0, tags=()):
    """绘制返回箭头 ‹ """
    px = lambda v: round(v * s)
    items.append(cv.create_line(cx + px(3), cy - px(5), cx - px(3), cy,
                                width=px(1.8), fill=color,
                                capstyle="round", joinstyle="round", tags=tags))
    items.append(cv.create_line(cx - px(3), cy, cx + px(3), cy + px(5),
                                width=px(1.8), fill=color, capstyle="round", tags=tags))


def draw_minus(cv, cx, cy, items, color, s=1.0, tags=()):
    """绘制最小化图标 ─ """
    px = lambda v: round(v * s)
    items.append(cv.create_line(cx - px(4), cy, cx + px(4), cy,
                                width=px(1.8), fill=color,
                                capstyle="round", tags=tags))


class CBtn:
    """Canvas 上的按钮：circle（图标圆钮）/ pill（胶囊按钮）/ chip（工作日选块）
    每个按钮属于一个页面（page），绘制与命中都会应用该页面的滑动偏移。"""

    def __init__(self, host, kind, box, text, cmd, font=None,
                 fg=C_SUB, bg=None, active_bg="#EDEDF2",
                 sel_bg=C_BLUE, sel_fg="#FFFFFF", selected=False,
                 decor=None, page=None):
        self.host, self.kind, self.box, self.text, self.cmd = host, kind, box, text, cmd
        self.font = font
        self.fg, self.bg, self.active_bg = fg, bg, active_bg
        self.sel_bg, self.sel_fg, self.selected = sel_bg, sel_fg, selected
        self.decor = decor  # 自绘图标回调 decor(cv, cx, cy, items, color)
        self.page = page    # 所属页面标签（None = 常驻）
        self.hover = False
        self.items = []
        self.draw()

    # -- 绘制 -------------------------------------------------------------
    def _clear(self):
        for it in self.items:
            self.host.cv.delete(it)
        self.items = []

    def _ox(self):
        return self.host.page_offsets.get(self.page, 0)

    def draw(self):
        cv = self.host.cv
        self._clear()
        ox = self._ox()
        tag = (f"page_{self.page}",) if self.page else ()  # 随页面一起滑动
        x1, y1, x2, y2 = self.box
        x1, x2 = x1 + ox, x2 + ox
        if self.kind == "circle":
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            if self.hover:
                self.items.append(cv.create_oval(x1, y1, x2, y2, fill=self.active_bg,
                                                 width=0, tags=tag))
            if self.decor:
                self.decor(cv, cx, cy, self.items, self.fg, tags=tag)
            else:
                self.items.append(cv.create_text(cx, cy, text=self.text,
                                                 font=self.font, fill=self.fg, tags=tag))
        elif self.kind == "pill":
            fill = self.active_bg if self.hover and self.active_bg else self.bg
            self.items.append(round_rect(cv, x1, y1, x2, y2, (y2 - y1) / 2,
                                         fill=fill, width=0, tags=tag))
            self.items.append(cv.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                             text=self.text, font=self.font,
                                             fill=self.fg, tags=tag))
        elif self.kind == "chip":
            fill = self.sel_bg if self.selected else ("#DEDEE3" if self.hover else "#E9E9EB")
            fg = self.sel_fg if self.selected else "#3C3C43"
            self.items.append(round_rect(cv, x1, y1, x2, y2, (y2 - y1) / 2,
                                         fill=fill, width=0, tags=tag))
            self.items.append(cv.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                             text=self.text, font=self.font,
                                             fill=fg, tags=tag))

    def set_hover(self, on):
        if on != self.hover:
            self.hover = on
            self.draw()

    def set_selected(self, on):
        if on != self.selected:
            self.selected = on
            self.draw()

    def inside(self, x, y):
        if self._ox() != 0:
            return False  # 所属页面未显示（已滑出）
        x1, y1, x2, y2 = self.box
        return x1 <= x <= x2 and y1 <= y <= y2


class FramelessMixin:
    """无边框 + 圆角 + 透明角 + 整窗拖拽 + Canvas 按钮分发"""

    RADIUS = 18

    def setup_frameless(self, w, h, x=None, y=None):
        self.win_w, self.win_h = w, h
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.config(bg=MAGIC)
        self.attributes("-transparentcolor", MAGIC)
        if x is None or y is None:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x, y = sw - w - 60, 80
        # 钳制到屏幕范围内（换显示器/分辨率变化后避免窗口丢失）
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(int(x), sw - w))
        y = max(0, min(int(y), sh - h))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.cv = tk.Canvas(self, bg=MAGIC, highlightthickness=0, bd=0)
        self.cv.pack(fill=tk.BOTH, expand=True)
        # 窗口本体：白色圆角卡片 + 发丝描边
        radius = round(getattr(self, "s", 1.0) * self.RADIUS)
        self.win_shape = round_rect(self.cv, 1, 1, w - 1, h - 1, radius,
                                    fill="#FFFFFF", outline="#E4E4E9", width=1)
        self.cv.tag_lower(self.win_shape)

        self.page_offsets = {}   # 页面标签 -> 当前 x 偏移（滑动切换用）
        self.buttons = []
        self._press_btn = None
        self._grab_dx = self._grab_dy = None
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Motion>", self._on_hover)

    # -- 按钮注册 ---------------------------------------------------------
    def add_button(self, *a, **kw):
        b = CBtn(self, *a, **kw)
        self.buttons.append(b)
        return b

    # -- 事件 -------------------------------------------------------------
    def _on_press(self, ev):
        self._moved = False
        self._press_btn = next((b for b in self.buttons if b.inside(ev.x, ev.y)), None)
        # 记录「指针 - 窗口左上角」抓取偏移，拖动时按屏幕绝对坐标定位窗口
        self._grab_dx = self.winfo_pointerx() - self.winfo_x()
        self._grab_dy = self.winfo_pointery() - self.winfo_y()
        self._press_sx, self._press_sy = self.winfo_pointerx(), self.winfo_pointery()

    def _on_drag(self, ev):
        if getattr(self, "_grab_dx", None) is None:
            return
        px, py = self.winfo_pointerx(), self.winfo_pointery()
        if abs(px - self._press_sx) + abs(py - self._press_sy) > 4:
            self._moved = True
            self._press_btn = None  # 移动了就当作拖窗，不触发按钮
            self.geometry(f"+{px - self._grab_dx}+{py - self._grab_dy}")

    def _on_release(self, ev):
        if self._press_btn is not None and not self._moved and self._press_btn.inside(ev.x, ev.y):
            btn, self._press_btn = self._press_btn, None
            btn.cmd()
        self._press_btn = None
        self._grab_dx = self._grab_dy = None

    def _on_hover(self, ev):
        hand = False
        for b in self.buttons:
            on = b.inside(ev.x, ev.y)
            b.set_hover(on)
            hand = hand or on
        self.cv.config(cursor="hand2" if hand else "arrow")


# ----------------------------------------------------------------------------
# 主窗口（主页面 / 设置页面 滑动切换）
# ----------------------------------------------------------------------------

MAIN_TAG = ("page_main",)
SET_TAG = ("page_settings",)
BALL_TAG = ("page_ball",)


class MainWindow(FramelessMixin, tk.Tk):

    W, H = 340, 352
    BALL = 64                 # 小圆球模式窗口尺寸（设计 px）
    SLIDE_FRAMES = 12
    BALL_FRAMES = 8

    def __init__(self, store):
        tk.Tk.__init__(self)
        self.store = store
        self.title(APP_TITLE)
        self.s = max(1.0, self.winfo_fpixels("1i") / 96.0)  # 缩放系数
        self._fonts()
        pos = store.cfg.get("window") or {}
        self.setup_frameless(self.px(self.W), self.px(self.H),
                             pos.get("x"), pos.get("y"))

        # 页面滑动状态：t=0 主页，t=1 设置页
        # 注意：先以偏移 0 构建两个页面（按钮等所有元素画在基准位置），
        # 再整体移动设置页出画面 —— 避免按钮绘制偏移与 move 叠加成双重偏移
        self._page = "main"
        self._after_id = None
        self._main_off = 0
        self._set_off = 0
        self.page_offsets = {"main": 0, "settings": 0}

        self._build_main()
        self._build_settings()
        self._set_off = self.px(self.W)
        self.page_offsets["settings"] = self._set_off
        self.cv.move(SET_TAG, self._set_off, 0)  # 设置页初始移出画面

        # 小圆球模式状态
        self._ball_btn = None
        self._ball_ids = {}
        self._ball_dot = C_GREEN
        self._ball_text = "--:--"

        self.after(200, self.tick)
        self.bind("<Escape>", self._on_esc)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if store.cfg.get("ball"):  # 上次退出时是圆球模式 → 直接以圆球恢复
            self._enter_ball_mode_instant()
        if os.environ.get("MCTIMER_SMOKE"):
            self.after(700, self.show_settings)
            self.after(1500, self.show_main)
            self.after(2300, self.collapse)
            self.after(3300, self.expand)
            self.after(4200, self.on_close)
        elif os.environ.get("MCTIMER_SHOW_SETTINGS"):  # 开发辅助：启动即打开设置页
            self.after(800, self.show_settings)
        elif os.environ.get("MCTIMER_SHOW_BALL"):      # 开发辅助：启动即折叠成圆球
            self.after(800, self.collapse)

    # -- 工具 -------------------------------------------------------------
    def px(self, v):
        return round(v * self.s)

    def _fonts(self):
        f = lambda fam, size, w=None: tkfont.Font(
            family=fam, size=-self.px(size), weight=w or "normal")
        self.f_title = f("Segoe UI Semibold", 14)
        self.f_cap = f("Segoe UI", 12)
        self.f_clock = f("Segoe UI Light", 54)
        self.f_big = f("Segoe UI Semibold", 17)
        self.f_small = f("Segoe UI", 11)
        self.f_tiny = f("Segoe UI", 10)
        self.f_label = f("Segoe UI", 12)
        self.f_entry = f("Segoe UI", 13)
        self.f_chip = f("Segoe UI", 11)
        self.f_btn = f("Segoe UI Semibold", 14)
        self.f_ball = f("Segoe UI Semibold", 15)

    # ============================================================
    # 主页面
    # ============================================================
    def _build_main(self):
        cv, px = self.cv, self.px
        W = px(self.W)

        # 顶栏
        cv.create_text(px(20), px(27), text="上班激励", anchor="w",
                       font=self.f_title, fill=C_TEXT, tags=MAIN_TAG)
        r = px(13)
        minus = lambda cv_, cx, cy, items, color, tags=(): draw_minus(cv_, cx, cy, items, color, self.s, tags)
        gear = lambda cv_, cx, cy, items, color, tags=(): draw_gear(cv_, cx, cy, items, color, self.s, tags)
        self.add_button("circle",
                        (px(248) - r, px(27) - r, px(248) + r, px(27) + r),
                        "", cmd=self.collapse, fg=C_SUB, decor=minus, page="main")
        self.add_button("circle",
                        (px(282) - r, px(27) - r, px(282) + r, px(27) + r),
                        "", cmd=self.show_settings, fg=C_SUB, decor=gear, page="main")
        self.add_button("circle",
                        (px(316) - r, px(27) - r, px(316) + r, px(27) + r),
                        "✕", cmd=self.on_close, font=self.f_tiny, fg=C_SUB, page="main")
        cv.create_line(px(0), px(48), W, px(48), fill=C_HAIR, tags=MAIN_TAG)

        # 状态圆点 + 说明
        self.dot = cv.create_oval(px(120), px(67), px(126), px(73),
                                  fill=C_GREEN, width=0, tags=MAIN_TAG)
        self.cap = cv.create_text(px(134), px(70), text="距下班还有",
                                  anchor="w", font=self.f_cap, fill=C_SUB, tags=MAIN_TAG)

        # 大倒计时（每位数字固定槽位，避免跳动）
        self.clock_ids = []
        self._layout_clock()

        # 进度条 + 百分比
        self.bar_track = round_rect(cv, px(36), px(158), px(270), px(164), px(3),
                                    fill=C_TRACK, width=0, tags=MAIN_TAG)
        self.bar_fill = round_rect(cv, px(36), px(158), px(42), px(164), px(3),
                                   fill=C_BLUE, width=0, tags=MAIN_TAG)
        self.pct = cv.create_text(px(304), px(161), text="0%", anchor="e",
                                  font=self.f_small, fill=C_SUB, tags=MAIN_TAG)

        # 金额卡片
        round_rect(cv, px(16), px(184), W - px(16), px(316), px(14),
                   fill=C_CARD, width=0, tags=MAIN_TAG)
        self.money = {}
        rows = [("today", "今日", px(208), C_GREEN),
                ("month", "本月", px(250), C_TEXT),
                ("total", "总计", px(292), C_TEXT)]
        for k, label, cy, color in rows:
            cv.create_text(px(34), cy, text=label, anchor="w",
                           font=self.f_cap, fill=C_SUB, tags=MAIN_TAG)
            self.money[k] = cv.create_text(W - px(34), cy, text="¥0.00", anchor="e",
                                           font=self.f_big, fill=color, tags=MAIN_TAG)
        cv.create_line(px(34), px(230), W - px(34), px(230), fill=C_HAIR, tags=MAIN_TAG)
        cv.create_line(px(34), px(272), W - px(34), px(272), fill=C_HAIR, tags=MAIN_TAG)

        # 底部提示
        self.footer = cv.create_text(W // 2, px(334), text="",
                                     font=self.f_tiny, fill=C_SUB, tags=MAIN_TAG)

    def _layout_clock(self):
        """按 “0” 的字宽为每个字符留固定槽位，数字更新时不抖动；冒号垂直居中"""
        cv, px = self.cv, self.px
        dw = self.f_clock.measure("0")
        cw = self.f_clock.measure(":")
        total_w = 6 * dw + 2 * cw
        x = (px(self.W) - total_w) // 2
        y_top = px(88)
        y_mid = y_top + self.f_clock.metrics("linespace") * 0.52
        for ch in "00:00:00":
            w = dw if ch != ":" else cw
            cx = x + w / 2
            if ch == ":":
                self.clock_ids.append(cv.create_text(cx, y_mid, text=":", anchor="c",
                                                     font=self.f_clock, fill=C_SUB,
                                                     tags=MAIN_TAG))
            else:
                self.clock_ids.append(cv.create_text(cx, y_top, text=ch, anchor="n",
                                                     font=self.f_clock, fill=C_TEXT,
                                                     tags=MAIN_TAG))
            x += w

    def _set_clock(self, text):
        for i, it in enumerate(self.clock_ids):
            self.cv.itemconfigure(it, text=text[i] if i < len(text) else "")

    # ============================================================
    # 设置页面
    # ============================================================
    def _build_settings(self):
        cv, px = self.cv, self.px
        W = px(self.W)
        cfg = self.store.cfg

        # 顶栏：返回 + 标题 + ✕
        chev = lambda cv_, cx, cy, items, color, tags=(): draw_chevron(cv_, cx, cy, items, color, self.s, tags)
        r = px(13)
        self.add_button("circle", (px(20), px(14), px(46), px(40)), "",
                        cmd=self.show_main, fg=C_TEXT, decor=chev, page="settings")
        cv.create_text(px(56), px(27), text="设置", anchor="w",
                       font=self.f_title, fill=C_TEXT, tags=SET_TAG)
        self.add_button("circle",
                        (px(316) - r, px(27) - r, px(316) + r, px(27) + r),
                        "✕", cmd=self.on_close, font=self.f_tiny, fg=C_SUB, page="settings")
        cv.create_line(px(0), px(48), W, px(48), fill=C_HAIR, tags=SET_TAG)

        # 输入行（iOS 分组卡片，白底输入框；三行同宽，左右边缘对齐）
        round_rect(cv, px(16), px(62), W - px(16), px(180), px(14),
                   fill=C_CARD, width=0, tags=SET_TAG)
        self.var_start = tk.StringVar(value=str(cfg["start"]))
        self.var_end = tk.StringVar(value=str(cfg["end"]))
        self.var_salary = tk.StringVar(value=f"{float(cfg['salary']):g}")
        field_w = px(96)  # 统一宽度，保证三行白框左右对齐
        self.ent_start = self._setting_row("上班时间", px(87), self.var_start, field_w)
        cv.create_line(px(30), px(104), W - px(30), px(104), fill=C_HAIR, tags=SET_TAG)
        self.ent_end = self._setting_row("下班时间", px(122), self.var_end, field_w)
        cv.create_line(px(30), px(139), W - px(30), px(139), fill=C_HAIR, tags=SET_TAG)
        self.ent_salary = self._setting_row("日薪（元）", px(157), self.var_salary, field_w)

        # Tab 在三个输入框间循环 / 回车保存
        ents = [self.ent_start, self.ent_end, self.ent_salary]
        for i, e in enumerate(ents):
            nxt = ents[(i + 1) % 3]
            e.bind("<Tab>", lambda ev, nxt=nxt: (nxt.focus_set(), "break")[1])
            e.bind("<Return>", lambda ev: self._save_settings())

        cv.create_text(W // 2, px(196), text="结束早于开始 = 跨天夜班",
                       font=self.f_tiny, fill=C_SUB, tags=SET_TAG)

        # 每周工作日
        cv.create_text(px(24), px(214), text="每周工作日", anchor="w",
                       font=self.f_label, fill=C_TEXT, tags=SET_TAG)
        self.chips = {}
        x = px(24)
        wd = set(cfg["workdays"])
        for i, lab in enumerate(WEEK_LABELS):
            box = (x, px(226), x + px(32), px(250))
            self.chips[i] = self.add_button(
                "chip", box, lab, cmd=lambda i=i: self._toggle_chip(i),
                font=self.f_chip, selected=i in wd, page="settings")
            x += px(38)

        # 说明 / 错误提示 + 保存按钮
        self.set_note = cv.create_text(W // 2, px(268), text=NOTE_TEXT,
                                       font=self.f_tiny, fill=C_SUB, tags=SET_TAG)
        self.add_button("pill", (px(24), px(280), W - px(24), px(324)), "保存",
                        cmd=self._save_settings, font=self.f_btn, fg="#FFFFFF",
                        bg=C_BLUE, active_bg=C_BLUE_DK, page="settings")

    def _setting_row(self, label, cy, var, w):
        """设置页的一行：左侧标签 + 右侧白色圆角输入框"""
        cv, px = self.cv, self.px
        cv.create_text(px(30), cy, text=label, anchor="w",
                       font=self.f_label, fill=C_TEXT, tags=SET_TAG)
        ent = tk.Entry(self, textvariable=var, font=self.f_entry, justify="center",
                       bd=0, relief="flat", bg="#FFFFFF", fg=C_TEXT,
                       insertbackground=C_TEXT, highlightthickness=0, takefocus=0)
        x1 = px(306) - w
        round_rect(cv, x1, cy - px(16), px(306), cy + px(16), px(8),
                   fill="#FFFFFF", width=0, tags=SET_TAG)
        # anchor="c"：Entry 居中嵌在圆角底上（默认 nw 会向右下偏出）
        cv.create_window(x1 + w // 2, cy, window=ent, width=w - px(10),
                         height=px(24), anchor="c", tags=SET_TAG)
        return ent

    def _toggle_chip(self, i):
        self.chips[i].set_selected(not self.chips[i].selected)
        self.cv.itemconfigure(self.set_note, text=NOTE_TEXT, fill=C_SUB)

    def _sync_settings(self):
        """每次滑入设置页时，用当前配置刷新控件"""
        cfg = self.store.cfg
        self.var_start.set(str(cfg["start"]))
        self.var_end.set(str(cfg["end"]))
        self.var_salary.set(f"{float(cfg['salary']):g}")
        wd = set(cfg["workdays"])
        for i, b in self.chips.items():
            b.set_selected(i in wd)
        self.cv.itemconfigure(self.set_note, text=NOTE_TEXT, fill=C_SUB)

    def _save_settings(self):
        try:
            start = parse_hhmm(self.var_start.get())
            end = parse_hhmm(self.var_end.get())
            if end == start:
                raise ValueError("上下班时间不能相同")
            salary = float(self.var_salary.get().replace(",", "")
                           .replace("￥", "").replace("¥", ""))
            if not (0 <= salary <= 10_000_000):
                raise ValueError("日薪需在 0 ~ 10,000,000 之间")
            workdays = sorted(i for i, b in self.chips.items() if b.selected)
            if not workdays:
                raise ValueError("请至少选择一个工作日")
        except ValueError as ex:
            self.cv.itemconfigure(self.set_note, text=str(ex), fill=C_RED)
            return
        except Exception:
            self.cv.itemconfigure(self.set_note, text="日薪请填写数字", fill=C_RED)
            return

        cfg = self.store.cfg
        cfg["start"] = f"{start.hour:02d}:{start.minute:02d}"
        cfg["end"] = f"{end.hour:02d}:{end.minute:02d}"
        cfg["salary"] = salary
        cfg["workdays"] = workdays
        self.store.save_cfg()
        backfill(self.store, datetime.now())
        self.show_main()

    # ============================================================
    # 页面滑动切换
    # ============================================================
    def show_settings(self):
        self._slide_to("settings")

    def show_main(self):
        self._slide_to("main")

    def _slide_to(self, target):
        """主页面与设置页左右滑动切换（iOS 推入式，ease-out）"""
        if self._page in ("anim", "ball") or self._page == target:
            return
        self._page = "anim"
        if target == "settings":
            self._sync_settings()

        W = self.px(self.W)
        t0 = -self._main_off / W
        t1 = 1.0 if target == "settings" else 0.0
        frames = self.SLIDE_FRAMES

        def step(i):
            u = min(1.0, i / frames)
            e = 1 - (1 - u) ** 3  # ease-out cubic
            t = t0 + (t1 - t0) * e
            m, s_ = -W * t, W * (1 - t)
            self.cv.move(MAIN_TAG, m - self._main_off, 0)
            self.cv.move(SET_TAG, s_ - self._set_off, 0)
            self._main_off, self._set_off = m, s_
            self.page_offsets["main"], self.page_offsets["settings"] = m, s_
            if i <= frames:
                self.after(16, lambda: step(i + 1))
            else:
                self._page = target
                if target == "main":
                    self._tick()
                else:
                    self.after(60, self.ent_start.focus_set)

        step(1)

    def _on_esc(self, _ev=None):
        if self._page == "settings":
            self.show_main()

    # ============================================================
    # 小圆球模式
    # ============================================================
    # -- 卡片形状管理（普通=圆角多边形；圆球模式=正圆 oval） --------------
    def _set_card_oval(self, w, h):
        """切换为椭圆/正圆卡片（小圆球模式）。Tk smooth 多边形在 r=半径时
        顶点退化画不出正圆，必须用 create_oval。"""
        try:
            self.cv.delete(self.win_shape)
        except Exception:
            pass
        self.win_shape = self.cv.create_oval(1, 1, w - 1, h - 1,
                                             fill="#FFFFFF", outline="#E4E4E9", width=1)
        self.cv.tag_lower(self.win_shape)

    def _set_card_rect(self, w, h):
        """恢复圆角矩形卡片（主/设置页面形态）"""
        try:
            self.cv.delete(self.win_shape)
        except Exception:
            pass
        self.win_shape = round_rect(self.cv, 1, 1, w - 1, h - 1,
                                    round(self.s * self.RADIUS),
                                    fill="#FFFFFF", outline="#E4E4E9", width=1)
        self.cv.tag_lower(self.win_shape)

    def _ball_face_for(self, st):
        """圆球显示内容：状态点颜色 + 紧凑文案（倒计时 HH:MM）"""
        dot = {"pre": C_ORANGE, "in": C_GREEN, "post": "#8E8E93", "off": C_GRAY}[st["phase"]]
        if st["phase"] in ("pre", "in"):
            text = fmt_hms(st["remaining"])[:5]
        else:
            text = "下班" if st["phase"] == "post" else "休息"
        return dot, text

    def _draw_ball_face(self, cv_, cx, cy, items, tags):
        """圆球内容：状态点 + 倒计时文字（由 CBtn 在绘制/悬停时调用）"""
        r = self.px(4)
        items.append(cv_.create_oval(cx - r, cy - self.px(17), cx + r, cy - self.px(9),
                                     fill=self._ball_dot, width=0, tags=tags))
        items.append(cv_.create_text(cx, cy + self.px(6), text=self._ball_text,
                                     font=self.f_ball, fill=C_TEXT, tags=tags))
        self._ball_ids = {"dot": items[-2], "text": items[-1]}

    def _create_ball_items(self, win_w, win_h):
        """在当前窗口中央创建整面圆球按钮（点击展开）"""
        b = self.px(self.BALL)
        x0, y0 = (win_w - b) // 2, (win_h - b) // 2
        face = lambda cv_, cx, cy, items, color, tags=(): \
            self._draw_ball_face(cv_, cx, cy, items, tags)
        self._ball_btn = self.add_button("circle", (x0, y0, x0 + b, y0 + b), "",
                                         cmd=self.expand, fg=C_TEXT,
                                         decor=face, page="ball")

    def _remove_ball_items(self):
        if self._ball_btn in self.buttons:
            self.buttons.remove(self._ball_btn)
        self._ball_btn = None
        self.cv.delete(BALL_TAG)
        self._ball_ids = {}

    def _hide_pages(self):
        """把主/设置两页都移出画面（折叠与启动恢复共用）"""
        far = self.px(self.W) * 2
        self.cv.move(MAIN_TAG, far * -1 - self._main_off, 0)
        self._main_off = -far
        self.page_offsets["main"] = self._main_off
        if self._set_off != self.px(self.W):
            self.cv.move(SET_TAG, self.px(self.W) - self._set_off, 0)
            self._set_off = self.px(self.W)
        self.page_offsets["settings"] = self._set_off

    def collapse(self):
        """主窗口 → 小圆球（缩放动画，中心点保持不动）"""
        if self._page not in ("main", "settings"):
            return
        w0, h0 = self.winfo_width(), self.winfo_height()
        cx = self.winfo_x() + w0 / 2
        cy = self.winfo_y() + h0 / 2
        self._page = "anim"
        self._hide_pages()
        self._set_card_oval(w0, h0)  # 卡片切换为椭圆，随动画收缩成正圆

        st = compute(self.store.cfg, self.store.history, datetime.now())
        self._ball_dot, self._ball_text = self._ball_face_for(st)
        self._create_ball_items(w0, h0)

        bw = bh = self.px(self.BALL)
        r0, r1 = self.px(self.RADIUS), (bw - 2) // 2
        frames = self.BALL_FRAMES
        moved_x = moved_y = 0.0

        def step(i):
            nonlocal moved_x, moved_y
            u = min(1.0, i / frames)
            e = 1 - (1 - u) ** 2
            w = round(w0 + (bw - w0) * e)
            h = round(h0 + (bh - h0) * e)
            # 球面内容跟随窗口中心：目标画布位置 = 窗口尺寸的一半
            mx, my = (w - w0) / 2, (h - h0) / 2
            self.cv.move(BALL_TAG, mx - moved_x, my - moved_y)
            moved_x, moved_y = mx, my
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = int(max(0, min(cx - w / 2, sw - w)))
            y = int(max(0, min(cy - h / 2, sh - h)))
            self.geometry(f"{w}x{h}+{x}+{y}")
            self.cv.coords(self.win_shape, 1, 1, w - 1, h - 1)  # 椭圆随窗口收缩
            if i < frames:
                self.after(16, lambda: step(i + 1))
            else:
                # 点击区域同步到最终位置（窗口原点，即球面）
                self._ball_btn.box = (0, 0, bw, bh)
                self.store.cfg["ball"] = True
                self.store.save_cfg()
                self._page = "ball"
                self._tick()

        step(0)

    def expand(self):
        """小圆球 → 主窗口（展开动画，球心过渡到窗口中心）"""
        if self._page != "ball":
            return
        self._page = "anim"
        self.store.cfg["ball"] = False
        self.store.save_cfg()
        self._remove_ball_items()

        w0, h0 = self.winfo_width(), self.winfo_height()
        x0, y0 = self.winfo_x(), self.winfo_y()
        cx, cy = x0 + w0 / 2, y0 + h0 / 2
        w1, h1 = self.px(self.W), self.px(self.H)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x1 = int(max(0, min(cx - w1 / 2, sw - w1)))
        y1 = int(max(0, min(cy - h1 / 2, sh - h1)))
        frames = self.BALL_FRAMES

        def step(i):
            u = min(1.0, i / frames)
            e = 1 - (1 - u) ** 2
            w = round(w0 + (w1 - w0) * e)
            h = round(h0 + (h1 - h0) * e)
            x = round(x0 + (x1 - x0) * e)
            y = round(y0 + (y1 - y0) * e)
            self.geometry(f"{w}x{h}+{x}+{y}")
            self.cv.coords(self.win_shape, 1, 1, w - 1, h - 1)
            if i < frames:
                self.after(16, lambda: step(i + 1))
            else:
                self._set_card_rect(w1, h1)  # 恢复圆角矩形卡片
                self.cv.move(MAIN_TAG, -self._main_off, 0)
                self._main_off = 0
                self.page_offsets["main"] = 0
                self.cv.move(SET_TAG, self.px(self.W) - self._set_off, 0)
                self._set_off = self.px(self.W)
                self.page_offsets["settings"] = self._set_off
                self._page = "main"
                self._tick()

        step(0)

    def _enter_ball_mode_instant(self):
        """启动时直接以圆球形态出现（无动画）"""
        bw = bh = self.px(self.BALL)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        pos = self.store.cfg.get("window") or {}
        x = max(0, min(int(pos.get("x", sw - bw - 20)), sw - bw))
        y = max(0, min(int(pos.get("y", 80)), sh - bh))
        self._hide_pages()
        self._page = "ball"
        self.geometry(f"{bw}x{bh}+{x}+{y}")
        self._set_card_oval(bw, bh)
        st = compute(self.store.cfg, self.store.history, datetime.now())
        self._ball_dot, self._ball_text = self._ball_face_for(st)
        self._create_ball_items(bw, bh)

    # ============================================================
    # 每帧刷新
    # ============================================================
    def tick(self):
        try:
            if not self.winfo_exists():
                return
            self._tick()
            self._after_id = self.after(200, self.tick)
        except tk.TclError:
            pass  # 窗口销毁过程中的残留回调

    def _tick(self):
        cv, px = self.cv, self.px
        now = datetime.now()
        st = compute(self.store.cfg, self.store.history, now)

        # 下班后冻结今日工资（无论当前在哪个页面都照常记账）
        key = now.date().isoformat()
        if st["phase"] == "post" and st["workday"] and not st["frozen"]:
            self.store.history[key] = round(float(self.store.cfg["salary"]), 2)
            self.store.save_history()
            st = compute(self.store.cfg, self.store.history, now)

        # 设置页显示时不重绘主页（滑出状态下 coords 会把元素拉回画面）
        if self._page == "ball":
            # 圆球模式：只刷新状态点与倒计时
            self._ball_dot, self._ball_text = self._ball_face_for(st)
            ids = self._ball_ids
            if ids.get("dot"):
                cv.itemconfigure(ids["dot"], fill=self._ball_dot)
            if ids.get("text"):
                cv.itemconfigure(ids["text"], text=self._ball_text)
            return
        if self._page != "main":
            return

        # 状态点 + 说明 + 倒计时
        phase = st["phase"]
        dot = {"pre": C_ORANGE, "in": C_GREEN, "post": "#8E8E93", "off": C_GRAY}[phase]
        cap = {"pre": "距上班还有", "in": "距下班还有",
               "post": "已下班 · 辛苦了", "off": "今天不上班"}[phase]
        cv.itemconfigure(self.dot, fill=dot)
        cv.itemconfigure(self.cap, text=cap)
        self._set_clock(fmt_hms(st["remaining"]))

        # 进度条
        pct = st["pct"]
        x1, x2 = px(36), px(270)
        w = max(px(6), int((x2 - x1) * pct))
        cv.coords(self.bar_fill, x1, px(158), x1 + w, px(164))
        cv.tag_lower(self.bar_track)
        cv.tag_lower(self.win_shape)
        ptxt = {"pre": "未开始", "off": "—"}.get(phase) or f"{pct * 100:.0f}%"
        cv.itemconfigure(self.pct, text=ptxt)

        # 金额
        cv.itemconfigure(self.money["today"], text=fmt_money(st["today_earned"], 4))
        cv.itemconfigure(self.money["month"], text=fmt_money(st["month_sum"], 2))
        cv.itemconfigure(self.money["total"], text=fmt_money(st["total_sum"], 2))

        # 底部提示
        rate = st["rate"]
        footer = {
            "pre": f"上班后开始进账 · 每秒 +¥{rate:.4f}",
            "in": f"进账中 · 每秒 +¥{rate:.4f} · 拖拽移动",
            "post": "今日工资已全额到账 · 拖拽移动",
            "off": "休息也是为了更好地赚钱 · 拖拽移动",
        }[phase]
        cv.itemconfigure(self.footer, text=footer)

    # -- 退出 -------------------------------------------------------------
    def on_close(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        try:
            self.store.cfg["window"] = {"x": self.winfo_x(), "y": self.winfo_y()}
            self.store.cfg["ball"] = (self._page == "ball")
            self.store.save_cfg()
        except Exception:
            pass
        self.destroy()


# ----------------------------------------------------------------------------
# 自测 & 启动
# ----------------------------------------------------------------------------


def selftest():
    cfg = dict(DEFAULT_CFG)
    cfg["first_use"] = "2026-10-01"
    hist = {}

    t = parse_hhmm("22:00"), parse_hhmm("06:00")
    s, e = shift_span(date(2026, 11, 5), *t)
    assert (e - s) == timedelta(hours=8), "夜班跨天计算错误"

    # 上班前
    st = compute(cfg, hist, datetime(2026, 11, 5, 7, 30))  # 周四，工作日
    assert st["phase"] == "pre" and st["today_earned"] == 0 and st["remaining"] == 1.5 * 3600
    # 上班中
    st = compute(cfg, hist, datetime(2026, 11, 5, 13, 0))
    assert st["phase"] == "in" and abs(st["pct"] - 4 / 9) < 1e-9
    assert abs(st["today_earned"] - 300 * 4 / 9) < 1e-6
    assert abs(st["rate"] - 300 / 32400) < 1e-12
    # 下班后未冻结 → 全额
    st = compute(cfg, hist, datetime(2026, 11, 5, 19, 0))
    assert st["phase"] == "post" and st["today_earned"] == 300
    # 冻结后不重复计入
    hist["2026-11-05"] = 300.0
    st = compute(cfg, hist, datetime(2026, 11, 5, 19, 0))
    assert st["today_earned"] == 300 and abs(st["month_sum"] - 300) < 1e-9
    # 周六不上班
    st = compute(cfg, hist, datetime(2026, 11, 7, 10, 0))  # 周六
    assert st["phase"] == "off"
    # 补记
    hist2 = {}
    stub = types.SimpleNamespace(cfg=dict(cfg), history=hist2, save_history=lambda: None)
    backfill(stub, datetime(2026, 11, 5, 19, 0))  # 10-01(周四)起，10月约 22 个工作日 + 当天
    assert "2026-10-01" in hist2 and "2026-10-03" not in hist2  # 10-03 是周六
    assert "2026-11-05" in hist2  # 已下班，当天补记

    assert fmt_hms(0) == "00:00:00" and fmt_hms(3600 + 60 + 1) == "01:01:01"
    assert fmt_money(1234567.891) == "¥1,234,567.89"
    print("selftest OK")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return

    enable_dpi_awareness()
    store = Store()
    backfill(store, datetime.now())
    app = MainWindow(store)
    app.mainloop()
    sys.exit(0)


if __name__ == "__main__":
    main()
