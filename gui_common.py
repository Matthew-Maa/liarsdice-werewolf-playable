# -*- coding: utf-8 -*-
"""RA-027 共用 pygame 界面层：素材、字体、按钮、头像、骰子、夜晚背景、音乐。"""
import math
import os
import random
import struct
import sys
import wave
from pathlib import Path

import pygame

W, H = 1280, 720
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = _BASE / "assets"

# ---------- 颜色 ----------
GOLD = (214, 178, 94)
GOLD_L = (246, 220, 150)
CREAM = (246, 239, 224)
INK = (24, 20, 18)
RED = (176, 58, 58)
RED_L = (224, 96, 90)
GREEN = (46, 118, 82)
GREEN_L = (96, 190, 138)
BLUE = (82, 122, 180)
MUTED = (168, 158, 142)
DARK = (18, 16, 15)

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key in _fonts:
        return _fonts[key]
    paths = [r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
             r"C:\Windows\Fonts\simhei.ttf"]
    for p in paths:
        if os.path.exists(p):
            try:
                f = pygame.font.Font(p, size)
                _fonts[key] = f
                return f
            except Exception:
                pass
    f = pygame.font.SysFont("microsoftyahei", size, bold=bold)
    _fonts[key] = f
    return f


def text(surface, s, size, color=CREAM, pos=(0, 0), bold=False,
         align="left", alpha=255, valign="top"):
    f = font(size, bold)
    img = f.render(str(s), True, color)
    if alpha < 255:
        img.set_alpha(alpha)
    x, y = pos
    if align == "center":
        x -= img.get_width() // 2
    elif align == "right":
        x -= img.get_width()
    if valign == "center":
        y -= img.get_height() // 2
    elif valign == "bottom":
        y -= img.get_height()
    surface.blit(img, (x, y))
    return img.get_rect(topleft=(x, y))


def wrap(s, size, max_w, bold=False):
    f = font(size, bold)
    lines, cur = [], ""
    for ch in str(s):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if f.size(cur + ch)[0] > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def wrap_text(surface, s, size, color, pos, max_w, line_h=None,
              bold=False, align="left"):
    x, y = pos
    lh = line_h or int(size * 1.45)
    for i, line in enumerate(wrap(s, size, max_w, bold)):
        text(surface, line, size, color, (x, y + i * lh), bold=bold,
             align=align)
    return y + len(wrap(s, size, max_w, bold)) * lh


def panel(surface, rect, color=(26, 22, 20), alpha=210, border=GOLD,
          radius=16, border_w=2):
    r = pygame.Rect(rect)
    s = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
    pygame.draw.rect(s, (*color, alpha), s.get_rect(), border_radius=radius)
    if border:
        pygame.draw.rect(s, (*border, min(255, alpha + 20)), s.get_rect(),
                         border_w, border_radius=radius)
    surface.blit(s, r.topleft)


def pill(surface, rect, label, color=GOLD, text_color=INK, size=18):
    r = pygame.Rect(rect)
    s = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
    pygame.draw.rect(s, (*color, 235), s.get_rect(), border_radius=r.h // 2)
    surface.blit(s, r.topleft)
    text(surface, label, size, text_color, r.center, align="center",
         bold=True, valign="center")


def cover(img, size):
    sw, sh = size
    iw, ih = img.get_size()
    scale = max(sw / iw, sh / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = pygame.transform.smoothscale(img, (nw, nh))
    x = (nw - sw) // 2
    y = (nh - sh) // 2
    return img.subsurface(pygame.Rect(x, y, sw, sh)).copy()


class Bank:
    def __init__(self):
        self.raw = {}
        self.scaled = {}
        self.avatars = {}

    def image(self, name, size=None):
        key = (name, size)
        if key in self.scaled:
            return self.scaled[key]
        if name not in self.raw:
            p = ASSETS / name
            img = pygame.image.load(str(p)).convert()
            self.raw[name] = img
        img = self.raw[name]
        if size:
            img = cover(img, size)
        self.scaled[key] = img
        return img

    def avatar(self, name, radius, gray=False, ring=GOLD):
        key = (name, radius, gray, ring)
        if key in self.avatars:
            return self.avatars[key]
        img = self.raw.get(name)
        if img is None:
            img = pygame.image.load(str(ASSETS / name)).convert()
            self.raw[name] = img
        diameter = radius * 2
        iw, ih = img.get_size()
        side = min(iw, ih)
        sq = img.subsurface(pygame.Rect((iw - side) // 2, (ih - side) // 2,
                                        side, side)).copy()
        sq = pygame.transform.smoothscale(sq, (diameter, diameter))
        if gray:
            sq = pygame.transform.grayscale(sq)
        mask = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255),
                           (radius, radius), radius)
        out = pygame.Surface((diameter + 8, diameter + 8), pygame.SRCALPHA)
        if ring:
            pygame.draw.circle(out, (*ring, 230),
                               (radius + 4, radius + 4), radius + 3, 3)
        out.blit(sq, (4, 4))
        out.blit(mask, (4, 4), special_flags=pygame.BLEND_RGBA_MULT)
        self.avatars[key] = out
        return out


bank = Bank()


def draw_avatar(surface, name, center, radius, gray=False, ring=GOLD,
                alpha=255):
    img = bank.avatar(name, radius, gray=gray, ring=ring)
    if alpha < 255:
        img = img.copy()
        img.set_alpha(alpha)
    surface.blit(img, (center[0] - radius - 4, center[1] - radius - 4))


def draw_die(surface, rect, value, hidden=False, highlight=False):
    r = pygame.Rect(rect)
    shadow = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, 90), shadow.get_rect(),
                     border_radius=r.w // 6)
    surface.blit(shadow, (r.x + 4, r.y + 6))
    body = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
    if hidden:
        pygame.draw.rect(body, (86, 28, 30), body.get_rect(),
                         border_radius=r.w // 6)
        pygame.draw.rect(body, (*GOLD, 255), body.get_rect(), 3,
                         border_radius=r.w // 6)
        pygame.draw.circle(body, (*GOLD, 220), (r.w // 2, r.h // 2),
                           max(8, r.w // 9), 3)
        surface.blit(body, r.topleft)
        return
    pygame.draw.rect(body, (250, 247, 239), body.get_rect(),
                     border_radius=r.w // 6)
    border_col = GOLD_L if highlight else (190, 180, 160)
    pygame.draw.rect(body, (*border_col, 255),
                     body.get_rect(), 3, border_radius=r.w // 6)
    surface.blit(body, r.topleft)
    # pips
    cx, cy = r.center
    d = r.w
    off = d * 0.24
    pr = max(3, int(d * 0.075))
    pts = {
        1: [(0, 0)],
        2: [(-1, -1), (1, 1)],
        3: [(-1, -1), (0, 0), (1, 1)],
        4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
        5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
        6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
    }.get(value, [])
    for dx, dy in pts:
        pygame.draw.circle(surface, (34, 30, 28),
                           (int(cx + dx * off), int(cy + dy * off)), pr)


class Button:
    def __init__(self, rect, label, kind="primary", size=24, enabled=True,
                 tag=None):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.kind = kind
        self.size = size
        self.enabled = enabled
        self.tag = tag
        self.hover = False

    def draw(self, surface):
        if not self.enabled:
            bg, fg = (52, 48, 44), (120, 112, 100)
        elif self.kind == "primary":
            bg, fg = ((GOLD if not self.hover else GOLD_L), INK)
        elif self.kind == "danger":
            bg, fg = ((RED if not self.hover else RED_L), CREAM)
        elif self.kind == "ghost":
            bg, fg = ((34, 30, 27) if not self.hover else (52, 46, 40)), CREAM
        else:
            bg, fg = ((60, 54, 46) if not self.hover else (84, 74, 60)), CREAM
        r = self.rect
        s = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
        pygame.draw.rect(s, (*bg, 240), s.get_rect(), border_radius=12)
        if self.kind == "ghost":
            pygame.draw.rect(s, (*GOLD, 170), s.get_rect(), 2,
                             border_radius=12)
        surface.blit(s, r.topleft)
        text(surface, self.label, self.size, fg, r.center, align="center",
             bold=True, valign="center")

    def handle(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.enabled and self.rect.collidepoint(event.pos):
                return True
        return False


class TextInput:
    def __init__(self, rect, value="", size=22, max_len=12):
        self.rect = pygame.Rect(rect)
        self.value = value
        self.size = size
        self.max_len = max_len
        self.active = False
        self.cursor_t = 0

    def handle(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.active = self.rect.collidepoint(event.pos)
        if not self.active:
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.value = self.value[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.active = False
        if event.type == pygame.TEXTINPUT:
            self.value = (self.value + event.text)[:self.max_len]

    def draw(self, surface, placeholder="你的名字"):
        pygame.draw.rect(surface, (30, 26, 23), self.rect, border_radius=10)
        pygame.draw.rect(surface, GOLD if self.active else (90, 80, 66),
                         self.rect, 2, border_radius=10)
        shown = self.value or placeholder
        col = CREAM if self.value else MUTED
        text(surface, shown, self.size, col,
             (self.rect.x + 14, self.rect.centery - self.size // 2 - 1))
        if self.active:
            self.cursor_t += 1
            if (self.cursor_t // 30) % 2 == 0:
                x = self.rect.x + 14 + font(self.size).size(self.value)[0] + 2
                pygame.draw.line(surface, GOLD, (x, self.rect.y + 12),
                                 (x, self.rect.bottom - 12), 2)


# 背景音乐不入库（版权与体积原因）。要音乐时把 mp3 放进 assets/，
# 按下列文件名命名即可；缺文件时游戏正常，只是没有背景音乐。
MUSIC_TRACKS = [
    ("曲目 1", "bgm_1.mp3"),
    ("曲目 2", "bgm_2.mp3"),
    ("曲目 3", "bgm_3.mp3"),
    ("曲目 4", "bgm_4.mp3"),
    ("曲目 5", "bgm_5.wav"),
]


class Music:
    def __init__(self, tracks=None, volume=0.28, mode="list", start=0,
                 enabled=True):
        self.tracks = list(tracks or MUSIC_TRACKS)
        self.index = int(start) % max(1, len(self.tracks))
        self.mode = mode if mode in ("list", "single") else "list"
        self.volume = max(0.0, min(1.0, float(volume)))
        self.enabled = bool(enabled)
        self.ok = False
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            self.ok = True
            self._load(self.index, play=self.enabled)
        except Exception:
            self.ok = False

    def _file(self, i=None):
        t = self.tracks[self.index if i is None else i]
        return t[1] if isinstance(t, (tuple, list)) else t

    def _name(self, i=None):
        t = self.tracks[self.index if i is None else i]
        return t[0] if isinstance(t, (tuple, list)) else str(t)

    def _load(self, i, play=True):
        if not self.ok:
            return
        self.index = int(i) % len(self.tracks)
        try:
            pygame.mixer.music.load(str(ASSETS / self._file()))
            pygame.mixer.music.set_volume(self.volume)
            if play:
                pygame.mixer.music.play()
            else:
                pygame.mixer.music.stop()
        except Exception:
            self.ok = False

    def tick(self):
        """列表自动切歌 / 单曲循环；每帧调用。"""
        if not self.ok or not self.enabled:
            return
        try:
            if not pygame.mixer.music.get_busy():
                if self.mode == "single":
                    self._load(self.index, play=True)
                else:
                    self.next_track()
        except Exception:
            pass

    def next_track(self):
        if not self.ok:
            return
        self._load((self.index + 1) % len(self.tracks), play=self.enabled)

    def set_track(self, i):
        self._load(i, play=self.enabled)

    def set_mode(self, mode):
        if mode in ("list", "single"):
            self.mode = mode

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, float(v)))
        try:
            pygame.mixer.music.set_volume(self.volume)
        except Exception:
            pass

    def name(self):
        return self._name()

    def toggle(self):
        if not self.ok:
            return
        self.enabled = not self.enabled
        try:
            if self.enabled:
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.unpause()
                else:
                    self._load(self.index, play=True)
            else:
                pygame.mixer.music.pause()
        except Exception:
            pass

    def draw(self, surface, pos=(W - 24, 18)):
        if not self.ok:
            return
        label = "音乐 开" if self.enabled else "音乐 关"
        text(surface, label, 18, CREAM if self.enabled else MUTED, pos,
             align="right")


class Sfx:
    def __init__(self):
        self.enabled = True
        self.sounds = {}
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            for n in ("click", "roll", "open", "win"):
                p = ASSETS / ("sfx_%s.wav" % n)
                if p.exists():
                    self.sounds[n] = pygame.mixer.Sound(str(p))
                    self.sounds[n].set_volume(0.35)
        except Exception:
            self.sounds = {}

    def play(self, name):
        if not self.enabled:
            return
        if name in self.sounds:
            try:
                self.sounds[name].play()
            except Exception:
                pass


def ensure_sfx():
    """生成几个短音效（若不存在）。"""
    ASSETS.mkdir(exist_ok=True)
    specs = {
        "click": [(880, 0.05, 0.18)],
        "roll": [(220, 0.04, 0.12), (330, 0.05, 0.12), (440, 0.06, 0.10)],
        "open": [(180, 0.06, 0.25), (140, 0.10, 0.30)],
        "win": [(523, 0.10, 0.18), (659, 0.10, 0.18), (784, 0.18, 0.20)],
    }
    for name, notes in specs.items():
        p = ASSETS / ("sfx_%s.wav" % name)
        if p.exists():
            continue
        rate = 22050
        frames = bytearray()
        for freq, dur, amp in notes:
            n = int(rate * dur)
            for i in range(n):
                env = min(1.0, i / (rate * 0.01)) * (1 - i / n)
                v = int(32767 * amp * env * math.sin(2 * math.pi * freq * i / rate))
                frames += struct.pack("<h", v)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))


def make_ambient(path, seconds=24, rate=22050):
    """生成狼人杀用的低沉环境音（周期性，可无缝循环）。"""
    if Path(path).exists():
        return
    rnd = random.Random(7)
    n = rate * seconds
    frames = bytearray()
    for i in range(n):
        t = i / rate
        # 三个低频正弦 + 缓慢起伏
        v = (0.20 * math.sin(2 * math.pi * 55 * t)
             + 0.12 * math.sin(2 * math.pi * 82.5 * t + 0.7)
             + 0.07 * math.sin(2 * math.pi * 110 * t + 1.3))
        v *= 0.55 + 0.45 * math.sin(2 * math.pi * t / seconds * 2)
        noise = rnd.uniform(-0.035, 0.035)
        v = max(-1.0, min(1.0, v + noise))
        frames += struct.pack("<h", int(32767 * v))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


_night_bg = None


def night_bg(size=(W, H)):
    global _night_bg
    if _night_bg is not None:
        return _night_bg
    sw, sh = size
    surf = pygame.Surface(size).convert()
    # 渐变
    for y in range(sh):
        f = y / sh
        col = (int(8 + 18 * (1 - f)), int(14 + 22 * (1 - f)),
               int(30 + 40 * (1 - f)))
        pygame.draw.line(surf, col, (0, y), (sw, y))
    rnd = random.Random(11)
    for _ in range(180):
        x, y = rnd.randrange(sw), rnd.randrange(int(sh * 0.65))
        b = rnd.randrange(120, 230)
        pygame.draw.circle(surf, (b, b, min(255, b + 20)), (x, y),
                           1 if rnd.random() < 0.8 else 2)
    # 月亮
    mx, my, mr = int(sw * 0.78), int(sh * 0.20), 58
    for k in range(12, 0, -1):
        a = int(4 + 6 * (12 - k))
        g = pygame.Surface((mr * 2 + k * 8, mr * 2 + k * 8), pygame.SRCALPHA)
        pygame.draw.circle(g, (220, 220, 200, a), (g.get_width() // 2,
                                                   g.get_height() // 2),
                           mr + k * 4)
        surf.blit(g, (mx - g.get_width() // 2, my - g.get_height() // 2))
    pygame.draw.circle(surf, (238, 234, 210), (mx, my), mr)
    pygame.draw.circle(surf, (210, 206, 184), (mx - 18, my - 12), 9)
    pygame.draw.circle(surf, (210, 206, 184), (mx + 20, my + 16), 6)
    # 远山与树
    pygame.draw.polygon(surf, (10, 18, 24),
                        [(0, sh), (0, int(sh * 0.68)), (int(sw * .18), int(sh * .55)),
                         (int(sw * .36), int(sh * .72)), (int(sw * .55), int(sh * .58)),
                         (int(sw * .78), int(sh * .70)), (sw, int(sh * .55)), (sw, sh)])
    for x in range(-40, sw + 60, 42):
        h = rnd.randrange(120, 260)
        base = int(sh * 0.86) + rnd.randrange(-20, 30)
        pygame.draw.polygon(surf, (6, 12, 16),
                            [(x, base), (x + 26, base - h), (x + 52, base)])
        pygame.draw.rect(surf, (6, 12, 16), (x + 22, base - 12, 8, 30))
    _night_bg = surf
    return surf


def draw_night(surface, t=0.0):
    surface.blit(night_bg(), (0, 0))
    # 雾
    for k in range(3):
        x = int((t * (8 + k * 5) + k * 430) % (W + 500)) - 250
        y = int(H * 0.66 + k * 24)
        s = pygame.Surface((520, 120), pygame.SRCALPHA)
        pygame.draw.ellipse(s, (150, 170, 190, 10), s.get_rect())
        surface.blit(s, (x, y))


class Toast:
    def __init__(self):
        self.msg = ""
        self.until = 0
        self.color = CREAM

    def show(self, msg, color=CREAM, ms=2000):
        self.msg = msg
        self.color = color
        self.until = pygame.time.get_ticks() + ms

    def draw(self, surface):
        if pygame.time.get_ticks() < self.until:
            f = font(22, True)
            w = f.size(self.msg)[0] + 40
            r = pygame.Rect((W - w) // 2, 24, w, 44)
            panel(surface, r, color=(20, 18, 16), alpha=225, border=self.color,
                  radius=22, border_w=2)
            text(surface, self.msg, 22, self.color, r.center, align="center",
                 bold=True, valign="center")


def ease_out(x):
    return 1 - (1 - x) ** 3


def lerp(a, b, t):
    return a + (b - a) * t


def fit_rect(rect, n, pad=12):
    rect = pygame.Rect(rect)
    w = (rect.w - pad * (n + 1)) / n
    return [pygame.Rect(int(rect.x + pad + i * (w + pad)), rect.y,
                        int(w), rect.h) for i in range(n)]
