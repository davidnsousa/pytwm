#!/usr/bin/env python3
# SPDX-License-Identifier: MIT (see LICENSE; licenses of the projects it is based on: LICENSES/)
"""
pytwm - a small desktop for X11 in Python: a tiling window manager with its own
launcher, status bar, screen lock and notifications.

Your preferences (colors, keys, programs, menus) live in pref.py, next to this
file, and the code behind the status bar, menus and watchers in parts.py. Edit
those; this file is the window manager itself. Saving any of the three
restarts pytwm in place, keeping every window where it is.

It lives in ~/.config/pytwm, with pref.py and parts.py next to it, its Python
packages in ~/.local/share/pytwm/venv, and ~/.xinitrc starts it with
    exec ~/.local/share/pytwm/venv/bin/python ~/.config/pytwm/pytwm.py
"""

import ctypes
import json
import os
import queue
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

from Xlib import X, XK, Xatom, Xcursorfont, display, error
from Xlib.keysymdef import xf86
from Xlib.protocol import event as xevent

VERSION = "1.0"
ALLTAGS = ~0
TAGBAR, LTSYMBOL, STATUSTEXT, WINTITLE, CLIENTWIN, ROOTWIN = range(6)  # clicks
CUR_NORMAL, CUR_RESIZE, CUR_MOVE = range(3)
COL_FG, COL_BG, COL_BORDER = range(3)

BUTTONMASK = X.ButtonPressMask | X.ButtonReleaseMask
MOUSEMASK = BUTTONMASK | X.PointerMotionMask
TAGMASK = 0           # set once pref.py is loaded

# modifiers for pref.py
SUPER = X.Mod4Mask    # the Windows key
ALT = X.Mod1Mask
SHIFT = X.ShiftMask
CTRL = X.ControlMask

SELF = os.path.abspath(__file__)
HERE = os.path.dirname(SELF)
# the files watched for reload (the fallback copy still watches the real ones)
WATCH = os.environ.get("PYTWM_WATCH", SELF)
prefs_file = os.environ.get("PYTWM_WATCH_PREF", os.path.join(HERE, "pref.py"))
pytwm_file = WATCH                                 # this file, for pref.py
PREF = os.environ.get("PYTWM_PREF", prefs_file)    # the pref.py actually loaded
parts_file = os.environ.get("PYTWM_WATCH_PARTS", os.path.join(HERE, "parts.py"))
PARTS = os.environ.get("PYTWM_PARTS", parts_file)  # the parts.py actually loaded
RUNDIR = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


def die(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


# ============================================================================
#  Drawing text and boxes with Xft, through ctypes
#
#  python-xlib speaks the X protocol itself and cannot render Xft fonts, so
#  the bar is drawn with libXft, on a second
#  connection opened by libX11. Window ids are global, so that connection can
#  draw on the bar windows the window manager connection created.
# ============================================================================

_x11 = ctypes.CDLL("libX11.so.6")
_xft = ctypes.CDLL("libXft.so.2")
_fc = ctypes.CDLL("libfontconfig.so.1")
c_dpy = ctypes.c_void_p
XID = ctypes.c_ulong


class XRenderColor(ctypes.Structure):
    _fields_ = [("red", ctypes.c_ushort), ("green", ctypes.c_ushort),
                ("blue", ctypes.c_ushort), ("alpha", ctypes.c_ushort)]


class XftColor(ctypes.Structure):
    _fields_ = [("pixel", ctypes.c_ulong), ("color", XRenderColor)]


class XftFont(ctypes.Structure):
    _fields_ = [("ascent", ctypes.c_int), ("descent", ctypes.c_int),
                ("height", ctypes.c_int), ("max_advance_width", ctypes.c_int),
                ("charset", ctypes.c_void_p), ("pattern", ctypes.c_void_p)]


class XGlyphInfo(ctypes.Structure):
    _fields_ = [("width", ctypes.c_ushort), ("height", ctypes.c_ushort),
                ("x", ctypes.c_short), ("y", ctypes.c_short),
                ("xOff", ctypes.c_short), ("yOff", ctypes.c_short)]


def _sig(lib, name, res, *args):
    f = getattr(lib, name)
    f.restype = res
    f.argtypes = list(args)
    return f

XOpenDisplay = _sig(_x11, "XOpenDisplay", c_dpy, ctypes.c_char_p)
XDefaultScreen = _sig(_x11, "XDefaultScreen", ctypes.c_int, c_dpy)
XRootWindow = _sig(_x11, "XRootWindow", XID, c_dpy, ctypes.c_int)
XDefaultVisual = _sig(_x11, "XDefaultVisual", ctypes.c_void_p, c_dpy, ctypes.c_int)
XDefaultColormap = _sig(_x11, "XDefaultColormap", XID, c_dpy, ctypes.c_int)
XDefaultDepth = _sig(_x11, "XDefaultDepth", ctypes.c_int, c_dpy, ctypes.c_int)
XCreatePixmap = _sig(_x11, "XCreatePixmap", XID, c_dpy, XID, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
XFreePixmap = _sig(_x11, "XFreePixmap", ctypes.c_int, c_dpy, XID)
XCreateGC = _sig(_x11, "XCreateGC", ctypes.c_void_p, c_dpy, XID, ctypes.c_ulong, ctypes.c_void_p)
XSetLineAttributes = _sig(_x11, "XSetLineAttributes", ctypes.c_int, c_dpy, ctypes.c_void_p,
                          ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int)
XSetForeground = _sig(_x11, "XSetForeground", ctypes.c_int, c_dpy, ctypes.c_void_p, ctypes.c_ulong)
XFillRectangle = _sig(_x11, "XFillRectangle", ctypes.c_int, c_dpy, XID, ctypes.c_void_p,
                      ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint)
XDrawRectangle = _sig(_x11, "XDrawRectangle", ctypes.c_int, c_dpy, XID, ctypes.c_void_p,
                      ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint)
XCopyArea = _sig(_x11, "XCopyArea", ctypes.c_int, c_dpy, XID, XID, ctypes.c_void_p,
                 ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int)
XSync = _sig(_x11, "XSync", ctypes.c_int, c_dpy, ctypes.c_int)
XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, c_dpy, ctypes.c_void_p)
XSetErrorHandler = _sig(_x11, "XSetErrorHandler", ctypes.c_void_p, XErrorHandler)

XftFontOpenName = _sig(_xft, "XftFontOpenName", ctypes.POINTER(XftFont), c_dpy, ctypes.c_int, ctypes.c_char_p)
XftFontOpenPattern = _sig(_xft, "XftFontOpenPattern", ctypes.POINTER(XftFont), c_dpy, ctypes.c_void_p)
XftFontClose = _sig(_xft, "XftFontClose", None, c_dpy, ctypes.POINTER(XftFont))
XftFontMatch = _sig(_xft, "XftFontMatch", ctypes.c_void_p, c_dpy, ctypes.c_int, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int))
XftCharExists = _sig(_xft, "XftCharExists", ctypes.c_int, c_dpy, ctypes.POINTER(XftFont), ctypes.c_uint)
XftTextExtentsUtf8 = _sig(_xft, "XftTextExtentsUtf8", None, c_dpy, ctypes.POINTER(XftFont),
                          ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(XGlyphInfo))
XftColorAllocName = _sig(_xft, "XftColorAllocName", ctypes.c_int, c_dpy, ctypes.c_void_p, XID,
                         ctypes.c_char_p, ctypes.POINTER(XftColor))
XftDrawCreate = _sig(_xft, "XftDrawCreate", ctypes.c_void_p, c_dpy, XID, ctypes.c_void_p, XID)
XftDrawDestroy = _sig(_xft, "XftDrawDestroy", None, ctypes.c_void_p)
XftDrawStringUtf8 = _sig(_xft, "XftDrawStringUtf8", None, ctypes.c_void_p, ctypes.POINTER(XftColor),
                         ctypes.POINTER(XftFont), ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int)

FcNameParse = _sig(_fc, "FcNameParse", ctypes.c_void_p, ctypes.c_char_p)
FcPatternDuplicate = _sig(_fc, "FcPatternDuplicate", ctypes.c_void_p, ctypes.c_void_p)
FcPatternDestroy = _sig(_fc, "FcPatternDestroy", None, ctypes.c_void_p)
FcPatternAddCharSet = _sig(_fc, "FcPatternAddCharSet", ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
FcPatternAddBool = _sig(_fc, "FcPatternAddBool", ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int)
FcCharSetCreate = _sig(_fc, "FcCharSetCreate", ctypes.c_void_p)
FcCharSetAddChar = _sig(_fc, "FcCharSetAddChar", ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
FcCharSetDestroy = _sig(_fc, "FcCharSetDestroy", None, ctypes.c_void_p)
FcConfigSubstitute = _sig(_fc, "FcConfigSubstitute", ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
FcDefaultSubstitute = _sig(_fc, "FcDefaultSubstitute", None, ctypes.c_void_p)

UTF_INVALID = 0xFFFD
_UTF_LENS = [1] * 16 + [0] * 8 + [2] * 4 + [3] * 2 + [4] + [0]
_UTF_LEADING_MASK = (0x7F, 0x1F, 0x0F, 0x07)
_UTF_OVERLONG = (0x0, 0x80, 0x0800, 0x10000)


def utf8decode(s, i):
    """Returns (length, codepoint, err) for the UTF-8 sequence at s[i]."""
    n = _UTF_LENS[s[i] >> 3]
    if n == 0:
        return 1, UTF_INVALID, 1
    cp = s[i] & _UTF_LEADING_MASK[n - 1]
    for k in range(1, n):
        if i + k >= len(s) or (s[i + k] & 0xC0) != 0x80:
            return k, UTF_INVALID, 1
        cp = (cp << 6) | (s[i + k] & 0x3F)
    # out of range, surrogate, overlong encoding
    if cp > 0x10FFFF or (cp >> 11) == 0x1B or cp < _UTF_OVERLONG[n - 1]:
        return n, UTF_INVALID, 1
    return n, cp, 0


def is_icon(cp):
    """True for the characters fonts keep for their own symbols, where icon
    fonts like the Nerd Fonts put theirs."""
    return 0xE000 <= cp <= 0xF8FF or cp >= 0xF0000


class Fnt:
    def __init__(self, xfont, pattern):
        self.xfont = xfont
        self.pattern = pattern
        self.ascent = xfont.contents.ascent
        self.h = xfont.contents.ascent + xfont.contents.descent


class Drw:
    def __init__(self, w, h):
        self.dpy = XOpenDisplay(None)
        if not self.dpy:
            die("pytwm: cannot open display for drawing")
        # libX11's default error handler exits; errors here (a bar destroyed
        # mid-draw) are harmless, so ignore them.
        self._errh = XErrorHandler(lambda d, e: 0)
        XSetErrorHandler(self._errh)
        self.screen = XDefaultScreen(self.dpy)
        self.root = XRootWindow(self.dpy, self.screen)
        self.visual = XDefaultVisual(self.dpy, self.screen)
        self.cmap = XDefaultColormap(self.dpy, self.screen)
        self.depth = XDefaultDepth(self.dpy, self.screen)
        self.w, self.h = w, h
        self.drawable = XCreatePixmap(self.dpy, self.root, w, h, self.depth)
        self.gc = XCreateGC(self.dpy, self.root, 0, None)
        XSetLineAttributes(self.dpy, self.gc, 1, 0, 1, 0)  # LineSolid, CapButt, JoinMiter
        self.fonts = []
        self.scheme = None
        self.ellipsis_width = 0
        self.invalid_width = 0
        self.nomatches = [0] * 128

    def resize(self, w, h):
        self.w, self.h = w, h
        if self.drawable:
            XFreePixmap(self.dpy, self.drawable)
        self.drawable = XCreatePixmap(self.dpy, self.root, w, h, self.depth)

    def xfont_create(self, fontname=None, fontpattern=None):
        pattern = None
        if fontname:
            xfont = XftFontOpenName(self.dpy, self.screen, fontname.encode())
            if not xfont:
                sys.stderr.write("error, cannot load font from name: '%s'\n" % fontname)
                return None
            pattern = FcNameParse(fontname.encode())
            if not pattern:
                sys.stderr.write("error, cannot parse font name to pattern: '%s'\n" % fontname)
                XftFontClose(self.dpy, xfont)
                return None
        elif fontpattern:
            xfont = XftFontOpenPattern(self.dpy, fontpattern)
            if not xfont:
                sys.stderr.write("error, cannot load font from pattern.\n")
                return None
        else:
            die("no font specified.")
        return Fnt(xfont, pattern)

    def xfont_free(self, font):
        if not font:
            return
        if font.pattern:
            FcPatternDestroy(font.pattern)
        XftFontClose(self.dpy, font.xfont)

    def fontset_create(self, names):
        self.fonts = [f for f in (self.xfont_create(n) for n in names) if f]
        return self.fonts

    def clr_create(self, name):
        c = XftColor()
        if not XftColorAllocName(self.dpy, self.visual, self.cmap, name.encode(), ctypes.byref(c)):
            die("error, cannot allocate color '%s'" % name)
        return c

    def scm_create(self, names):
        return [self.clr_create(n) for n in names]

    def setscheme(self, scm):
        self.scheme = scm

    def rect(self, x, y, w, h, filled, invert):
        if not self.scheme:
            return
        XSetForeground(self.dpy, self.gc, self.scheme[COL_BG if invert else COL_FG].pixel)
        if filled:
            XFillRectangle(self.dpy, self.drawable, self.gc, x, y, w, h)
        else:
            XDrawRectangle(self.dpy, self.drawable, self.gc, x, y, w - 1, h - 1)

    def font_getexts(self, font, text):
        ext = XGlyphInfo()
        XftTextExtentsUtf8(self.dpy, font.xfont, text, len(text), ctypes.byref(ext))
        return ext.xOff

    def text(self, x, y, w, h, lpad, text, invert):
        """Draw text in a box. With x, y, w, h all 0 it only measures the text."""
        render = bool(x or y or w or h)
        if (render and (not self.scheme or not w)) or text is None or not self.fonts:
            return 0
        t = text.encode("utf-8", "replace")
        d = None
        if not render:
            w = invert if invert else 0xFFFFFFFF
        else:
            XSetForeground(self.dpy, self.gc, self.scheme[COL_FG if invert else COL_BG].pixel)
            XFillRectangle(self.dpy, self.drawable, self.gc, x, y, w, h)
            if w < lpad:
                return x + w
            d = XftDrawCreate(self.dpy, self.drawable, self.visual, self.cmap)
            x += lpad
            w -= lpad

        usedfont = self.fonts[0]
        if not self.ellipsis_width and render:
            self.ellipsis_width = self.fontset_getwidth("...")
        if not self.invalid_width and render:
            self.invalid_width = self.fontset_getwidth("�")
        pos = 0
        charexists = overflow = 0
        ellipsis_x = ellipsis_w = 0
        utf8codepoint = 0
        while True:
            ew = ellipsis_len = utf8err = utf8strlen = 0
            utf8str = pos
            nextfont = None
            while pos < len(t):
                utf8charlen, utf8codepoint, utf8err = utf8decode(t, pos)
                for curfont in self.fonts:
                    charexists = charexists or XftCharExists(self.dpy, curfont.xfont, utf8codepoint)
                    if charexists:
                        tmpw = self.font_getexts(curfont, t[pos:pos + utf8charlen])
                        if ew + self.ellipsis_width <= w:
                            # keep track where the ellipsis still fits
                            ellipsis_x = x + ew
                            ellipsis_w = w - ew
                            ellipsis_len = utf8strlen
                        if ew + tmpw > w:
                            overflow = 1
                            if not render:
                                x += tmpw
                            else:
                                utf8strlen = ellipsis_len
                        elif curfont is usedfont:
                            pos += utf8charlen
                            utf8strlen += 0 if utf8err else utf8charlen
                            ew += 0 if utf8err else tmpw
                        else:
                            nextfont = curfont
                        break
                if overflow or not charexists or nextfont or utf8err:
                    break
                charexists = 0

            if utf8strlen:
                if render:
                    ty = y + (h - usedfont.h) // 2 + usedfont.ascent
                    run = t[utf8str:utf8str + utf8strlen]
                    if usedfont is not self.fonts[0] and all(is_icon(ord(c)) for c in run.decode("utf-8", "replace")):
                        # icons (a font's private characters) centered on their own shape
                        ext = XGlyphInfo()
                        XftTextExtentsUtf8(self.dpy, usedfont.xfont, run, utf8strlen, ctypes.byref(ext))
                        ty = y + (h - ext.height + 1) // 2 + ext.y
                    XftDrawStringUtf8(d, ctypes.byref(self.scheme[COL_BG if invert else COL_FG]),
                                      usedfont.xfont, x, ty, t[utf8str:utf8str + utf8strlen], utf8strlen)
                x += ew
                w -= ew
            if utf8err and (not render or self.invalid_width < w):
                if render:
                    self.text(x, y, w, h, 0, "�", invert)
                x += self.invalid_width
                w -= self.invalid_width
            if render and overflow:
                self.text(ellipsis_x, y, ellipsis_w, h, 0, "...", invert)

            if pos >= len(t) or overflow:
                break
            elif nextfont:
                charexists = 0
                usedfont = nextfont
            else:
                # Regardless of whether or not a fallback font is found, the
                # character must be drawn.
                charexists = 1
                hsh = utf8codepoint & 0xFFFFFFFF
                hsh = (((hsh >> 16) ^ hsh) * 0x21F0AAAD) & 0xFFFFFFFF
                hsh = (((hsh >> 15) ^ hsh) * 0xD35A2D97) & 0xFFFFFFFF
                h0 = ((hsh >> 15) ^ hsh) % 128
                h1 = (hsh >> 17) % 128
                # avoid expensive XftFontMatch call when we know we won't find a match
                if self.nomatches[h0] == utf8codepoint or self.nomatches[h1] == utf8codepoint:
                    usedfont = self.fonts[0]
                    continue
                fccharset = FcCharSetCreate()
                FcCharSetAddChar(fccharset, utf8codepoint)
                if not self.fonts[0].pattern:
                    die("the first font in the cache must be loaded from a font string.")
                fcpattern = FcPatternDuplicate(self.fonts[0].pattern)
                FcPatternAddCharSet(fcpattern, b"charset", fccharset)
                FcPatternAddBool(fcpattern, b"scalable", 1)
                FcConfigSubstitute(None, fcpattern, 0)  # FcMatchPattern
                FcDefaultSubstitute(fcpattern)
                result = ctypes.c_int()
                match = XftFontMatch(self.dpy, self.screen, fcpattern, ctypes.byref(result))
                FcCharSetDestroy(fccharset)
                FcPatternDestroy(fcpattern)
                if match:
                    usedfont = self.xfont_create(None, match)
                    if usedfont and XftCharExists(self.dpy, usedfont.xfont, utf8codepoint):
                        self.fonts.append(usedfont)
                    else:
                        self.xfont_free(usedfont)
                        self.nomatches[h1 if self.nomatches[h0] else h0] = utf8codepoint
                        usedfont = self.fonts[0]
        if d:
            XftDrawDestroy(d)
        return x + (w if render else 0)

    def map(self, win, x, y, w, h):
        XCopyArea(self.dpy, self.drawable, win, self.gc, x, y, w, h, x, y)
        XSync(self.dpy, 0)

    def fontset_getwidth_clamp(self, text, n):
        tmp = 0
        if self.fonts and text is not None and n:
            tmp = self.text(0, 0, 0, 0, 0, text, n)
        return min(n, tmp)

    def fontset_getwidth(self, text):
        if not self.fonts or text is None:
            return 0
        return self.text(0, 0, 0, 0, 0, text, 0)


# ============================================================================
#  The window manager
# ============================================================================

class Client:
    def __init__(self, win):
        self.win = win
        self.name = ""
        self.mina = self.maxa = 0.0
        self.x = self.y = self.w = self.h = 0
        self.oldx = self.oldy = self.oldw = self.oldh = 0
        self.basew = self.baseh = self.incw = self.inch = 0
        self.maxw = self.maxh = self.minw = self.minh = 0
        self.hintsvalid = False
        self.bw = self.oldbw = 0
        self.tags = 0
        self.isfixed = self.isfloating = self.isurgent = False
        self.neverfocus = self.oldstate = self.isfullscreen = False
        self.wmstate = None     # the _NET_WM_STATE last set on the window
        self.mon = None
        self.cfact = 1.0      # its share of its column's height (setcfact)


class Layout:
    def __init__(self, symbol, arrange):
        self.symbol = symbol
        self.arrange = arrange


class TagLayouts:
    def __init__(self, m):
        n = len(tags) + 1
        self.curtag = self.prevtag = 1          # current and previous tag
        self.nmasters = [m.nmaster] * n         # number of windows in master area
        self.mfacts = [m.mfact] * n             # mfacts per tag
        self.sellts = [m.sellt] * n             # selected layouts
        self.ltidxs = [[m.lt[0], m.lt[1]] for _ in range(n)]  # tags x layouts
        self.showbars = [m.showbar] * n         # display bar for the current tag


class Monitor:
    def __init__(self):
        self.ltsymbol = ""
        self.mfact = 0.0
        self.nmaster = 0
        self.num = 0
        self.by = 0                                     # bar geometry
        self.mx = self.my = self.mw = self.mh = 0       # screen size
        self.wx = self.wy = self.ww = self.wh = 0       # window area
        self.seltags = 0
        self.sellt = 0
        self.tagset = [1, 1]
        self.showbar = True
        self.topbar = True
        self.clients = []     # tiling order, first is master
        self.sel = None
        self.stack = []       # focus history, first is most recent
        self.barwin = None
        self.lt = [None, None]
        self.taglayouts = None


# variables
broken = "broken"
stext = ""
screen = None
screen_w = screen_h = 0   # X display screen geometry width, height
bh = 0                # bar height
lrpad = 0             # sum of left and right padding for text
numlockmask = 0
running = True
dpy = None
drw = None
mons = []
selmon = None
root = None
wmcheckwin = None
cursor = [None, None, None]
scheme = {}
wmatom = {}
netatom = {}
LAYOUTS = []
KEYS = []
BUTTONS = []
RULES = []
queued = []           # events set aside while a mouse grab loop runs
last_error = None     # shown in the bar after a failed reload or handler crash


def ISVISIBLE(c):
    return c.tags & c.mon.tagset[c.mon.seltags]

def WIDTH(c):
    return c.w + 2 * c.bw

def HEIGHT(c):
    return c.h + 2 * c.bw

def TEXTW(text):
    return drw.fontset_getwidth(text) + lrpad

def CLEANMASK(mask):
    return mask & ~(numlockmask | X.LockMask) & (X.ShiftMask | X.ControlMask | X.Mod1Mask
                                                 | X.Mod2Mask | X.Mod3Mask | X.Mod4Mask | X.Mod5Mask)

def INTERSECT(x, y, w, h, m):
    return (max(0, min(x + w, m.wx + m.ww) - max(x, m.wx))
            * max(0, min(y + h, m.wy + m.wh) - max(y, m.wy)))


def xcall(fn, *args, default=None, **kw):
    """Run a request that waits for a reply; a window that is already gone gives `default`."""
    try:
        return fn(*args, **kw)
    except (error.BadWindow, error.BadDrawable, error.BadMatch, error.BadValue, error.BadAtom):
        return default


def applyrules(c):
    # rule matching
    c.isfloating = False
    c.tags = 0
    ch = xcall(c.win.get_wm_class)
    instance, klass = (ch if ch else (broken, broken))
    for r_class, r_instance, r_title, r_tags, r_isfloating, r_monitor in RULES:
        if ((not r_title or r_title in c.name)
                and (not r_class or r_class in klass)
                and (not r_instance or r_instance in instance)):
            c.isfloating = r_isfloating
            c.tags |= r_tags
            for m in mons:
                if m.num == r_monitor:
                    c.mon = m
                    break
    c.tags = c.tags & TAGMASK if c.tags & TAGMASK else c.mon.tagset[c.mon.seltags]


def applysizehints(c, x, y, w, h, interact):
    m = c.mon
    # set minimum possible
    w = max(1, w)
    h = max(1, h)
    if interact:
        if x > screen_w:
            x = screen_w - WIDTH(c)
        if y > screen_h:
            y = screen_h - HEIGHT(c)
        if x + w + 2 * c.bw < 0:
            x = 0
        if y + h + 2 * c.bw < 0:
            y = 0
    else:
        if x >= m.wx + m.ww:
            x = m.wx + m.ww - WIDTH(c)
        if y >= m.wy + m.wh:
            y = m.wy + m.wh - HEIGHT(c)
        if x + w + 2 * c.bw <= m.wx:
            x = m.wx
        if y + h + 2 * c.bw <= m.wy:
            y = m.wy
    if h < bh:
        h = bh
    if w < bh:
        w = bh
    if resizehints or c.isfloating or not c.mon.lt[c.mon.sellt].arrange:
        if not c.hintsvalid:
            updatesizehints(c)
        # see last two sentences in ICCCM 4.1.2.3
        baseismin = c.basew == c.minw and c.baseh == c.minh
        if not baseismin:  # temporarily remove base dimensions
            w -= c.basew
            h -= c.baseh
        # adjust for aspect limits
        if c.mina > 0 and c.maxa > 0:
            if c.maxa < w / h:
                w = int(h * c.maxa + 0.5)
            elif c.mina < h / w:
                h = int(w * c.mina + 0.5)
        if baseismin:  # increment calculation requires this
            w -= c.basew
            h -= c.baseh
        # adjust for increment value
        if c.incw:
            w -= w % c.incw
        if c.inch:
            h -= h % c.inch
        # restore base dimensions
        w = max(w + c.basew, c.minw)
        h = max(h + c.baseh, c.minh)
        if c.maxw:
            w = min(w, c.maxw)
        if c.maxh:
            h = min(h, c.maxh)
    return (x != c.x or y != c.y or w != c.w or h != c.h), x, y, w, h


def arrange(m):
    if m:
        showhide(m)
    else:
        for mm in mons:
            showhide(mm)
    if m:
        arrangemon(m)
        restack(m)
    else:
        for mm in mons:
            arrangemon(mm)


def arrangemon(m):
    m.ltsymbol = m.lt[m.sellt].symbol
    if m.lt[m.sellt].arrange:
        m.lt[m.sellt].arrange(m)
    for c in m.clients:
        updatewmstate(c)


def updatewmstate(c):
    """Tells the window whether it is fullscreen, or tiled: a tiled window is
    told it is maximized, so apps that draw their own frame (and shadow)
    leave it out and fill their tile."""
    if c.isfullscreen:
        state = [netatom["WMFullscreen"]]
    elif not c.isfloating and c.mon.lt[c.mon.sellt].arrange:
        state = [netatom["WMMaxVert"], netatom["WMMaxHorz"]]
    else:
        state = []
    if state != c.wmstate:
        c.wmstate = state
        c.win.change_property(netatom["WMState"], Xatom.ATOM, 32, state)


def resendwmstate(c):
    """The app changed its own idea of being maximized (its own maximize
    button): the state is cleared, then set again a moment later, as two
    changes the app sees."""
    c.wmstate = []
    c.win.change_property(netatom["WMState"], Xatom.ATOM, 32, [])
    dpy.flush()

    def again():
        if c in c.mon.clients:      # still managed
            updatewmstate(c)
            dpy.flush()
    threading.Timer(0.2, call_main, (again,)).start()


def attach(c):
    c.mon.clients.insert(0, c)


def attachstack(c):
    c.mon.stack.insert(0, c)


def buttonpress(e):
    global selmon
    arg = None
    click = ROOTWIN
    # focus monitor if necessary
    m = wintomon(e.window.id)
    if m and m is not selmon:
        unfocus(selmon.sel, True)
        selmon = m
        focus(None)
    if selmon.barwin and e.window.id == selmon.barwin.id:
        x, i = 0, None
        for t in bartags(selmon):               # the tags shown on the bar
            x += TEXTW(tags[t])
            if e.event_x < x:
                i = t
                break
        if i is not None:
            click = TAGBAR
            arg = 1 << i
        elif e.event_x < x + TEXTW(selmon.ltsymbol):
            click = LTSYMBOL
        elif e.event_x > selmon.ww - TEXTW(uncolored(stext)):
            click = STATUSTEXT
        elif notify_shown():
            notify_click(notify_shown())                      # a click on a notification
            return
        else:
            click = WINTITLE
    else:
        c = wintoclient(e.window.id)
        if c:
            focus(c)
            restack(selmon)
            dpy.allow_events(X.ReplayPointer, X.CurrentTime)
            click = CLIENTWIN
    for b_click, b_mask, b_button, b_func, b_arg in BUTTONS:
        if (click == b_click and b_func and b_button == e.detail
                and CLEANMASK(b_mask) == CLEANMASK(e.state)):
            call(b_func, arg if click == TAGBAR and b_arg is None else b_arg)


def checkotherwm():
    ec = error.CatchError(error.BadAccess)
    root.change_attributes(event_mask=X.SubstructureRedirectMask, onerror=ec)
    dpy.sync()
    if ec.get_error():
        die("pytwm: another window manager is already running")


def cleanup():
    view(ALLTAGS)
    selmon.lt[selmon.sellt] = Layout("", None)
    for m in mons:
        while m.stack:
            unmanage(m.stack[0], False)
    root.ungrab_key(X.AnyKey, X.AnyModifier)
    while mons:
        cleanupmon(mons[0])
    wmcheckwin.destroy()
    dpy.sync()
    dpy.set_input_focus(X.PointerRoot, X.RevertToPointerRoot, X.CurrentTime)
    root.delete_property(netatom["ActiveWindow"])
    dpy.sync()


def cleanupmon(mon):
    mons.remove(mon)
    mon.barwin.unmap()
    mon.barwin.destroy()


def clientmessage(e):
    c = wintoclient(e.window.id)
    if not c:
        return
    fmt, data = e.data
    if e.client_type == netatom["WMState"]:
        if data[1] == netatom["WMFullscreen"] or data[2] == netatom["WMFullscreen"]:
            setfullscreen(c, (data[0] == 1      # _NET_WM_STATE_ADD
                              or (data[0] == 2  # _NET_WM_STATE_TOGGLE
                                  and not c.isfullscreen)))
        elif {data[1], data[2]} & {netatom["WMMaxVert"], netatom["WMMaxHorz"]}:
            resendwmstate(c)            # pytwm decides: maximized while tiled
    elif e.client_type == netatom["ActiveWindow"]:
        if c is not selmon.sel and not c.isurgent:
            seturgent(c, True)


def configure(c):
    ce = xevent.ConfigureNotify(event=c.win, window=c.win, above_sibling=X.NONE,
                                x=c.x, y=c.y, width=c.w, height=c.h,
                                border_width=c.bw, override=0)
    c.win.send_event(ce, event_mask=X.StructureNotifyMask)


def configurenotify(e):
    global screen_w, screen_h
    # the screen size changed (a monitor was added, removed or resized)
    if e.window.id == root.id:
        dirty = screen_w != e.width or screen_h != e.height
        screen_w = e.width
        screen_h = e.height
        if updategeom() or dirty:
            drw.resize(screen_w, bh)
            updatebars()
            for m in mons:
                for c in m.clients:
                    if c.isfullscreen:
                        resizeclient(c, m.mx, m.my, m.mw, m.mh)
                m.barwin.configure(x=m.wx, y=m.by, width=m.ww, height=bh)
            focus(None)
            arrange(None)
            wallpaper_update()      # the monitors changed


def configurerequest(e):
    c = wintoclient(e.window.id)
    if c:
        if e.value_mask & X.CWBorderWidth:
            c.bw = e.border_width
        elif c.isfloating or not selmon.lt[selmon.sellt].arrange:
            m = c.mon
            if e.value_mask & X.CWX:
                c.oldx = c.x
                c.x = m.mx + e.x
            if e.value_mask & X.CWY:
                c.oldy = c.y
                c.y = m.my + e.y
            if e.value_mask & X.CWWidth:
                c.oldw = c.w
                c.w = e.width
            if e.value_mask & X.CWHeight:
                c.oldh = c.h
                c.h = e.height
            if (c.x + c.w) > m.mx + m.mw and c.isfloating:
                c.x = m.mx + (m.mw // 2 - WIDTH(c) // 2)  # center in x direction
            if (c.y + c.h) > m.my + m.mh and c.isfloating:
                c.y = m.my + (m.mh // 2 - HEIGHT(c) // 2)  # center in y direction
            if (e.value_mask & (X.CWX | X.CWY)) and not (e.value_mask & (X.CWWidth | X.CWHeight)):
                configure(c)
            if ISVISIBLE(c):
                c.win.configure(x=c.x, y=c.y, width=c.w, height=c.h)
        else:
            configure(c)
    else:
        wc = {}
        if e.value_mask & X.CWX: wc["x"] = e.x
        if e.value_mask & X.CWY: wc["y"] = e.y
        if e.value_mask & X.CWWidth: wc["width"] = e.width
        if e.value_mask & X.CWHeight: wc["height"] = e.height
        if e.value_mask & X.CWBorderWidth: wc["border_width"] = e.border_width
        if e.value_mask & X.CWSibling: wc["sibling"] = e.sibling
        if e.value_mask & X.CWStackMode: wc["stack_mode"] = e.stack_mode
        e.window.configure(**wc)
    dpy.sync()


def createmon():
    m = Monitor()
    m.tagset = [1, 1]
    m.mfact = mfact
    m.nmaster = nmaster
    m.showbar = showbar
    m.topbar = topbar
    m.lt = [LAYOUTS[0], LAYOUTS[1 % len(LAYOUTS)]]
    m.ltsymbol = LAYOUTS[0].symbol
    m.taglayouts = TagLayouts(m)
    return m


def destroynotify(e):
    c = wintoclient(e.window.id)
    if c:
        unmanage(c, True)


def detach(c):
    c.mon.clients.remove(c)


def detachstack(c):
    c.mon.stack.remove(c)
    if c is c.mon.sel:
        c.mon.sel = next((t for t in c.mon.stack if ISVISIBLE(t)), None)


def dirtomon(direction):
    i = mons.index(selmon)
    return mons[(i + (1 if direction > 0 else -1)) % len(mons)]


def bartags(m):
    """The numbers (from 0) of the tags shown on m's bar: all of them, or with
    hide_vacant_tags only the ones with windows and the ones being viewed."""
    if not prefns.get("hide_vacant_tags"):
        return range(len(tags))
    occ = 0
    for c in m.clients:
        occ |= c.tags
    seen = occ | m.tagset[m.seltags]
    return [i for i in range(len(tags)) if seen & 1 << i]


def drawbar(m):
    tw = 0
    boxs = drw.fonts[0].h // 9
    boxw = drw.fonts[0].h // 6 + 2
    occ = urg = 0

    if not m.showbar:
        return

    # draw status first so it can be overdrawn by tags later
    if m is selmon:  # status is only drawn on selected monitor
        drw.setscheme(scheme["norm"])
        text = last_error or stext
        tw = TEXTW(uncolored(text)) - lrpad + 2  # 2px right padding
        drw.text(m.ww - tw, 0, tw, bh, 0, "", 0)
        x = m.ww - tw
        for piece, color in color_pieces(text):
            if color and color not in status_schemes:
                status_schemes[color] = drw.scm_create((color,) + tuple(colors["norm"][1:]))
            drw.setscheme(status_schemes[color] if color else scheme["norm"])
            pw = drw.fontset_getwidth(piece)
            drw.text(x, 0, pw, bh, 0, piece, 0)
            x += pw
        drw.setscheme(scheme["norm"])

    for c in m.clients:
        occ |= c.tags
        if c.isurgent:
            urg |= c.tags
    x = 0
    for i in bartags(m):
        t = tags[i]
        w = TEXTW(t)
        drw.setscheme(scheme["sel" if m.tagset[m.seltags] & 1 << i else "norm"])
        drw.text(x, 0, w, bh, lrpad // 2, t, urg & 1 << i)
        if occ & 1 << i:
            drw.rect(x + boxs, boxs, boxw, boxw,
                     m is selmon and selmon.sel and selmon.sel.tags & 1 << i,
                     urg & 1 << i)
        x += w
    w = TEXTW(m.ltsymbol)
    drw.setscheme(scheme["norm"])
    x = drw.text(x, 0, w, bh, lrpad // 2, m.ltsymbol, 0)

    w = m.ww - tw - x
    n = notify_shown() if m is selmon else None
    if w > bh:
        if n:
            notify_draw(n, x, w)
        elif m.sel and prefns.get("bar_title", False):
            drw.setscheme(scheme["sel" if m is selmon else "norm"])
            drw.text(x, 0, w, bh, lrpad // 2, m.sel.name, 0)
            if m.sel.isfloating:
                drw.rect(x + boxs, boxs, boxw, boxw, m.sel.isfixed, 0)
        else:
            drw.setscheme(scheme["norm"])
            drw.rect(x, 0, w, bh, 1, 1)
    dpy.flush()
    drw.map(m.barwin.id, 0, 0, m.ww, bh)


def title_area(m):
    """Where the window title (or a notification) goes on m's bar: (x, width)."""
    x = sum(TEXTW(tags[i]) for i in bartags(m)) + TEXTW(m.ltsymbol)
    tw = TEXTW(uncolored(last_error or stext)) - lrpad + 2 if m is selmon else 0
    return x, m.ww - tw - x


def drawbars():
    for m in mons:
        drawbar(m)


def enternotify(e):
    global selmon
    if (e.mode != X.NotifyNormal or e.detail == X.NotifyInferior) and e.window.id != root.id:
        return
    c = wintoclient(e.window.id)
    m = c.mon if c else wintomon(e.window.id)
    if m is not selmon:
        unfocus(selmon.sel, True)
        selmon = m
    elif not c or c is selmon.sel:
        return
    focus(c)


def expose(e):
    if e.count == 0:
        m = wintomon(e.window.id)
        if m:
            drawbar(m)


def focus(c):
    global selmon
    if not c or not ISVISIBLE(c):
        c = next((t for t in selmon.stack if ISVISIBLE(t)), None)
    if selmon.sel and selmon.sel is not c:
        unfocus(selmon.sel, False)
    if c:
        if c.mon is not selmon:
            selmon = c.mon
        if c.isurgent:
            seturgent(c, False)
        detachstack(c)
        attachstack(c)
        grabbuttons(c, True)
        c.win.change_attributes(border_pixel=scheme["sel"][COL_BORDER].pixel)
        setfocus(c)
    else:
        dpy.set_input_focus(root, X.RevertToPointerRoot, X.CurrentTime)
        root.delete_property(netatom["ActiveWindow"])
    selmon.sel = c
    drawbars()


# there are some broken focus acquiring clients needing extra handling
def focusin(e):
    if selmon.sel and e.window.id != selmon.sel.win.id:
        setfocus(selmon.sel)


def focusmon(arg):
    global selmon
    if len(mons) < 2:
        return
    m = dirtomon(arg)
    if m is selmon:
        return
    unfocus(selmon.sel, False)
    selmon = m
    focus(None)


def focusstack(arg):
    if not selmon.sel or (selmon.sel.isfullscreen and lockfullscreen):
        return
    cl = selmon.clients
    i = cl.index(selmon.sel)
    if arg > 0:
        order = cl[i + 1:] + cl[:i + 1]
    else:
        order = cl[:i][::-1] + cl[i:][::-1]
    c = next((t for t in order if ISVISIBLE(t)), None)
    if c:
        focus(c)
        restack(selmon)


def neighbour(c, direction, tiled_only=False):
    """The nearest visible window in a direction from c: "left", "right", "up"
    or "down". Only windows lined up in that direction count (for left and
    right, ones sharing some height with c; for up and down, some width); the
    nearest edge wins, and between equally near ones the most recently
    focused. Returns (window or None, whether some window lies exactly on c)."""
    horizontal = direction in ("left", "right")
    best, bestgap, stacked = None, None, False
    for t in selmon.stack:                      # most recently focused first
        if t is c or not ISVISIBLE(t) or (tiled_only and t.isfloating):
            continue
        if (t.x, t.y, t.w, t.h) == (c.x, c.y, c.w, c.h):
            stacked = True
            continue
        if horizontal:
            lined_up = t.y < c.y + HEIGHT(c) and c.y < t.y + HEIGHT(t)
            gap = t.x - (c.x + WIDTH(c)) if direction == "right" else c.x - (t.x + WIDTH(t))
            ahead = (t.x + t.w / 2 > c.x + c.w / 2) == (direction == "right")
        else:
            lined_up = t.x < c.x + WIDTH(c) and c.x < t.x + WIDTH(t)
            gap = t.y - (c.y + HEIGHT(c)) if direction == "down" else c.y - (t.y + HEIGHT(t))
            ahead = (t.y + t.h / 2 > c.y + c.h / 2) == (direction == "down")
        if not (lined_up and ahead):
            continue
        gap = max(gap, 0)                       # overlapping (floating) windows count as touching
        if bestgap is None or gap < bestgap:
            best, bestgap = t, gap
    return best, stacked


def focusdir(arg):
    """Focus the nearest window in a direction: "left", "right", "up" or "down"
    (see neighbour). When the windows are stacked on top of each other
    (monocle), it cycles like focusstack."""
    c = selmon.sel
    if not c or (c.isfullscreen and lockfullscreen):
        return
    best, stacked = neighbour(c, arg)
    if best:
        focus(best)
        restack(selmon)
    elif stacked:
        focusstack(+1 if arg in ("right", "down") else -1)


def swapdir(arg):
    """Swap the focused tiled window with the nearest tiled window in a
    direction: "left", "right", "up" or "down". It keeps the focus, and its
    height share (setcfact) goes with it."""
    c = selmon.sel
    if not c or c.isfloating or not selmon.lt[selmon.sellt].arrange:
        return
    t, _ = neighbour(c, arg, tiled_only=True)
    if not t:
        return
    cl = selmon.clients
    i, j = cl.index(c), cl.index(t)
    cl[i], cl[j] = t, c
    arrange(selmon)


def getatomprop(c, prop):
    p = xcall(c.win.get_full_property, prop, Xatom.ATOM)
    return p.value[0] if p and len(p.value) else X.NONE


def getrootptr():
    p = root.query_pointer()
    return p.root_x, p.root_y


def getstate(w):
    p = xcall(w.get_full_property, wmatom["State"], wmatom["State"])
    return p.value[0] if p and len(p.value) else -1


def gettextprop(w, atom):
    """Returns the text (up to 255 bytes), or None if the property is not set."""
    p = xcall(w.get_full_property, atom, X.AnyPropertyType)
    if not p or p.format != 8 or not len(p.value):
        return None
    raw = p.value if isinstance(p.value, bytes) else bytes(p.value)
    return raw[:255].decode("utf-8", "replace")


def grabbuttons(c, focused):
    updatenumlockmask()
    modifiers = (0, X.LockMask, numlockmask, numlockmask | X.LockMask)
    c.win.ungrab_button(X.AnyButton, X.AnyModifier)
    if not focused:
        c.win.grab_button(X.AnyButton, X.AnyModifier, False, BUTTONMASK,
                          X.GrabModeSync, X.GrabModeSync, X.NONE, X.NONE)
    for b_click, b_mask, b_button, _, _ in BUTTONS:
        if b_click == CLIENTWIN:
            for mod in modifiers:
                c.win.grab_button(b_button, b_mask | mod, False, BUTTONMASK,
                                  X.GrabModeAsync, X.GrabModeSync, X.NONE, X.NONE)


def grabkeys():
    updatenumlockmask()
    modifiers = (0, X.LockMask, numlockmask, numlockmask | X.LockMask)
    root.ungrab_key(X.AnyKey, X.AnyModifier)
    start = dpy.display.info.min_keycode
    end = dpy.display.info.max_keycode
    syms = dpy.get_keyboard_mapping(start, end - start + 1)
    if not syms:
        return
    for k in range(start, end + 1):
        for k_mod, k_sym, _, _ in KEYS:
            # skip modifier codes, we do that ourselves
            if k_sym == syms[k - start][0]:
                for mod in modifiers:
                    root.grab_key(k, k_mod | mod, True, X.GrabModeAsync, X.GrabModeAsync)


def incnmaster(arg):
    selmon.nmaster = selmon.taglayouts.nmasters[selmon.taglayouts.curtag] = max(selmon.nmaster + arg, 0)
    arrange(selmon)


key_released = {}   # keycode -> time of its last release


def keypress(e):
    # a held key repeats as release + press at the same moment; functions
    # marked .repeat = False (toggles like mute) act only on the first press
    held = key_released.get(e.detail) == e.time
    keysym = dpy.keycode_to_keysym(e.detail, 0)
    for k_mod, k_sym, k_func, k_arg in KEYS:
        if keysym == k_sym and CLEANMASK(k_mod) == CLEANMASK(e.state) and k_func:
            if held and getattr(k_func, "repeat", True) is False:
                continue
            call(k_func, k_arg)


def keyrelease(e):
    key_released[e.detail] = e.time


def killclient(arg):
    if not selmon.sel:
        return
    if not sendevent(selmon.sel, wmatom["Delete"]):
        dpy.grab_server()
        dpy.set_close_down_mode(X.DestroyAll)
        selmon.sel.win.kill_client(onerror=lambda *a: None)
        dpy.sync()
        dpy.ungrab_server()


def manage(w, wa, geom):
    c = Client(w)
    # geometry
    c.x = c.oldx = geom.x
    c.y = c.oldy = geom.y
    c.w = c.oldw = geom.width
    c.h = c.oldh = geom.height
    c.oldbw = geom.border_width

    updatetitle(c)
    trans = xcall(w.get_wm_transient_for)
    t = wintoclient(trans.id) if trans else None
    if t:
        c.mon = t.mon
        c.tags = t.tags
    else:
        c.mon = selmon
        applyrules(c)
        mask = launched_tags(w) & TAGMASK      # launch(cmd, tag) wins over the rules
        if mask:
            c.tags = mask

    if c.x + WIDTH(c) > c.mon.wx + c.mon.ww:
        c.x = c.mon.wx + c.mon.ww - WIDTH(c)
    if c.y + HEIGHT(c) > c.mon.wy + c.mon.wh:
        c.y = c.mon.wy + c.mon.wh - HEIGHT(c)
    c.x = max(c.x, c.mon.wx)
    c.y = max(c.y, c.mon.wy)
    c.bw = borderpx

    w.configure(border_width=c.bw)
    w.change_attributes(border_pixel=scheme["norm"][COL_BORDER].pixel)
    configure(c)  # propagates border_width, if size doesn't change
    updatewindowtype(c)
    updatesizehints(c)
    updatewmhints(c)
    w.change_attributes(event_mask=X.EnterWindowMask | X.FocusChangeMask
                        | X.PropertyChangeMask | X.StructureNotifyMask)
    grabbuttons(c, False)
    if not c.isfloating:
        c.isfloating = c.oldstate = trans is not None or c.isfixed
    if c.isfloating:
        w.raise_window()
    attach(c)
    attachstack(c)
    root.change_property(netatom["ClientList"], Xatom.WINDOW, 32, [w.id], X.PropModeAppend)
    w.configure(x=c.x + 2 * screen_w, y=c.y, width=c.w, height=c.h)  # some windows require this
    setclientstate(c, 1)  # NormalState
    if c.mon is selmon:
        unfocus(selmon.sel, False)
    c.mon.sel = c
    arrange(c.mon)
    w.map()
    focus(None)
    return c


def mappingnotify(e):
    dpy.refresh_keyboard_mapping(e)
    if e.request == X.MappingKeyboard:
        grabkeys()


def maprequest(e):
    wa = xcall(e.window.get_attributes)
    if not wa or wa.override_redirect:
        return
    if not wintoclient(e.window.id):
        geom = xcall(e.window.get_geometry)
        if geom:
            manage(e.window, wa, geom)


def monocle(m):
    n = sum(1 for c in m.clients if ISVISIBLE(c))
    if n > 0:  # override layout symbol
        m.ltsymbol = "[%d]" % n
    for c in tiled(m):
        resize(c, m.wx, m.wy, m.ww - 2 * c.bw, m.wh - 2 * c.bw, False)


_motion_mon = None

def motionnotify(e):
    global selmon, _motion_mon
    if e.window.id != root.id:
        return
    m = recttomon(e.root_x, e.root_y, 1, 1)
    if m is not _motion_mon and _motion_mon:
        unfocus(selmon.sel, True)
        selmon = m
        focus(None)
    _motion_mon = m


def grabloop(c, on_motion):
    """The XMaskEvent loop shared by movemouse and resizemouse."""
    lasttime = 0
    while True:
        ev = dpy.next_event()
        if ev.type in (X.ConfigureRequest, X.Expose, X.MapRequest):
            handler[ev.type](ev)
        elif ev.type == X.MotionNotify:
            if ev.time - lasttime <= 1000 // 60:
                continue
            lasttime = ev.time
            on_motion(ev)
        elif ev.type == X.ButtonRelease:
            return
        elif ev.type != X.ButtonPress:
            queued.append(ev)   # handled after the drag


def movemouse(arg):
    global selmon
    c = selmon.sel
    if not c:
        return
    if c.isfullscreen:  # no support moving fullscreen windows by mouse
        return
    restack(selmon)
    ocx, ocy = c.x, c.y
    if root.grab_pointer(False, MOUSEMASK, X.GrabModeAsync, X.GrabModeAsync,
                         X.NONE, cursor[CUR_MOVE], X.CurrentTime) != X.GrabSuccess:
        return
    x, y = getrootptr()

    def motion(ev):
        nx = ocx + (ev.event_x - x)
        ny = ocy + (ev.event_y - y)
        if abs(selmon.wx - nx) < snap:
            nx = selmon.wx
        elif abs((selmon.wx + selmon.ww) - (nx + WIDTH(c))) < snap:
            nx = selmon.wx + selmon.ww - WIDTH(c)
        if abs(selmon.wy - ny) < snap:
            ny = selmon.wy
        elif abs((selmon.wy + selmon.wh) - (ny + HEIGHT(c))) < snap:
            ny = selmon.wy + selmon.wh - HEIGHT(c)
        if (not c.isfloating and selmon.lt[selmon.sellt].arrange
                and (abs(nx - c.x) > snap or abs(ny - c.y) > snap)):
            togglefloating(None)
        if not selmon.lt[selmon.sellt].arrange or c.isfloating:
            resize(c, nx, ny, c.w, c.h, True)

    grabloop(c, motion)
    dpy.ungrab_pointer(X.CurrentTime)
    m = recttomon(c.x, c.y, c.w, c.h)
    if m is not selmon:
        sendmon(c, m)
        selmon = m
        focus(None)


def nexttiled(clients, start=0):
    for c in clients[start:]:
        if not c.isfloating and ISVISIBLE(c):
            return c
    return None


def tiled(m):
    """All tiled, visible clients of m in order."""
    return [c for c in m.clients if not c.isfloating and ISVISIBLE(c)]


def pop(c):
    detach(c)
    attach(c)
    focus(c)
    arrange(c.mon)


def propertynotify(e):
    if e.window.id == root.id and e.atom == Xatom.WM_NAME:
        updatestatus()
    elif e.state == X.PropertyDelete:
        return  # ignore
    else:
        c = wintoclient(e.window.id)
        if not c:
            return
        if e.atom == Xatom.WM_TRANSIENT_FOR:
            if not c.isfloating:
                trans = xcall(c.win.get_wm_transient_for)
                if trans and wintoclient(trans.id):
                    c.isfloating = True
                    arrange(c.mon)
        elif e.atom == Xatom.WM_NORMAL_HINTS:
            c.hintsvalid = False
        elif e.atom == Xatom.WM_HINTS:
            updatewmhints(c)
            drawbars()
        if e.atom == Xatom.WM_NAME or e.atom == netatom["WMName"]:
            updatetitle(c)
            if c is c.mon.sel:
                drawbar(c.mon)
        if e.atom == netatom["WMWindowType"]:
            updatewindowtype(c)


def quit(arg):
    global running
    running = False


def recttomon(x, y, w, h):
    r = selmon
    area = 0
    for m in mons:
        a = INTERSECT(x, y, w, h, m)
        if a > area:
            area = a
            r = m
    return r


def resize(c, x, y, w, h, interact):
    changed, x, y, w, h = applysizehints(c, x, y, w, h, interact)
    if changed:
        resizeclient(c, x, y, w, h)


def resizeclient(c, x, y, w, h):
    c.oldx, c.x = c.x, x
    c.oldy, c.y = c.y, y
    c.oldw, c.w = c.w, w
    c.oldh, c.h = c.h, h
    c.win.configure(x=x, y=y, width=w, height=h, border_width=c.bw)
    configure(c)
    dpy.sync()


def resizemouse(arg):
    global selmon
    c = selmon.sel
    if not c:
        return
    if c.isfullscreen:  # no support resizing fullscreen windows by mouse
        return
    restack(selmon)
    ocx, ocy = c.x, c.y
    if root.grab_pointer(False, MOUSEMASK, X.GrabModeAsync, X.GrabModeAsync,
                         X.NONE, cursor[CUR_RESIZE], X.CurrentTime) != X.GrabSuccess:
        return
    c.win.warp_pointer(c.w + c.bw - 1, c.h + c.bw - 1)

    def motion(ev):
        nw = max(ev.event_x - ocx - 2 * c.bw + 1, 1)
        nh = max(ev.event_y - ocy - 2 * c.bw + 1, 1)
        if (c.mon.wx + nw >= selmon.wx and c.mon.wx + nw <= selmon.wx + selmon.ww
                and c.mon.wy + nh >= selmon.wy and c.mon.wy + nh <= selmon.wy + selmon.wh):
            if (not c.isfloating and selmon.lt[selmon.sellt].arrange
                    and (abs(nw - c.w) > snap or abs(nh - c.h) > snap)):
                togglefloating(None)
        if not selmon.lt[selmon.sellt].arrange or c.isfloating:
            resize(c, c.x, c.y, nw, nh, True)

    grabloop(c, motion)
    c.win.warp_pointer(c.w + c.bw - 1, c.h + c.bw - 1)
    dpy.ungrab_pointer(X.CurrentTime)
    dropenters()
    m = recttomon(c.x, c.y, c.w, c.h)
    if m is not selmon:
        sendmon(c, m)
        selmon = m
        focus(None)


def dropenters():
    """while (XCheckMaskEvent(dpy, EnterWindowMask, &ev)); - drop queued EnterNotify events."""
    dpy.sync()
    queued[:] = [ev for ev in queued if ev.type != X.EnterNotify]
    while dpy.pending_events():
        ev = dpy.next_event()
        if ev.type != X.EnterNotify:
            queued.append(ev)


def restack(m):
    drawbar(m)
    if not m.sel:
        return
    if m.sel.isfloating or not m.lt[m.sellt].arrange:
        m.sel.win.raise_window()
    if m.lt[m.sellt].arrange:
        sibling = m.barwin
        for c in m.stack:
            if not c.isfloating and ISVISIBLE(c):
                c.win.configure(sibling=sibling, stack_mode=X.Below)
                sibling = c.win
    dropenters()


def run():
    """Main event loop. Also checks every half second whether this file was saved."""
    global last_error
    dpy.sync()
    fd = dpy.fileno()
    mtime = file_mtime()
    lastcheck = time.monotonic()
    while running:
        while running and (queued or dpy.pending_events()):
            ev = queued.pop(0) if queued else dpy.next_event()
            h = handler.get(ev.type)
            if h:
                try:
                    h(ev)
                except Exception:
                    traceback.print_exc()
                    last_error = "pytwm error: %s" % sys.exc_info()[1]
                    drawbars()
        if not running:
            break
        r, _, _ = select.select([fd, _wake_r], [], [], 0.05 if notify_flashing() else 0.5)
        if _wake_r in r:
            os.read(_wake_r, 4096)
        runmainq()
        notify_tick()
        if autoreload and time.monotonic() - lastcheck >= 0.5:
            lastcheck = time.monotonic()
            m = file_mtime()
            if m != mtime:
                mtime = m
                reload(None)
        if not running:
            break


def scan():
    tree = xcall(root.query_tree)
    if not tree:
        return
    wins = tree.children
    for w in wins:
        wa = xcall(w.get_attributes)
        if not wa or wa.override_redirect or xcall(w.get_wm_transient_for):
            continue
        if wa.map_state == X.IsViewable or getstate(w) == 3:  # IconicState
            geom = xcall(w.get_geometry)
            if geom:
                manage(w, wa, geom)
    for w in wins:  # now the transients
        wa = xcall(w.get_attributes)
        if not wa:
            continue
        if xcall(w.get_wm_transient_for) and (wa.map_state == X.IsViewable or getstate(w) == 3):
            geom = xcall(w.get_geometry)
            if geom:
                manage(w, wa, geom)


def sendmon(c, m):
    if c.mon is m:
        return
    unfocus(c, True)
    detach(c)
    detachstack(c)
    c.mon = m
    c.tags = m.tagset[m.seltags]  # assign tags of target monitor
    attach(c)
    attachstack(c)
    focus(None)
    arrange(None)


def setclientstate(c, state):
    c.win.change_property(wmatom["State"], wmatom["State"], 32, [state, X.NONE])


def sendevent(c, proto):
    protocols = xcall(c.win.get_wm_protocols) or []
    exists = proto in protocols
    if exists:
        ev = xevent.ClientMessage(window=c.win, client_type=wmatom["Protocols"],
                                  data=(32, [proto, X.CurrentTime, 0, 0, 0]))
        c.win.send_event(ev, event_mask=X.NoEventMask)
    return exists


def setfocus(c):
    if not c.neverfocus:
        dpy.set_input_focus(c.win, X.RevertToPointerRoot, X.CurrentTime)
        root.change_property(netatom["ActiveWindow"], Xatom.WINDOW, 32, [c.win.id])
    sendevent(c, wmatom["TakeFocus"])


def setfullscreen(c, fullscreen):
    if fullscreen and not c.isfullscreen:
        c.isfullscreen = True
        updatewmstate(c)
        c.oldstate = c.isfloating
        c.oldbw = c.bw
        c.bw = 0
        c.isfloating = True
        resizeclient(c, c.mon.mx, c.mon.my, c.mon.mw, c.mon.mh)
        c.win.raise_window()
    elif not fullscreen and c.isfullscreen:
        c.isfullscreen = False
        c.isfloating = c.oldstate
        updatewmstate(c)
        c.bw = c.oldbw
        c.x = c.oldx
        c.y = c.oldy
        c.w = c.oldw
        c.h = c.oldh
        resizeclient(c, c.x, c.y, c.w, c.h)
        arrange(c.mon)


def setlayout(arg):
    """arg is an index into layouts(), or None to switch to the previous layout."""
    lt = LAYOUTS[arg] if arg is not None else None
    if lt is None or lt is not selmon.lt[selmon.sellt]:
        selmon.taglayouts.sellts[selmon.taglayouts.curtag] ^= 1
        selmon.sellt = selmon.taglayouts.sellts[selmon.taglayouts.curtag]
    if lt is not None:
        selmon.lt[selmon.sellt] = selmon.taglayouts.ltidxs[selmon.taglayouts.curtag][selmon.sellt] = lt
    selmon.ltsymbol = selmon.lt[selmon.sellt].symbol
    if selmon.sel:
        arrange(selmon)
    else:
        drawbar(selmon)


# arg > 1.0 will set mfact absolutely
def setmfact(arg):
    if arg is None or not selmon.lt[selmon.sellt].arrange:
        return
    f = arg + selmon.mfact if arg < 1.0 else arg - 1.0
    if f < 0.05 or f > 0.95:
        return
    selmon.mfact = selmon.taglayouts.mfacts[selmon.taglayouts.curtag] = f
    arrange(selmon)


def setcfact(arg):
    """Make the focused window taller (+0.05) or shorter (-0.05) than the
    others in its column; 0 makes them even again."""
    c = selmon.sel
    if arg is None or not c or c.isfloating or not selmon.lt[selmon.sellt].arrange:
        return
    f = 1.0 if arg == 0 else c.cfact + arg
    if f < 0.25 or f > 4.0:
        return
    c.cfact = f
    arrange(selmon)


def setup():
    global screen, screen_w, screen_h, root, drw, lrpad, bh, wmcheckwin, LAYOUTS, KEYS, BUTTONS, RULES
    # do not transform children into zombies when they terminate
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    # clean up any zombies (inherited from .xinitrc etc) immediately
    try:
        while os.waitpid(-1, os.WNOHANG)[0] > 0:
            pass
    except ChildProcessError:
        pass

    # turn the configuration into the structures the code below uses
    global TAGMASK
    if len(tags) > 31:
        raise ValueError("tags must fit into a 31 bit mask")
    TAGMASK = (1 << len(tags)) - 1
    LAYOUTS = [Layout(s, f) for s, f in layouts()]
    KEYS = read_keys()
    BUTTONS = read_buttons()
    RULES = read_rules()

    # init screen
    screen = dpy.screen()
    screen_w = screen.width_in_pixels
    screen_h = screen.height_in_pixels
    root = screen.root
    drw = Drw(screen_w, screen_h)
    global bar_fonts, bold_fonts
    # the bold version of the bar's fonts too, for the summary of notifications
    bold_fonts = drw.fontset_create([f if f.startswith("-") else f + ":bold" for f in fonts])
    bar_fonts = drw.fontset_create(fonts)
    if not bar_fonts:
        die("no fonts could be loaded.")
    bold_fonts = bold_fonts or bar_fonts
    lrpad = drw.fonts[0].h
    bh = drw.fonts[0].h + 2
    updategeom()
    # init atoms
    utf8string = dpy.intern_atom("UTF8_STRING")
    wmatom["Protocols"] = dpy.intern_atom("WM_PROTOCOLS")
    wmatom["Delete"] = dpy.intern_atom("WM_DELETE_WINDOW")
    wmatom["State"] = dpy.intern_atom("WM_STATE")
    wmatom["TakeFocus"] = dpy.intern_atom("WM_TAKE_FOCUS")
    netatom["ActiveWindow"] = dpy.intern_atom("_NET_ACTIVE_WINDOW")
    netatom["Supported"] = dpy.intern_atom("_NET_SUPPORTED")
    netatom["WMName"] = dpy.intern_atom("_NET_WM_NAME")
    netatom["WMState"] = dpy.intern_atom("_NET_WM_STATE")
    netatom["WMCheck"] = dpy.intern_atom("_NET_SUPPORTING_WM_CHECK")
    netatom["WMFullscreen"] = dpy.intern_atom("_NET_WM_STATE_FULLSCREEN")
    netatom["WMMaxVert"] = dpy.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")
    netatom["WMMaxHorz"] = dpy.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
    netatom["WMWindowType"] = dpy.intern_atom("_NET_WM_WINDOW_TYPE")
    netatom["WMWindowTypeDialog"] = dpy.intern_atom("_NET_WM_WINDOW_TYPE_DIALOG")
    netatom["ClientList"] = dpy.intern_atom("_NET_CLIENT_LIST")
    # init cursors
    font = dpy.open_font("cursor")
    for i, shape in ((CUR_NORMAL, Xcursorfont.left_ptr), (CUR_RESIZE, Xcursorfont.sizing),
                     (CUR_MOVE, Xcursorfont.fleur)):
        cursor[i] = font.create_glyph_cursor(font, shape, shape + 1,
                                             (0, 0, 0), (65535, 65535, 65535))
    # init appearance
    for name, cols in colors.items():
        scheme[name] = drw.scm_create(cols)
    if "urgent" not in scheme:
        scheme["urgent"] = drw.scm_create(URGENT_COLORS)
    # window borders fully opaque: on windows with an alpha channel (32-bit,
    # e.g. a terminal with a see-through background) the top byte of the
    # border color is its opacity, and 0 would make the border invisible
    for s in scheme.values():
        if len(s) > COL_BORDER:
            s[COL_BORDER].pixel |= 0xff000000
    # init bars
    updatebars()
    updatestatus()
    # supporting window for NetWMCheck
    wmcheckwin = root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
    wmcheckwin.change_property(netatom["WMCheck"], Xatom.WINDOW, 32, [wmcheckwin.id])
    wmcheckwin.change_property(netatom["WMName"], utf8string, 8, b"pytwm")
    root.change_property(netatom["WMCheck"], Xatom.WINDOW, 32, [wmcheckwin.id])
    # EWMH support per view
    root.change_property(netatom["Supported"], Xatom.ATOM, 32, list(netatom.values()))
    root.delete_property(netatom["ClientList"])
    # select events
    root.change_attributes(cursor=cursor[CUR_NORMAL],
                           event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask
                           | X.ButtonPressMask | X.PointerMotionMask | X.EnterWindowMask
                           | X.LeaveWindowMask | X.StructureNotifyMask | X.PropertyChangeMask)
    grabkeys()
    focus(None)


def seturgent(c, urg):
    c.isurgent = urg
    wmh = xcall(c.win.get_wm_hints)
    if not wmh:
        return
    setwmflags(c, wmh.flags | Xutil_UrgencyHint if urg else wmh.flags & ~Xutil_UrgencyHint, wmh)


def setwmflags(c, flags, wmh):
    """XSetWMHints with only the flags changed."""
    hints = dict(wmh._data)
    hints["flags"] = flags
    c.win.set_wm_hints(hints, onerror=lambda *a: None)


def showhide(m):
    # show clients top down
    for c in m.stack:
        if ISVISIBLE(c):
            c.win.configure(x=c.x, y=c.y)
            if (not c.mon.lt[c.mon.sellt].arrange or c.isfloating) and not c.isfullscreen:
                resize(c, c.x, c.y, c.w, c.h, False)
    # hide clients bottom up
    for c in reversed(m.stack):
        if not ISVISIBLE(c):
            c.win.configure(x=WIDTH(c) * -2, y=c.y)


def child_env(**more):
    """The environment for programs pytwm starts: pytwm's own, without its
    PYTWM_* bookkeeping (picom's pid, the wallpaper drawn, reload paths)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTWM_")}
    env.update(more)
    return env


def spawn(arg):
    """Start a command (a list of arguments) in its own session. Returns its
    process id, or None if it couldn't start."""
    try:
        devnull = [(os.POSIX_SPAWN_OPEN, fd, "/dev/null", os.O_RDWR, 0) for fd in (0, 1, 2)]
        return os.posix_spawnp(arg[0], list(arg), child_env(), setsid=True,
                               setsigdef=(signal.SIGCHLD,), file_actions=devnull)   # their output isn't pytwm's log
    except OSError as ex:
        sys.stderr.write("pytwm: execvp '%s' failed: %s\n" % (arg[0], ex))
        return None


def tag(arg):
    if selmon.sel and arg & TAGMASK:
        selmon.sel.tags = arg & TAGMASK
        focus(None)
        arrange(selmon)


def tagmon(arg):
    if not selmon.sel or len(mons) < 2:
        return
    sendmon(selmon.sel, dirtomon(arg))


def tile(m):
    cl = tiled(m)
    n = len(cl)
    if n == 0:
        return
    g = prefns.get("gaps", 0)                   # around the edge and between windows
    x0, y0, w0, h0 = m.wx + g, m.wy + g, m.ww - 2 * g, m.wh - 2 * g
    nm = min(n, m.nmaster)
    if n > m.nmaster:
        mw = int((w0 - g) * m.mfact) if m.nmaster else 0
        sx = x0 + mw + g if m.nmaster else x0
    else:
        mw = sx = w0
    stack_w = x0 + w0 - sx                      # the stack column's width
    my = ty = 0
    # each window's height is its share (cfact) of what is left in its column
    mfacts = sum(c.cfact for c in cl[:m.nmaster])
    sfacts = sum(c.cfact for c in cl[m.nmaster:])
    for i, c in enumerate(cl):
        if i < m.nmaster:
            left = h0 - my - g * (nm - i - 1)   # what is left, less the gaps still to come
            h = int(left * c.cfact / mfacts)
            mfacts -= c.cfact
            resize(c, x0, y0 + my, mw - (2 * c.bw), h - (2 * c.bw), False)
            if my + HEIGHT(c) < h0:
                my += HEIGHT(c) + g
        else:
            left = h0 - ty - g * (n - i - 1)
            h = int(left * c.cfact / sfacts)
            sfacts -= c.cfact
            resize(c, sx, y0 + ty, stack_w - (2 * c.bw), h - (2 * c.bw), False)
            if ty + HEIGHT(c) < h0:
                ty += HEIGHT(c) + g


def togglebar(arg):
    selmon.showbar = selmon.taglayouts.showbars[selmon.taglayouts.curtag] = not selmon.showbar
    updatebarpos(selmon)
    selmon.barwin.configure(x=selmon.wx, y=selmon.by, width=selmon.ww, height=bh)
    arrange(selmon)


def togglefloating(arg):
    c = selmon.sel
    if not c:
        return
    if c.isfullscreen:  # no support for fullscreen windows
        return
    c.isfloating = not c.isfloating or c.isfixed
    if c.isfloating:
        resize(c, c.x, c.y, c.w, c.h, False)
    arrange(selmon)


def toggletag(arg):
    if not selmon.sel:
        return
    newtags = selmon.sel.tags ^ (arg & TAGMASK)
    if newtags:
        selmon.sel.tags = newtags
        focus(None)
        arrange(selmon)


def applytaglayouts():
    """apply settings for this view (shared tail of view and toggleview)"""
    pt = selmon.taglayouts
    selmon.nmaster = pt.nmasters[pt.curtag]
    selmon.mfact = pt.mfacts[pt.curtag]
    selmon.sellt = pt.sellts[pt.curtag]
    selmon.lt[selmon.sellt] = pt.ltidxs[pt.curtag][selmon.sellt]
    selmon.lt[selmon.sellt ^ 1] = pt.ltidxs[pt.curtag][selmon.sellt ^ 1]
    if selmon.showbar != pt.showbars[pt.curtag]:
        togglebar(None)
    focus(None)
    arrange(selmon)


def toggleview(arg):
    newtagset = selmon.tagset[selmon.seltags] ^ (arg & TAGMASK)
    if newtagset:
        selmon.tagset[selmon.seltags] = newtagset
        pt = selmon.taglayouts
        if newtagset == ~0:
            pt.prevtag = pt.curtag
            pt.curtag = 0
        # test if the user did not select the same tag
        if not (newtagset & 1 << ((pt.curtag - 1) & 31)):  # C shifts by 31 when curtag is 0
            pt.prevtag = pt.curtag
            i = 0
            while not (newtagset & 1 << i):
                i += 1
            pt.curtag = i + 1
        applytaglayouts()


def unfocus(c, setfocus):
    if not c:
        return
    grabbuttons(c, False)
    c.win.change_attributes(border_pixel=scheme["norm"][COL_BORDER].pixel)
    if setfocus:
        dpy.set_input_focus(root, X.RevertToPointerRoot, X.CurrentTime)
        root.delete_property(netatom["ActiveWindow"])


def unmanage(c, destroyed):
    m = c.mon
    detach(c)
    detachstack(c)
    if not destroyed:
        dpy.grab_server()  # avoid race conditions
        quiet = lambda *a: None
        c.win.change_attributes(event_mask=X.NoEventMask, onerror=quiet)
        c.win.configure(border_width=c.oldbw, onerror=quiet)  # restore border
        c.win.ungrab_button(X.AnyButton, X.AnyModifier, onerror=quiet)
        c.win.change_property(wmatom["State"], wmatom["State"], 32, [0, X.NONE], onerror=quiet)
        dpy.sync()
        dpy.ungrab_server()
    focus(None)
    updateclientlist()
    arrange(m)


def unmapnotify(e):
    c = wintoclient(e.window.id)
    if c:
        if e.send_event:
            setclientstate(c, 0)  # WithdrawnState
        else:
            unmanage(c, False)


def updatebars():
    for m in mons:
        if m.barwin:
            continue
        m.barwin = root.create_window(m.wx, m.by, m.ww, bh, 0, screen.root_depth,
                                      X.CopyFromParent, X.CopyFromParent,
                                      override_redirect=1, background_pixmap=X.ParentRelative,
                                      event_mask=X.ButtonPressMask | X.ExposureMask)
        m.barwin.change_attributes(cursor=cursor[CUR_NORMAL])
        m.barwin.map()
        m.barwin.raise_window()
        m.barwin.set_wm_class("pytwm", "pytwm")
    dpy.sync()


def updatebarpos(m):
    m.wy = m.my
    m.wh = m.mh
    if m.showbar:
        m.wh -= bh
        m.by = m.wy if m.topbar else m.wy + m.wh
        m.wy = m.wy + bh if m.topbar else m.wy
    else:
        m.by = -bh


def updateclientlist():
    root.delete_property(netatom["ClientList"])
    for m in mons:
        for c in m.clients:
            root.change_property(netatom["ClientList"], Xatom.WINDOW, 32,
                                 [c.win.id], X.PropModeAppend)


def updategeom():
    global selmon
    dirty = False
    if dpy.has_extension("XINERAMA") and dpy.xinerama_is_active():
        n = len(mons)
        # only consider unique geometries as separate screens
        unique = []
        for s in dpy.xinerama_query_screens().screens:
            g = (s.x, s.y, s.width, s.height)
            if g not in unique:
                unique.append(g)
        nn = len(unique)
        # new monitors if nn > n
        for i in range(n, nn):
            mons.append(createmon())
        for i, m in enumerate(mons[:nn]):
            x, y, w, h = unique[i]
            if i >= n or x != m.mx or y != m.my or w != m.mw or h != m.mh:
                dirty = True
                m.num = i
                m.mx = m.wx = x
                m.my = m.wy = y
                m.mw = m.ww = w
                m.mh = m.wh = h
                updatebarpos(m)
        # removed monitors if n > nn
        for i in range(nn, n):
            m = mons[-1]
            while m.clients:
                dirty = True
                c = m.clients.pop(0)
                detachstack(c)
                c.mon = mons[0]
                attach(c)
                attachstack(c)
            if m is selmon:
                selmon = mons[0]
            cleanupmon(m)
    else:  # default monitor setup
        if not mons:
            mons.append(createmon())
        if mons[0].mw != screen_w or mons[0].mh != screen_h:
            dirty = True
            mons[0].mw = mons[0].ww = screen_w
            mons[0].mh = mons[0].wh = screen_h
            updatebarpos(mons[0])
    if dirty:
        selmon = mons[0]
        selmon = wintomon(root.id)
    return dirty


def updatenumlockmask():
    global numlockmask
    numlockmask = 0
    numlock = dpy.keysym_to_keycode(XK.XK_Num_Lock)
    for i, codes in enumerate(dpy.get_modifier_mapping()):
        if numlock in codes:
            numlockmask = 1 << i


def updatesizehints(c):
    size = xcall(c.win.get_wm_normal_hints)
    flags = size.flags if size else 0x8  # PSize: size is unset, ensure no flags are used
    PMinSize, PMaxSize, PResizeInc, PAspect, PBaseSize = 16, 32, 64, 128, 256
    if flags & PBaseSize:
        c.basew, c.baseh = size.base_width, size.base_height
    elif flags & PMinSize:
        c.basew, c.baseh = size.min_width, size.min_height
    else:
        c.basew = c.baseh = 0
    if flags & PResizeInc:
        c.incw, c.inch = size.width_inc, size.height_inc
    else:
        c.incw = c.inch = 0
    if flags & PMaxSize:
        c.maxw, c.maxh = size.max_width, size.max_height
    else:
        c.maxw = c.maxh = 0
    if flags & PMinSize:
        c.minw, c.minh = size.min_width, size.min_height
    elif flags & PBaseSize:
        c.minw, c.minh = size.base_width, size.base_height
    else:
        c.minw = c.minh = 0
    if flags & PAspect and size.min_aspect["num"] and size.max_aspect["denum"]:
        c.mina = size.min_aspect["denum"] / size.min_aspect["num"]
        c.maxa = size.max_aspect["num"] / size.max_aspect["denum"]
    else:
        c.maxa = c.mina = 0.0
    c.isfixed = bool(c.maxw and c.maxh and c.maxw == c.minw and c.maxh == c.minh)
    c.hintsvalid = True


COLOR_MARK = "\x1b"    # colored() puts it around the color and text


def color_pieces(text):
    """The status text as (text, color) pieces; color is None for the bar's own."""
    pieces = text.split(COLOR_MARK)
    return [(p[7:], p[:7]) if i % 2 else (p, None) for i, p in enumerate(pieces) if p[7 * (i % 2):]]


def uncolored(text):
    return "".join(t for t, _ in color_pieces(text))


status_schemes = {}     # color -> the "norm" scheme with that text color


def updatestatus():
    """The status text: from status() in pref.py if it has one, otherwise the
    root window's name, which status scripts set."""
    global stext
    stext = status_text if status_text is not None else gettextprop(root, Xatom.WM_NAME)
    if stext is None:
        stext = "pytwm-" + VERSION
    drawbar(selmon)


status_text = None


def setstatus(text):
    global status_text
    status_text = text
    updatestatus()


status_now = threading.Event()     # set it to update the status right away


def update_status():
    """Update the status now, instead of at the next status_interval."""
    status_now.set()


def statusloop():
    """Calls status() from pref.py every status_interval seconds, in the background."""
    func = prefns["status"]
    while True:
        status_now.clear()
        try:
            text = str(func())
        except Exception as ex:
            traceback.print_exc()
            text = "status(): %s: %s" % (type(ex).__name__, ex)
        call_main(setstatus, text)
        status_now.wait(prefns.get("status_interval", 1))


def updatetitle(c):
    c.name = gettextprop(c.win, netatom["WMName"]) or gettextprop(c.win, Xatom.WM_NAME) or ""
    if c.name == "":  # hack to mark broken clients
        c.name = broken


def updatewindowtype(c):
    state = getatomprop(c, netatom["WMState"])
    wtype = getatomprop(c, netatom["WMWindowType"])
    if state == netatom["WMFullscreen"]:
        setfullscreen(c, True)
    if wtype == netatom["WMWindowTypeDialog"]:
        c.isfloating = True


Xutil_UrgencyHint = 1 << 8
Xutil_InputHint = 1 << 0

def updatewmhints(c):
    wmh = xcall(c.win.get_wm_hints)
    if wmh:
        if c is selmon.sel and wmh.flags & Xutil_UrgencyHint:
            setwmflags(c, wmh.flags & ~Xutil_UrgencyHint, wmh)
        else:
            c.isurgent = bool(wmh.flags & Xutil_UrgencyHint)
        if wmh.flags & Xutil_InputHint:
            c.neverfocus = not wmh.input
        else:
            c.neverfocus = False


def view(arg):
    """arg is a tag mask, or None to go back to the previous view."""
    ui = arg if arg is not None else 0
    if (ui & TAGMASK) == selmon.tagset[selmon.seltags]:
        return
    selmon.seltags ^= 1  # toggle sel tagset
    pt = selmon.taglayouts
    if ui & TAGMASK:
        selmon.tagset[selmon.seltags] = ui & TAGMASK
        pt.prevtag = pt.curtag
        if ui == ~0:
            pt.curtag = 0
        else:
            i = 0
            while not (ui & 1 << i):
                i += 1
            pt.curtag = i + 1
    else:
        pt.prevtag, pt.curtag = pt.curtag, pt.prevtag
    applytaglayouts()


def wintoclient(w):
    for m in mons:
        for c in m.clients:
            if c.win.id == w:
                return c
    return None


def wintomon(w):
    if w == root.id:
        x, y = getrootptr()
        return recttomon(x, y, 1, 1)
    for m in mons:
        if m.barwin and w == m.barwin.id:
            return m
    c = wintoclient(w)
    if c:
        return c.mon
    return selmon


# There's no way to check accesses to destroyed windows, thus those cases are
# ignored (especially on UnmapNotify's). Other errors are printed.
def open_display():
    """Open the display. python-xlib's RandR and Damage code files its errors under the core
    codes 1 and 2, so a plain BadValue came back as a broken BadRRModeError; put the core ones back."""
    d = display.Display()
    d.display.error_classes.update(error.xerror_class)
    return d


def xerror(err, request):
    code = err.__class__
    req = err.major_opcode
    ignored = (code is error.BadWindow
               or (req == 42 and code is error.BadMatch)       # X_SetInputFocus
               or (req == 74 and code is error.BadDrawable)    # X_PolyText8
               or (req == 70 and code is error.BadDrawable)    # X_PolyFillRectangle
               or (req == 66 and code is error.BadDrawable)    # X_PolySegment
               or (req == 12 and code is error.BadMatch)       # X_ConfigureWindow
               or (req == 28 and code is error.BadAccess)      # X_GrabButton
               or (req == 33 and code is error.BadAccess)      # X_GrabKey
               or (req == 62 and code is error.BadDrawable))   # X_CopyArea
    if not ignored:
        sys.stderr.write("pytwm: error: request code=%s, error=%s\n" % (req, code.__name__))


def zoom(arg):
    c = selmon.sel
    if not selmon.lt[selmon.sellt].arrange or not c or c.isfloating:
        return
    if c is nexttiled(selmon.clients):
        c = nexttiled(selmon.clients, selmon.clients.index(c) + 1)
        if not c:
            return
    pop(c)


# ============================================================================
#  The menu
#
#  A one-line menu at the top of the screen: type to filter the items, pick
#  one with Enter. The launcher (Super+x) lists the installed apps. While
#  the menu is open it owns the keyboard; every other X event still goes to
#  the window manager handlers, so windows keep opening and closing normally.
# ============================================================================

class Menu:
    """An open menu. curr (first item on screen), sel (selected), next and
    prev (first items of the next and previous page) are indices into `matches`."""

    def __init__(self, items):
        self.items = items
        self.out = set()             # items already run with Ctrl+Return
        self.text = ""
        self.cursor = 0              # in characters
        self.matches = []
        self.curr = self.sel = self.next = self.prev = None
        self.running = True
        self.prompt = menu_prompt
        self.lines = menu_lines
        self.nocase = menu_case_insensitive
        self.onchoose = None         # called with each chosen line


mdrw = None          # the menu's own Drw: its font can differ from the bar's
mscheme = {}
_appcache = (None, {})


def desktop_apps():
    """The installed applications, from their .desktop files: {name: command}.
    Hidden entries (NoDisplay, Hidden, a missing TryExec) are left out; a file
    in ~/.local/share/applications wins over one of the same name elsewhere.
    Cached until an applications folder changes."""
    global _appcache
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [os.path.join(d, "applications") for d in [data_home, *data_dirs.split(":")] if d]
    stamp = []
    for d in dirs:
        for folder, subs, _ in os.walk(d):
            stamp.append((folder, os.stat(folder).st_mtime))
    if _appcache[0] == stamp:
        return _appcache[1]
    files = {}                       # desktop file id: path, the first one found wins
    for d in dirs:
        for folder, _, names in os.walk(d):
            for n in names:
                if n.endswith(".desktop"):
                    rel = os.path.relpath(os.path.join(folder, n), d)
                    files.setdefault(rel.replace("/", "-"), os.path.join(folder, n))
    apps = {}
    for path in files.values():
        entry = desktop_entry(path)
        if (entry.get("Type") != "Application" or "Exec" not in entry
                or entry.get("NoDisplay") == "true" or entry.get("Hidden") == "true"):
            continue
            # TryExec names a program that must be installed for the entry to show
        if "TryExec" in entry and not shutil.which(os.path.expanduser(entry["TryExec"])):
            continue
        cmd = desktop_exec(entry["Exec"])
        if not cmd or not shutil.which(cmd[0]):
            continue
        if entry.get("Terminal") == "true":
            cmd = term(*cmd)
        apps.setdefault(entry.get("Name") or os.path.basename(path)[:-8], cmd)
    _appcache = (stamp, apps)
    return apps


def desktop_entry(path):
    """The [Desktop Entry] keys of a .desktop file (no translations)."""
    entry, inside = {}, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    inside = line == "[Desktop Entry]"
                elif inside and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    entry.setdefault(key.strip(), value.strip())
    except OSError:
        pass
    return entry


def desktop_exec(exec_line):
    """An Exec line as a command, without its %f, %U, ... field codes."""
    import shlex
    try:
        words = shlex.split(exec_line.replace("\\\\", "\\"))
    except ValueError:
        return []
    cmd = []
    for w in words:
        if len(w) == 2 and w[0] == "%" and w[1] != "%":
            continue                 # a file, URL or icon the menu doesn't pass
        cmd.append(w.replace("%%", "%"))
    return cmd


def menu_textw(text):
    return mdrw.fontset_getwidth(text) + mlrpad


def textw_clamp(text, n):
    w = mdrw.fontset_getwidth_clamp(text, n) + mlrpad
    return min(w, n)


def calcoffsets(mn):
    if mn.lines > 0:
        n = mn.lines * mbh
    else:
        n = mmw - (mpromptw + menu_textw("<") + menu_textw(">"))
    if mn.curr is None:
        mn.next = mn.prev = None
        return
    # calculate which items will begin the next page and previous page
    i, j = 0, mn.curr
    while j < len(mn.matches):
        i += mbh if mn.lines > 0 else textw_clamp(mn.matches[j], n)
        if i > n:
            break
        j += 1
    mn.next = j if j < len(mn.matches) else None
    i, j = 0, mn.curr
    while j > 0:
        i += mbh if mn.lines > 0 else textw_clamp(mn.matches[j - 1], n)
        if i > n:
            break
        j -= 1
    mn.prev = j


def typed_spans(mn, item):
    """Where the typed words are in item: [(start, end), ...], merged."""
    fold = (lambda t: t.lower()) if mn.nocase else (lambda t: t)
    hay, spans = fold(item), []
    for token in mn.text.split():
        token = fold(token)
        i = hay.find(token)
        while i >= 0:
            spans.append((i, i + len(token)))
            i = hay.find(token, i + len(token))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def drawitem(mn, idx, x, y, w):
    """An item, with the typed words in it highlighted (colors swapped)."""
    item = mn.matches[idx]
    if idx == mn.sel:
        mdrw.setscheme(mscheme["sel"])
    elif item in mn.out:
        mdrw.setscheme(mscheme["out"])
    else:
        mdrw.setscheme(mscheme["norm"])
    end = mdrw.text(x, y, w, mbh, mlrpad // 2, item, 0)
    for a, b in typed_spans(mn, item):
        hx = x + mlrpad // 2 + mdrw.fontset_getwidth(item[:a])
        hw = min(mdrw.fontset_getwidth(item[a:b]), x + w - hx)
        if hw > 0:
            mdrw.text(hx, y, hw, mbh, 0, item[a:b], 1)
    return end


def drawmenu(mn):
    """The prompt, then the items; there is no input field: the typed words
    show highlighted in the items, or on their own when nothing matches."""
    x = y = 0
    mdrw.setscheme(mscheme["norm"])
    mdrw.rect(0, 0, mmw, mmh, 1, 1)

    if mn.prompt:
        mdrw.setscheme(mscheme["sel"])
        x = mdrw.text(x, 0, mpromptw, mbh, mlrpad // 2, mn.prompt, 0)

    if not mn.matches:
        if mn.text:
            mdrw.setscheme(mscheme["norm"])
            tw = min(mdrw.fontset_getwidth(mn.text), mmw - x - mlrpad // 2)
            mdrw.text(x + mlrpad // 2, 0, tw, mbh, 0, mn.text, 1)
    elif mn.lines > 0:
        # draw vertical list
        i = mn.curr
        while i is not None and i != mn.next and i < len(mn.matches):
            drawitem(mn, i, x, y, mmw - x)
            y += mbh
            i += 1
    else:
        # draw horizontal list
        w = menu_textw("<")
        if mn.curr:
            mdrw.setscheme(mscheme["norm"])
            mdrw.text(x, 0, w, mbh, mlrpad // 2, "<", 0)
        x += w
        i = mn.curr
        while i is not None and i != mn.next and i < len(mn.matches):
            x = drawitem(mn, i, x, 0, textw_clamp(mn.matches[i], mmw - x - menu_textw(">")))
            i += 1
        if mn.next is not None:
            w = menu_textw(">")
            mdrw.setscheme(mscheme["norm"])
            mdrw.text(mmw - w, 0, w, mbh, mlrpad // 2, ">", 0)
    dpy.flush()
    mdrw.map(mwin.id, 0, 0, mmw, mmh)


def menumatch(mn):
    tokens = mn.text.split()          # strtok(buf, " ")
    if mn.nocase:
        fstr = lambda h, n: n.lower() in h.lower()
        fprefix = lambda h, n: h.lower().startswith(n.lower())
        fexact = lambda a, b: a.lower() == b.lower()
    else:
        fstr = lambda h, n: n in h
        fprefix = lambda h, n: h.startswith(n)
        fexact = lambda a, b: a == b
    exact, prefix, substr = [], [], []
    for item in mn.items:
        if not all(fstr(item, t) for t in tokens):  # not all tokens match
            continue
        # exact matches go first, then prefixes, then substrings
        if not tokens or fexact(mn.text, item):
            exact.append(item)
        elif fprefix(item, tokens[0]):
            prefix.append(item)
        else:
            substr.append(item)
    mn.matches = exact + prefix + substr
    mn.curr = mn.sel = 0 if mn.matches else None
    calcoffsets(mn)


def menuinsert(mn, s, n):
    """n > 0 inserts s at the cursor, n < 0 deletes -n characters before it."""
    if n > 0:
        mn.text = mn.text[:mn.cursor] + s + mn.text[mn.cursor:]
    elif n < 0:
        mn.text = mn.text[:mn.cursor + n] + mn.text[mn.cursor:]
    mn.cursor += n
    menumatch(mn)


def movewordedge(mn, direction):
    t, delim = mn.text, menu_worddelimiters
    if direction < 0:  # move cursor to the start of the word
        while mn.cursor > 0 and t[mn.cursor - 1] in delim:
            mn.cursor -= 1
        while mn.cursor > 0 and t[mn.cursor - 1] not in delim:
            mn.cursor -= 1
    else:              # move cursor to the end of the word
        while mn.cursor < len(t) and t[mn.cursor] in delim:
            mn.cursor += 1
        while mn.cursor < len(t) and t[mn.cursor] not in delim:
            mn.cursor += 1


def lookupkey(e):
    """A small XLookupString: (keysym, text) for a key event. Handles Shift,
    Caps Lock, AltGr and Num Lock; dead keys and input methods are not supported."""
    state = e.state
    level = 4 if state & X.Mod5Mask else 0                # AltGr: third level
    shift = bool(state & X.ShiftMask)
    ks = dpy.keycode_to_keysym(e.detail, level + 1) if shift else 0
    base = dpy.keycode_to_keysym(e.detail, level) or dpy.keycode_to_keysym(e.detail, 0)
    if state & numlockmask:                               # keypad digits
        kp = dpy.keycode_to_keysym(e.detail, 1)
        if 0xff80 <= kp <= 0xffbd:
            ks = kp if not shift else base
    if not ks:
        ks = base
        if shift and 0x61 <= ks <= 0x7a:                  # letter with no shifted symbol
            ks -= 0x20
    if state & X.LockMask and not shift and (0x61 <= ks <= 0x7a or 0xe0 <= ks <= 0xfe and ks != 0xf7):
        ks -= 0x20
    if 0x20 <= ks <= 0x7e or 0xa0 <= ks <= 0xff:
        ch = chr(ks)
    elif ks & 0xff000000 == 0x01000000:                   # Unicode keysyms
        ch = chr(ks & 0xffffff)
    elif 0xffaa <= ks <= 0xffb9 or ks in (0xff80, 0xffbd):  # keypad characters
        ch = chr(ks - 0xff80)
    else:
        ch = ""
    return ks, ch


def menukeypress(mn, e):
    ksym, buf = lookupkey(e)
    state = e.state
    K = XK

    if state & X.ControlMask:
        buf = ""   # with Ctrl held, X gives a control character, never inserted
        k = {K.XK_a: K.XK_Home, K.XK_b: K.XK_Left, K.XK_c: K.XK_Escape, K.XK_d: K.XK_Delete,
             K.XK_e: K.XK_End, K.XK_f: K.XK_Right, K.XK_g: K.XK_Escape, K.XK_h: K.XK_BackSpace,
             K.XK_i: K.XK_Tab, K.XK_n: K.XK_Down, K.XK_p: K.XK_Up}.get(ksym)
        if k:
            ksym = k
        elif ksym in (K.XK_j, K.XK_J, K.XK_m, K.XK_M):
            ksym = K.XK_Return
            state &= ~X.ControlMask
        elif ksym == K.XK_k:                       # delete right
            mn.text = mn.text[:mn.cursor]
            menumatch(mn)
        elif ksym == K.XK_u:                       # delete left
            menuinsert(mn, None, -mn.cursor)
        elif ksym == K.XK_w:                       # delete word
            while mn.cursor > 0 and mn.text[mn.cursor - 1] in menu_worddelimiters:
                menuinsert(mn, None, -1)
            while mn.cursor > 0 and mn.text[mn.cursor - 1] not in menu_worddelimiters:
                menuinsert(mn, None, -1)
        elif ksym in (K.XK_y, K.XK_Y):             # paste selection
            sel = dpy.intern_atom("CLIPBOARD") if state & X.ShiftMask else Xatom.PRIMARY
            utf8 = dpy.intern_atom("UTF8_STRING")
            mwin.convert_selection(sel, utf8, utf8, X.CurrentTime)
            return
        elif ksym in (K.XK_Left, K.XK_KP_Left):
            movewordedge(mn, -1)
            return drawmenu(mn)
        elif ksym in (K.XK_Right, K.XK_KP_Right):
            movewordedge(mn, +1)
            return drawmenu(mn)
        elif ksym in (K.XK_Return, K.XK_KP_Enter):
            pass
        elif ksym == K.XK_bracketleft:
            mn.running = False
            return
        else:
            return
    elif state & X.Mod1Mask:
        if ksym == K.XK_b:
            movewordedge(mn, -1)
            return drawmenu(mn)
        elif ksym == K.XK_f:
            movewordedge(mn, +1)
            return drawmenu(mn)
        k = {K.XK_g: K.XK_Home, K.XK_G: K.XK_End, K.XK_h: K.XK_Up, K.XK_j: K.XK_Next,
             K.XK_k: K.XK_Prior, K.XK_l: K.XK_Down}.get(ksym)
        if not k:
            return
        ksym = k

    n = len(mn.matches)
    if ksym in (K.XK_Delete, K.XK_KP_Delete, K.XK_BackSpace):
        if ksym != K.XK_BackSpace:
            if mn.cursor >= len(mn.text):
                return
            mn.cursor += 1
        if mn.cursor == 0:
            return
        menuinsert(mn, None, -1)
    elif ksym in (K.XK_End, K.XK_KP_End):
        if mn.cursor < len(mn.text):
            mn.cursor = len(mn.text)
        else:
            if mn.next is not None:
                # jump to end of list and position items in reverse
                mn.curr = n - 1
                calcoffsets(mn)
                mn.curr = mn.prev
                calcoffsets(mn)
                while mn.next is not None and mn.curr + 1 < n:
                    mn.curr += 1
                    calcoffsets(mn)
            mn.sel = n - 1 if n else None
    elif ksym == K.XK_Escape:
        mn.running = False
        return
    elif ksym in (K.XK_Home, K.XK_KP_Home):
        if mn.sel == (0 if n else None):
            mn.cursor = 0
        else:
            mn.sel = mn.curr = 0
            calcoffsets(mn)
    elif ksym in (K.XK_Left, K.XK_KP_Left, K.XK_Up, K.XK_KP_Up):
        if ksym in (K.XK_Left, K.XK_KP_Left):
            if mn.cursor > 0 and (mn.sel is None or mn.sel == 0 or mn.lines > 0):
                mn.cursor -= 1
                return drawmenu(mn)
            if mn.lines > 0:
                return
        if mn.sel is not None and mn.sel > 0:
            mn.sel -= 1
            if mn.sel + 1 == mn.curr:
                mn.curr = mn.prev
                calcoffsets(mn)
    elif ksym in (K.XK_Next, K.XK_KP_Next):
        if mn.next is None:
            return
        mn.sel = mn.curr = mn.next
        calcoffsets(mn)
    elif ksym in (K.XK_Prior, K.XK_KP_Prior):
        if mn.prev is None:
            return
        mn.sel = mn.curr = mn.prev
        calcoffsets(mn)
    elif ksym in (K.XK_Return, K.XK_KP_Enter):
        item = mn.matches[mn.sel] if mn.sel is not None else None
        line = item if (item is not None and not state & X.ShiftMask) else mn.text
        mn.onchoose(line)
        if not state & X.ControlMask:
            mn.running = False
            return
        if item is not None:
            mn.out.add(item)
    elif ksym in (K.XK_Right, K.XK_KP_Right, K.XK_Down, K.XK_KP_Down):
        if ksym in (K.XK_Right, K.XK_KP_Right):
            if mn.cursor < len(mn.text):
                mn.cursor += 1
                return drawmenu(mn)
            if mn.lines > 0:
                return
        if mn.sel is not None and mn.sel + 1 < n:
            mn.sel += 1
            if mn.sel == mn.next:
                mn.curr = mn.next
                calcoffsets(mn)
    elif ksym == K.XK_Tab:
        if mn.sel is None:
            return
        mn.text = mn.matches[mn.sel]
        mn.cursor = len(mn.text)
        menumatch(mn)
    else:
        if buf and buf.isprintable():
            menuinsert(mn, buf, len(buf))
    drawmenu(mn)


def menupaste(mn):
    """we have been given the current selection, now insert it into input"""
    utf8 = dpy.intern_atom("UTF8_STRING")
    p = xcall(mwin.get_full_property, utf8, utf8)
    if p and len(p.value):
        s = bytes(p.value).decode("utf-8", "replace").split("\n")[0]
        menuinsert(mn, s, len(s))
    drawmenu(mn)


def launcher(arg):
    """The program launcher: pick an installed application. Anything else
    typed runs as a command with $SHELL."""
    apps = desktop_apps()
    shell = os.environ.get("SHELL") or "/bin/sh"

    def run(line):
        spawn(apps[line] if line in apps else [shell, "-c", line])

    import locale
    locale.setlocale(locale.LC_COLLATE, "")
    openmenu(sorted(apps, key=locale.strxfrm), run, nocase=True)   # app names are capitalized


def openmenu(items, onchoose, prompt=None, lines=None, nocase=None):
    """Show the menu on the focused monitor until
    Enter or Escape; onchoose(line) is called for each chosen line.
    nocase=True ignores case whatever menu_case_insensitive says."""
    global mdrw, mwin, mbh, mmw, mmh, mlrpad, mpromptw
    mn = Menu(items)
    mn.onchoose = onchoose
    if prompt is not None:
        mn.prompt = prompt
    if lines is not None:
        mn.lines = min(lines, len(items))
    if nocase is not None:
        mn.nocase = nocase
    if mdrw is None:
        mdrw = Drw(screen_w, screen_h)
        if not mdrw.fontset_create(menu_fonts):
            die("no fonts could be loaded.")
        for name, cols in menu_colors.items():
            mscheme[name] = mdrw.scm_create(cols)
    mlrpad = mdrw.fonts[0].h

    # grab the keyboard; the key binding's own grab may still be active for a moment
    for _ in range(1000):
        if root.grab_keyboard(True, X.GrabModeAsync, X.GrabModeAsync,
                              X.CurrentTime) == X.GrabSuccess:
            break
        time.sleep(0.001)
    else:
        return

    # calculate menu geometry
    mbh = mdrw.fonts[0].h + 2
    lines = max(mn.lines, 0)
    mmh = max(lines, 1) * mbh
    m = selmon
    ax, aw = title_area(m)
    if prefns.get("menu_wide", False) or not m.showbar or aw < m.ww // 4:
        x = m.mx                                  # across the whole screen
        y = m.my + (0 if menu_topbar else m.mh - mmh)
        mmw = m.mw
    else:
        x = m.wx + ax                             # on the bar, where notifications show
        y = m.by if m.topbar else m.by + bh - mmh
        mmw = aw
    mpromptw = menu_textw(mn.prompt) - mlrpad // 4 if mn.prompt else 0
    menumatch(mn)

    # create menu window
    mwin = root.create_window(x, y, mmw, mmh, 0, X.CopyFromParent, X.CopyFromParent,
                              X.CopyFromParent, override_redirect=1,
                              background_pixel=mscheme["norm"][COL_BG].pixel,
                              event_mask=X.ExposureMask | X.KeyPressMask
                              | X.VisibilityChangeMask)
    mwin.set_wm_class("pytwm-menu", "pytwm-menu")
    mwin.map()
    mwin.raise_window()
    mdrw.resize(mmw, mmh)
    try:
        drawmenu(mn)
        while mn.running:
            ev = queued.pop(0) if queued else dpy.next_event()
            if ev.type == X.KeyPress:
                menukeypress(mn, ev)
            elif ev.type == X.KeyRelease:
                pass
            elif ev.type == X.Expose and ev.window.id == mwin.id:
                if ev.count == 0:
                    mdrw.map(mwin.id, 0, 0, mmw, mmh)
            elif ev.type == X.SelectionNotify and ev.requestor.id == mwin.id:
                if ev.property == dpy.intern_atom("UTF8_STRING"):
                    menupaste(mn)
            elif ev.type == X.VisibilityNotify and ev.window.id == mwin.id:
                if ev.state != X.VisibilityUnobscured:
                    mwin.raise_window()
            elif ev.type in handler:
                handler[ev.type](ev)   # the window manager keeps working
    finally:
        dpy.ungrab_keyboard(X.CurrentTime)
        mwin.destroy()
        dpy.sync()



# ============================================================================
#  The interface pref.py uses
#
#  pref.py is run in its own namespace, which starts as a copy of this
#  module's: it can use every function here, but defining a name of its own
#  never replaces one of the window manager's. The settings the window
#  manager reads (PREF_NAMES) are copied back after it has run.
#
#  Functions written in pref.py and bound to keys run in a background thread,
#  so slow commands in a menu never freeze the window manager. Whatever has
#  to touch X (opening a menu, view(), ...) is handed to the main thread with
#  call_main(), which waits for the result.
# ============================================================================

PREF_NAMES = ["borderpx", "snap", "showbar", "topbar", "fonts", "colors", "tags", "rules",
              "mfact", "nmaster", "resizehints", "lockfullscreen", "layouts", "keys", "buttons",
              "menu_lines", "menu_topbar", "menu_prompt",
              "menu_case_insensitive", "menu_worddelimiters", "autoreload",
              "lock_colors", "lock_failonclear"]
prefns = {}           # pref.py's namespace
pref_clashes = []     # names in pref.py that hide one of pytwm's functions
PREFSOURCE = b""      # pref.py's code, kept for the fallback
PARTSSOURCE = b""     # parts.py's code (b"" without one), kept for the fallback

_mainq = queue.Queue()
_wake_r, _wake_w = os.pipe()


def load_prefs():
    """Runs parts.py (the code behind the status, menus and watchers), then
    pref.py, in one namespace: pref.py can use everything in parts.py, and
    parts.py's functions see pref.py's settings."""
    global prefns, PREFSOURCE, PARTSSOURCE
    with open(PREF, "rb") as f:
        PREFSOURCE = f.read()
    try:
        with open(PARTS, "rb") as f:
            PARTSSOURCE = f.read()
    except FileNotFoundError:
        PARTSSOURCE = b""
    ns = dict(globals())
    ns["__file__"] = prefs_file
    ns["__name__"] = "pref"
    ns["term"] = lambda *cmd: term(*cmd, ns=ns)    # this file's terminal, even while it loads
    if PARTSSOURCE:
        exec(compile(PARTSSOURCE, parts_file, "exec"), ns)
    exec(compile(PREFSOURCE, prefs_file, "exec"), ns)
    missing = [n for n in PREF_NAMES if n not in ns]
    if missing:
        raise NameError("pref.py does not set: " + ", ".join(missing))
    for n in PREF_NAMES:
        globals()[n] = ns[n]
    # menus look like the bar unless pref.py gives them their own font or colors
    global menu_fonts, menu_colors
    menu_fonts = [ns["menufont"]] if "menufont" in ns else list(ns["fonts"])
    menu_colors = {"norm": tuple(ns["colors"]["norm"][:2]),
                    "sel": tuple(ns["colors"]["sel"][:2]),
                    "out": ("#000000", "#00ffff")}
    menu_colors.update(ns.get("menu_colors", {}))
    # a setting named like one of pytwm's functions hides it inside pref.py
    global pref_clashes
    pref_clashes = sorted(n for n, v in ns.items() if not n.startswith("_") and not callable(v)
                          and callable(globals().get(n)) and n not in PREF_NAMES)
    prefns = ns


def call(func, arg):
    """Run a key or button binding: pytwm's own functions right away, the
    ones written in pref.py in the background."""
    if getattr(func, "__globals__", None) is prefns:
        threading.Thread(target=_background, args=(func, arg), daemon=True).start()
    else:
        func(arg)


def _background(func, arg):
    try:
        func(arg)
    except Exception as ex:
        traceback.print_exc()
        call_main(showerror, "pref.py: %s: %s" % (type(ex).__name__, ex))


def showerror(msg):
    global last_error
    last_error = msg
    drawbars()


def call_main(func, *args):
    """Run func(*args) on the main thread and return its result."""
    if threading.current_thread() is threading.main_thread():
        return func(*args)
    box, done = {}, threading.Event()
    _mainq.put((func, args, box, done))
    os.write(_wake_w, b"x")
    done.wait()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def runmainq():
    while True:
        try:
            func, args, box, done = _mainq.get_nowait()
        except queue.Empty:
            return
        try:
            box["result"] = func(*args)
        except Exception as ex:
            box["error"] = ex
        finally:
            done.set()


def menu(items, prompt=None, lines=None):
    """Show a menu with these items. Returns the item picked with Enter (or
    the typed text with Shift+Enter), or None after Escape."""
    chosen = []
    call_main(openmenu, [str(i) for i in items], chosen.append, prompt, lines)
    return chosen[-1] if chosen else None


def choose(entries, prompt=None, lines=None):
    """Show a menu of {text: action} and run the action of the entry picked.
    Entries whose action is None are left out. Returns what the action returned."""
    entries = {text: action for text, action in entries.items() if action}
    picked = menu(list(entries), prompt, lines)
    if picked in entries:
        return entries[picked]()


def colored(text, color):
    """text drawn in another color ("#rrggbb") on the bar, e.g. in status():
    colored("VPN", "#ff5555")."""
    return COLOR_MARK + color + text + COLOR_MARK


every_last = {}     # func -> (when, result)

def every(seconds, func):
    """func()'s result, worked out again only once it is `seconds` old; in
    between the last result is reused. For slow parts of the status:
    every(5, network_status)."""
    now = time.monotonic()
    last = every_last.get(func)
    if last is None or now - last[0] >= seconds:
        last = every_last[func] = (now, func())
    return last[1]


def output(cmd, timeout=5):
    """Run a command (a list, or a shell line) and return what it printed. A
    command still running after `timeout` seconds is stopped, and "" returned,
    so nothing that hangs can freeze the status bar or a menu. A function
    (e.g. a not_set_notify placeholder) is called instead."""
    if callable(cmd):                            # e.g. a not_set_notify placeholder
        cmd()
        return ""
    args = list(cmd) if isinstance(cmd, (list, tuple)) else ["/bin/sh", "-c", cmd]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, text=True, start_new_session=True,
                                env=child_env())
    except OSError:
        return ""
    try:
        return proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)      # the command and everything it started
        proc.communicate()
        return ""


LAUNCH_WAIT = 60      # seconds launch(cmd, tag) waits for the command's windows
launched = []         # (process id, tag mask, until) from launch(cmd, tag)
launched_lock = threading.Lock()


def launch(cmd, tag=None):
    """Start a command (a list, or a shell line) in the background. tag: the tag
    number, or a list of them, its windows open on, e.g. launch(terminal, 2). A
    function (e.g. a not_set_notify placeholder) is called instead."""
    if callable(cmd):
        return cmd()
    if tag is not None:
        numbers = list(tag) if isinstance(tag, (list, tuple)) else [tag]
        bad = [n for n in numbers if not isinstance(n, int) or not 1 <= n <= len(tags)]
        if bad:
            raise ValueError("launch: there is no tag %r" % bad[0])
    pid = spawn(list(cmd) if isinstance(cmd, (list, tuple)) else sh(cmd))
    if pid and tag is not None:
        with launched_lock:
            launched.append((pid, tagmask(*numbers), time.monotonic() + LAUNCH_WAIT))


def parent_pid(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def launched_tags(w):
    """The tags launch(cmd, tag) chose, if window w belongs to that command (its
    process or one started by it, by _NET_WM_PID), else 0."""
    now = time.monotonic()
    with launched_lock:
        launched[:] = [l for l in launched if l[2] > now]
        wanted = {pid: mask for pid, mask, _ in launched}
    if not wanted:
        return 0
    prop = xcall(w.get_full_property, dpy.intern_atom("_NET_WM_PID"), Xatom.CARDINAL)
    pid = prop.value[0] if prop and len(prop.value) else 0
    for _ in range(32):                        # up through its parent processes
        if pid <= 1:
            break
        if pid in wanted:
            return wanted[pid]
        pid = parent_pid(pid)
    return 0


def notify(text, ms=None, urgency=None):
    """Show a notification. ms: how long it stays, in milliseconds (None: the usual
    time). urgency: "low", "normal" or "critical"."""
    launch(["notify-send"] + (["-t", str(ms)] if ms else [])
           + (["-u", urgency] if urgency else []) + [text])


def term(*cmd, ns=None):
    """A command that opens `cmd` in your terminal (`terminal` in pref.py)."""
    terminal = (ns or prefns).get("terminal", ["xterm"])
    if isinstance(terminal, str):
        terminal = [terminal]
    return [*terminal, "-e", *cmd]


watch_procs = []    # the commands watch() is running


def watch(cmd, func):
    """Run a command that keeps printing, e.g. ["pactl", "subscribe"], and call
    func(line) for every line. Its messages are untranslated (English). If it
    stops it is started again; pytwm stops it when it reloads or quits. Blocks:
    use it in a watcher started with in_background."""
    env = child_env(LC_ALL="C.UTF-8")
    while True:
        proc = subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, env=env)
        watch_procs.append(proc)
        try:
            for line in proc.stdout:
                func(line.rstrip("\n"))
        finally:
            proc.kill()
            proc.wait()
            watch_procs.remove(proc)
        time.sleep(5)


def stop_watches():
    for proc in list(watch_procs):
        proc.kill()


def not_set_notify(what):
    """A placeholder for a program not chosen yet: a notification says so.
    Replace it with a command, e.g. not_set_notify("Web browser") with ["firefox"]."""
    def placeholder(arg=None):
        where = prefs_file.replace(os.path.expanduser("~"), "~", 1)
        notify("%s not set: edit %s" % (what, where))
    placeholder.__name__ = "%s (not set)" % what     # as the Shortcuts list shows it
    return placeholder


def in_background(func, *args):
    """Run func(*args) in the background, e.g. a loop that keeps watching something.
    If it raises an error, the error is shown in the bar."""
    threading.Thread(target=_background, args=(lambda _: func(*args), None), daemon=True).start()


def sh(cmd):
    """A shell line as a command list: ["/bin/sh", "-c", cmd]."""
    return ["/bin/sh", "-c", cmd]


def tagmask(*numbers):
    """The tags with these numbers, for view, tag and friends: tagmask(3), tagmask(1, 2)."""
    mask = 0
    for n in numbers:
        mask |= 1 << (n - 1)
    return mask


def random_accent(arg):
    """A new accent color with a random hue, as dark and saturated as the current
    one so text on it stays readable. It's written into the accent line of
    pref.py, so it stays, and the reload applies it everywhere."""
    import colorsys, random, re
    current = str(prefns.get("accent", ""))
    try:
        with open(prefs_file) as f:
            src = f.read()
    except OSError as ex:
        return showerror("random_accent: %s" % ex)
    line = re.compile(r'^(accent\s*=\s*)"#[0-9a-fA-F]{6}"', re.M)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", current) or not line.search(src):
        return showerror('random_accent: pref.py needs a line like  accent = "#005577"')
    h, l, s = colorsys.rgb_to_hls(*(int(current[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    rgb = colorsys.hls_to_rgb(random.random(), l, s)
    new = "#%02x%02x%02x" % tuple(round(v * 255) for v in rgb)
    with open(prefs_file + ".tmp", "w") as f:            # not .new: setup.sh's copies
        f.write(line.sub(r'\g<1>"%s"' % new, src, count=1))
    os.replace(prefs_file + ".tmp", prefs_file)          # saving it makes pytwm reload


class Presses:
    """Applies changes (+5, -5) in the background, one at a time. Presses
    made while one is being applied (a held key) are added up and applied
    together, so they never pile up."""

    def __init__(self, apply, name):
        self.apply, self.name = apply, name
        self.lock = threading.Lock()
        self.todo, self.busy = 0, False

    def add(self, change):
        with self.lock:
            self.todo += int(change)
            if self.busy:
                return
            self.busy = True
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            while True:
                with self.lock:
                    change, self.todo = self.todo, 0
                if not change:
                    break
                msg = self.apply(change)
                if msg:
                    call_main(showerror, "%s: %s" % (self.name, msg))
        finally:
            with self.lock:
                self.busy = False


def run_ok(cmd, timeout=10):
    """Run a command and wait; True if it worked."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout, env=child_env()).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def in_thread(func, *args):
    """Run one of pytwm's own slow functions without stopping the window manager."""
    threading.Thread(target=func, args=args, daemon=True).start()


# --- volume (pactl: PipeWire or PulseAudio) ---------------------------------

def volume(change):
    """Turn the sound up (+5) or down (-5), in percent, never above 100%."""
    volume_presses.add(change)


def mute(arg=None):
    """Mute / unmute the sound."""
    in_thread(sound_toggle, ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])


def mic_mute(arg=None):
    """Mute / unmute the microphone."""
    in_thread(sound_toggle, ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "toggle"])


mute.repeat = mic_mute.repeat = False     # held down, they toggle only once


def sound_toggle(cmd):
    if not run_ok(cmd):
        return call_main(showerror, "sound: pactl failed (is pipewire-pulse running?)")
    status_now.set()


def volume_change(change):
    found = output(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    levels = [int(w[:-1]) for w in found.split() if w.endswith("%") and w[:-1].isdigit()]
    if not levels:
        return "pactl failed (is pipewire-pulse running?)"
    if max(levels) + change > 100:
        to = "100%"                             # the cap
    else:
        to = "%+d%%" % change                   # relative: keeps the left/right balance
    if not run_ok(["pactl", "set-sink-volume", "@DEFAULT_SINK@", to]):
        return "pactl failed"
    status_now.set()


volume_presses = Presses(volume_change, "volume")


# --- brightness --------------------------------------------------------------

BACKLIGHT_DIR = "/sys/class/backlight"
bright_buses = None       # i2c bus numbers of the external monitors, once found


def brightness(change):
    """Make the screens brighter (+5) or darker (-5), in percent. Laptop
    screens change their backlight (brightnessctl); external monitors are
    changed over the video cable (ddcutil). A monitor takes a moment for each
    change, so presses made meanwhile are added up and applied together."""
    bright_presses.add(change)


def bright_monitors():
    """The i2c buses of the monitors ddcutil can talk to."""
    found = output(["ddcutil", "detect", "--brief"], timeout=15)
    buses, display = [], False
    for line in found.splitlines():
        if line and not line[0].isspace():
            display = line.startswith("Display ")      # not "Invalid display"
        elif display and "/dev/i2c-" in line:
            buses.append(line.rsplit("/dev/i2c-", 1)[1].strip())
    return buses


def bright_change(change):
    """Apply one change to every screen; returns an error message, or None."""
    global bright_buses
    step = ["+" if change > 0 else "-", str(abs(change))]
    done = False
    try:
        backlight = bool(os.listdir(BACKLIGHT_DIR))
    except OSError:
        backlight = False
    if backlight:
        if not run_ok(["brightnessctl", "-q", "-c", "backlight", "set",
                           "%d%%%s" % (abs(change), step[0])]):
            return "brightnessctl failed (is it installed?)"
        done = True
    if not shutil.which("ddcutil"):
        return None if done else "no backlight, and ddcutil isn't installed"
    for attempt in range(2):
        if bright_buses is None:
            bright_buses = bright_monitors()
        # --noverify: some monitors report the new value only a moment later
        failed = [b for b in bright_buses
                  if not run_ok(["ddcutil", "--noverify", "--bus", b, "setvcp", "10"] + step)]
        if not failed:
            break
        bright_buses = None                  # a monitor changed: look again, once
    else:
        return "monitor on i2c bus %s didn't answer" % ", ".join(failed)
    if done or bright_buses:
        return None
    return "no screen found whose brightness can be changed"


bright_presses = Presses(bright_change, "brightness")


def wm(func, arg=None):
    """Call a window manager function (view, tag, setlayout, ...) from a menu."""
    return call_main(func, arg)



# ============================================================================
#  The screen lock
#
#  Runs as its own process (`pytwm.py lock`), so the screen stays
#  locked even if the window manager restarts or reloads underneath it. The
#  password is checked by unix_chkpwd, the setuid helper PAM's pam_unix uses,
#  which can check (only) the calling user's own password without root.
# ============================================================================

def ondisplay(name):
    """True when pytwm runs on display `name`, e.g. ":0" (":0.0" counts too)."""
    return os.environ.get("DISPLAY", "").split(".")[0] == name.split(".")[0]


def lock(arg):
    """Lock the screen, with the code running now (also when that is the
    previous version, after a reload that failed)."""
    spawn([sys.executable, SELF, "lock"])


def checkpass(passwd):
    import pwd
    user = pwd.getpwuid(os.getuid()).pw_name
    for helper in ("/usr/bin/unix_chkpwd", "/usr/sbin/unix_chkpwd", "/sbin/unix_chkpwd"):
        if os.path.exists(helper):
            break
    else:
        die("pytwm lock: unix_chkpwd not found")
    p = subprocess.run([helper, user, "nullok"], input=bytes(passwd) + b"\0",
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0


def lock_main():
    global dpy, screen, root, screen_w, screen_h
    load_prefs()
    try:
        dpy = open_display()
    except Exception:
        die("pytwm lock: cannot open display")
    dpy.set_error_handler(lambda err, req: None)
    screen = dpy.screen()
    root = screen.root
    screen_w, screen_h = screen.width_in_pixels, screen.height_in_pixels
    updatenumlockmask()
    cmap = screen.default_colormap
    color = {k: cmap.alloc_named_color(v).pixel for k, v in lock_colors.items()}

    # init color, an invisible cursor, and one window over everything
    win = root.create_window(0, 0, screen_w, screen_h, 0, X.CopyFromParent, X.CopyFromParent,
                             X.CopyFromParent, override_redirect=1,
                             background_pixel=color["init"])
    pm = win.create_pixmap(8, 8, 1)
    gc = pm.create_gc(foreground=0)
    pm.fill_rectangle(gc, 0, 0, 8, 8)
    invisible = pm.create_cursor(pm, (0, 0, 0), (0, 0, 0), 0, 0)
    win.change_attributes(cursor=invisible)
    win.map()
    win.raise_window()

    # try to grab mouse pointer *and* keyboard for 600ms, else fail the lock
    ptgrab = kbgrab = -1
    for _ in range(6):
        if ptgrab != X.GrabSuccess:
            ptgrab = root.grab_pointer(False, BUTTONMASK | X.PointerMotionMask,
                                       X.GrabModeAsync, X.GrabModeAsync, X.NONE,
                                       invisible, X.CurrentTime)
        if kbgrab != X.GrabSuccess:
            kbgrab = root.grab_keyboard(True, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
        if ptgrab == X.GrabSuccess and kbgrab == X.GrabSuccess:
            break
        time.sleep(0.1)
    else:
        die("pytwm lock: unable to grab %s" % ("mouse pointer" if ptgrab != X.GrabSuccess
                                               else "keyboard"))
    # get the windows mapped above us, and screen size changes
    root.change_attributes(event_mask=X.SubstructureNotifyMask | X.StructureNotifyMask)
    dpy.sync()

    passwd = bytearray()
    failure = False
    oldc = "init"
    running = True
    while running:
        ev = dpy.next_event()
        if ev.type == X.KeyPress:
            ksym, buf = lookupkey(ev)
            if ev.state & X.ControlMask:
                buf = ""                       # a control character
            if 0xff80 <= ksym <= 0xffbd:       # IsKeypadKey
                if ksym == XK.XK_KP_Enter:
                    ksym = XK.XK_Return
                elif XK.XK_KP_0 <= ksym <= XK.XK_KP_9:
                    ksym = XK.XK_0 + ksym - XK.XK_KP_0
            if (0xffbe <= ksym <= 0xffe0          # IsFunctionKey
                    or 0xff80 <= ksym <= 0xffbd    # IsKeypadKey
                    or 0xff60 <= ksym <= 0xff6b    # IsMiscFunctionKey
                    or 0xff91 <= ksym <= 0xff94    # IsPFKey
                    or 0x11000000 <= ksym <= 0x1100ffff):  # IsPrivateKeypadKey
                continue
            if ksym == XK.XK_Return:
                if checkpass(passwd):
                    running = False
                else:
                    dpy.bell(100)
                    failure = True
                passwd[:] = bytes(len(passwd))
                passwd.clear()
            elif ksym == XK.XK_Escape:
                passwd[:] = bytes(len(passwd))
                passwd.clear()
            elif ksym == XK.XK_BackSpace:
                if passwd:
                    passwd.pop()
            elif buf and buf.isprintable() and len(passwd) < 256:
                passwd += buf.encode()
            c = "input" if passwd else ("failed" if failure or lock_failonclear else "init")
            if running and c != oldc:
                win.change_attributes(background_pixel=color[c])
                win.clear_area()
                oldc = c
        elif ev.type == X.ConfigureNotify and ev.window.id == root.id:
            win.configure(width=ev.width, height=ev.height)
            win.raise_window()
        else:
            win.raise_window()        # something was mapped: stay on top
    dpy.ungrab_keyboard(X.CurrentTime)
    dpy.ungrab_pointer(X.CurrentTime)
    win.destroy()
    dpy.close()
    sys.exit(0)


# ============================================================================
#  Notifications
#
#  pytwm is the notification server itself: it owns the name
#  org.freedesktop.Notifications on the session D-Bus, which notify-send and
#  apps use, and shows the notifications on the bar of the selected monitor,
#  where the window title goes, one at a time. A background thread receives
#  the D-Bus messages and hands them to the main thread. When pytwm reloads,
#  the D-Bus connection is handed to the new process, so the name is never
#  free (if it were, D-Bus could start another notification daemon in the gap).
# ============================================================================

NOTIFY_PATH = "/org/freedesktop/Notifications"
NOTIFY_IFACE = "org.freedesktop.Notifications"
REASON_EXPIRED, REASON_DISMISSED, REASON_CLOSED = 1, 2, 3
URGENT_COLORS = ("#ffffff", "#990000", "#990000")   # when pref.py's colors have no "urgent"

notify_conn = None            # the D-Bus connection
notify_thread = None
notify_stop = threading.Event()
notify_queue = []             # the notifications, most urgent first; the first is the one shown
notify_since = None           # when the first one went on the bar
notify_paused = False
notify_next_id = 1
notify_flash = None           # (start, seconds) of the flash of a new notification
notify_phase = 0              # 1 while the flash shows it inverted
FLASH_STEP = 0.15             # seconds per blink half


def markup_strip(s):
    import re
    s = re.sub(r"<[^>]*>", "", s)
    return (s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&"))


def notify_shown():
    """The notification on the bar now, or None (none, paused, or the bar hidden)."""
    if notify_queue and not notify_paused and selmon and selmon.showbar:
        return notify_queue[0]
    return None


def notify_parts(n):
    """A notification as one line, in parts of (text, bold): a dot (so it
    never looks like a window title), how many times it came, the app's
    name, the summary in bold, the body, and how many more are waiting."""
    one_line = lambda s: " ".join(markup_strip(s).split())
    parts = [("\u25cf ", False)]
    if n["count"] > 1:
        parts.append(("(%d) " % n["count"], False))
    app = one_line(n["appname"])
    if app and app != "notify-send":   # notify-send's own name says nothing
        parts.append(("[%s] " % app, False))
    summary, body = one_line(n["summary"]), one_line(n["body"])
    if summary:
        parts.append((summary + ("  " if body else ""), True))
    if body:
        parts.append((body, False))
    if len(notify_queue) > 1:
        parts.append(("  (+%d)" % (len(notify_queue) - 1), False))
    return parts


def notify_draw(n, x, w):
    """Draws the notification in the bar from x, w wide."""
    drw.setscheme(notify_scheme(n))
    parts = notify_parts(n)
    try:
        for i, (text, bold) in enumerate(parts):
            drw.fonts = bold_fonts if bold else bar_fonts
            pad = lrpad // 2 if i == 0 else 0
            pw = w if i == len(parts) - 1 else min(w, drw.fontset_getwidth(text) + pad)
            drw.text(x, 0, pw, bh, pad, text, notify_phase)
            x, w = x + pw, w - pw
            if w <= 0:
                break
    finally:
        drw.fonts = bar_fonts


def notify_click(n):
    """A click on the notification: runs its default action, if it has one (e.g. a
    browser opens the chat it came from), and takes it away."""
    if "default" in n.get("actions", ()):
        notify_signal("ActionInvoked", "us", (n["id"], "default"))
    notify_close(n, REASON_DISMISSED)


def notify_next(arg=None):
    """Takes the notification on the bar away, so the next one shows."""
    n = notify_shown()
    if n:
        notify_close(n, REASON_DISMISSED)


def notify_scheme(n):
    return scheme[("norm", "sel", "urgent")[n["urgency"]]]


def notify_tick():
    """Starts the time of the notification on the bar, and takes it off when
    its time is up. While it can't be seen, its time doesn't run. A new one
    flashes when it first shows (a critical one for longer)."""
    global notify_since, notify_flash, notify_phase
    n = notify_shown()
    now = time.monotonic()
    if n is None:
        notify_since = None
    elif notify_since is None:
        notify_since = now
        if not n.get("flashed"):
            n["flashed"] = True
            notify_flash = (now, 12 * FLASH_STEP if n["urgency"] == 2 else 6 * FLASH_STEP)
    elif n["timeout"] > 0 and now - notify_since >= n["timeout"]:
        notify_close(n, REASON_EXPIRED)
        return
    phase = notify_inverted()
    if phase != notify_phase:
        notify_phase = phase
        drawbars()


def notify_inverted():
    """1 while a flash shows the notification in inverted colors."""
    if not notify_flash or not notify_shown():
        return 0
    t = time.monotonic() - notify_flash[0]
    return 1 if t < notify_flash[1] and int(t / FLASH_STEP) % 2 == 0 else 0


def notify_flashing():
    return bool(notify_flash) and time.monotonic() - notify_flash[0] < notify_flash[1]


def notify_sort():
    global notify_since
    first = notify_queue[0] if notify_queue else None
    notify_queue.sort(key=lambda n: (-n["urgency"], n["id"]))
    if notify_queue and notify_queue[0] is not first:
        notify_since = None           # another one is shown now: its time starts


def notify_add(appname, replaces_id, summary, body, urgency, timeout, tag, actions=()):
    """A new notification, or an update of the one it replaces or repeats.
    Returns its id."""
    global notify_next_id, notify_since
    if timeout < 0:                   # the usual time
        timeout = 0 if urgency == 2 else prefns.get("notification_timeout", 10)
    else:
        timeout = timeout / 1000
    fields = {"appname": appname, "summary": summary, "body": body,
              "urgency": urgency, "timeout": timeout, "tag": tag,
              "actions": list(actions[0::2])}  # the action keys; their labels aren't shown
    for n in notify_queue:
        if ((replaces_id and n["id"] == replaces_id)
                or (tag and n["tag"] == tag and n["appname"] == appname)):
            n.update(fields)          # replaced: e.g. a volume or brightness level
            break
        if all(n[k] == fields[k] for k in ("appname", "summary", "body", "urgency")):
            n["count"] += 1           # the same again: counted, and it flashes again
            n["flashed"] = False
            break
    else:
        n = dict(fields, id=notify_next_id, count=1)
        notify_next_id += 1
        notify_queue.append(n)
    if notify_queue and n is notify_queue[0]:
        notify_since = None           # shown: its time starts again
    notify_sort()
    notify_tick()
    drawbars()
    return n["id"]


def notify_close(n, reason):
    global notify_since
    if n not in notify_queue:
        return
    if n is notify_queue[0]:
        notify_since = None
    notify_queue.remove(n)
    notify_signal("NotificationClosed", "uu", (n["id"], reason))
    notify_tick()
    drawbars()


def notify_signal(name, sig, body):
    from jeepney import DBusAddress, new_signal
    if notify_conn:
        try:
            notify_conn.send(new_signal(DBusAddress(NOTIFY_PATH, interface=NOTIFY_IFACE),
                                        name, sig, body))
        except OSError:
            pass


def notify_handle(msg):
    """One D-Bus message, on the main thread."""
    from jeepney import MessageType, HeaderFields, new_method_return, new_error
    h = msg.header
    if h.message_type != MessageType.method_call:
        return
    iface = h.fields.get(HeaderFields.interface)
    member = h.fields.get(HeaderFields.member)
    args = msg.body

    def reply(sig="", body=()):
        notify_conn.send(new_method_return(msg, sig, body))

    if iface in (NOTIFY_IFACE, None) and member == "Notify":
        appname, replaces_id, icon, summary, body, actions, hints, timeout = args
        hv = {k: v[1] for k, v in hints.items()}
        urgency = max(0, min(2, int(hv.get("urgency", 1))))
        tag = str(hv.get("x-canonical-private-synchronous") or "")
        reply("u", (notify_add(appname, replaces_id, summary, body, urgency, timeout, tag,
                               actions),))
    elif iface in (NOTIFY_IFACE, None) and member == "CloseNotification":
        for n in list(notify_queue):
            if n["id"] == args[0]:
                notify_close(n, REASON_CLOSED)
        reply()
    elif iface in (NOTIFY_IFACE, None) and member == "GetCapabilities":
        # "actions": a click runs the default one; browsers (Chromium) need it
        reply("as", (["actions", "body", "x-canonical-private-synchronous"],))
    elif iface in (NOTIFY_IFACE, None) and member == "GetServerInformation":
        reply("ssss", ("pytwm", "pytwm", VERSION, "1.2"))
    elif iface == "org.freedesktop.DBus.Introspectable" and member == "Introspect":
        reply("s", ('<node><interface name="%s"/></node>' % NOTIFY_IFACE,))
    elif iface == "org.freedesktop.DBus.Peer" and member == "Ping":
        reply()
    else:
        notify_conn.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod"))


def notify_connect():
    """The D-Bus connection: handed over by the previous pytwm, or a new one
    that asks for the notification name (waiting a little if another
    notification server is just leaving)."""
    import socket
    from jeepney.io.blocking import open_dbus_connection, DBusConnection
    from jeepney.bus_messages import message_bus
    fd = os.environ.get("PYTWM_NOTIFY_FD")
    if fd:
        # already authenticated and has said Hello: build it without doing that again
        from jeepney.io.common import MessageFilters
        conn = DBusConnection.__new__(DBusConnection)
        super(DBusConnection, conn).__init__(socket.socket(fileno=int(fd)), False)
        conn._filters = MessageFilters()
        conn.unique_name = os.environ.get("PYTWM_NOTIFY_NAME", "")
        return conn
    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception as ex:
        sys.stderr.write("pytwm: no notifications, no D-Bus session: %s\n" % ex)
        return None
    for _ in range(20):
        r = conn.send_and_get_reply(message_bus.RequestName(NOTIFY_IFACE, 4))  # DO_NOT_QUEUE
        if r.body[0] in (1, 4):       # the owner now
            return conn
        time.sleep(0.5)
    conn.close()
    return False


def notify_start():
    """Start being the notification server (in the background)."""
    global notify_thread
    conn = notify_connect() if os.environ.get("PYTWM_NOTIFY_FD") else None
    notify_stop.clear()
    notify_thread = threading.Thread(target=notify_serve, args=(conn,), daemon=True)
    notify_thread.start()


def notify_serve(conn):
    """The D-Bus thread: passes each message to the main thread."""
    global notify_conn
    if conn is None:
        conn = notify_connect()
        if conn is False:
            call_main(showerror, "pytwm: another notification server is running")
        if not conn:
            return
    notify_conn = conn
    while not notify_stop.is_set():
        if not select.select([conn.sock], [], [], 0.2)[0]:
            continue
        while not notify_stop.is_set():
            try:
                msg = conn.receive(timeout=0)
            except TimeoutError:
                break
            except (ConnectionError, OSError):
                call_main(showerror, "pytwm: lost the D-Bus connection (notifications)")
                return
            call_main(notify_handle, msg)


def notify_handover():
    """Before a reload: stop the D-Bus thread, answer what has arrived, and
    return what the new process needs to take the connection over."""
    if not notify_conn:
        return {}
    notify_stop.set()
    end = time.monotonic() + 2
    while notify_thread.is_alive() and time.monotonic() < end:
        runmainq()                    # it may be waiting for the main thread
        notify_thread.join(0.05)
    while True:
        try:
            notify_handle(notify_conn.receive(timeout=0))
        except (TimeoutError, OSError, ConnectionError):
            break
    os.set_inheritable(notify_conn.sock.fileno(), True)
    return {"PYTWM_NOTIFY_FD": str(notify_conn.sock.fileno()),
            "PYTWM_NOTIFY_NAME": notify_conn.unique_name or ""}


def notifications_paused():
    """True while notifications are paused."""
    return notify_paused


def pause_notifications(paused=True):
    """Pause (True) or resume (False) notifications. While paused they wait."""
    global notify_paused
    notify_paused = bool(paused)
    call_main(lambda: (notify_tick(), drawbars()))


# ============================================================================
#  Wallpaper
#
#  `wallpaper` from pref.py on the root window: a picture (loaded and scaled
#  with gdk-pixbuf, through ctypes) on every monitor, or a plain color. It is
#  drawn into a pixmap made on a connection of its own that is left behind
#  when it closes (the usual way, as other wallpaper setters do), and named in
#  _XROOTPMAP_ID and ESETROOT_PMAP_ID so a compositor and transparent
#  terminals find it. Redone only when the setting, the picture or the
#  monitors change.
# ============================================================================

WALLPAPER_MODES = ("fill", "fit", "stretch", "center", "tile")


class Pixbuf:
    """A picture loaded with gdk-pixbuf."""
    _lib = None
    h_ = None

    def __init__(self, path=None, handle=None):
        L = Pixbuf.lib()
        if handle is None:
            err = ctypes.c_void_p()
            handle = L.gdk_pixbuf_new_from_file(path.encode(), ctypes.byref(err))
            if not handle:
                raise OSError("can't open the picture %s" % path)
        self.h_ = handle
        self.width, self.height = L.gdk_pixbuf_get_width(handle), L.gdk_pixbuf_get_height(handle)

    @classmethod
    def lib(cls):
        if cls._lib is None:
            L = ctypes.CDLL("libgdk_pixbuf-2.0.so.0")
            P, I = ctypes.c_void_p, ctypes.c_int
            for name, res, args in (
                    ("gdk_pixbuf_new_from_file", P, [ctypes.c_char_p, P]),
                    ("gdk_pixbuf_get_width", I, [P]), ("gdk_pixbuf_get_height", I, [P]),
                    ("gdk_pixbuf_get_rowstride", I, [P]), ("gdk_pixbuf_get_n_channels", I, [P]),
                    ("gdk_pixbuf_get_pixels", P, [P]),
                    ("gdk_pixbuf_scale_simple", P, [P, I, I, I]),
                    ("gdk_pixbuf_new_subpixbuf", P, [P, I, I, I, I])):
                f = getattr(L, name)
                f.restype, f.argtypes = res, args
            g = ctypes.CDLL("libgobject-2.0.so.0")
            g.g_object_unref.argtypes = [P]
            L.unref = g.g_object_unref
            cls._lib = L
        return cls._lib

    def __del__(self):
        if self.h_:
            Pixbuf.lib().unref(self.h_)

    @staticmethod
    def made(handle, what):
        """A new picture from gdk-pixbuf, which gives NULL (None here) when it can't."""
        if not handle:
            raise OSError("gdk-pixbuf couldn't %s the picture" % what)
        return Pixbuf(handle=handle)

    def crop(self, x, y, w, h):
        return Pixbuf.made(Pixbuf.lib().gdk_pixbuf_new_subpixbuf(self.h_, x, y, w, h), "cut")

    def scale(self, w, h):
        return Pixbuf.made(Pixbuf.lib().gdk_pixbuf_scale_simple(self.h_, max(w, 1), max(h, 1), 2), "scale")

    def bgrx(self):
        """The pixels as X wants them (blue, green, red, unused), row after row."""
        L, w, h = Pixbuf.lib(), self.width, self.height
        stride, n = L.gdk_pixbuf_get_rowstride(self.h_), L.gdk_pixbuf_get_n_channels(self.h_)
        raw = ctypes.string_at(L.gdk_pixbuf_get_pixels(self.h_), stride * (h - 1) + w * n)
        src = raw if stride == w * n else b"".join(raw[r * stride:r * stride + w * n] for r in range(h))
        out = bytearray(w * h * 4)
        out[0::4], out[1::4], out[2::4] = src[2::n], src[1::n], src[0::n]
        return bytes(out)


def wallpaper_put(pm, gc, depth, pb, x, y):
    """Draws the picture into the pixmap at x, y (in strips that fit a request)."""
    data, row = pb.bgrx(), pb.width * 4
    rows = max(1, 200000 // row)
    for r in range(0, pb.height, rows):
        n = min(rows, pb.height - r)
        pm.put_image(gc, x, y + r, pb.width, n, X.ZPixmap, depth, 0, data[r * row:(r + n) * row])


def wallpaper_monitor(pm, gc, depth, pic, mode, mx, my, mw, mh):
    """The picture on one monitor, in the given mode."""
    iw, ih = pic.width, pic.height
    if mode == "stretch":
        wallpaper_put(pm, gc, depth, pic.scale(mw, mh), mx, my)
    elif mode == "fit":                       # all of it, bars where it doesn't fill
        s = min(mw / iw, mh / ih)
        w, h = round(iw * s), round(ih * s)
        wallpaper_put(pm, gc, depth, pic.scale(w, h), mx + (mw - w) // 2, my + (mh - h) // 2)
    elif mode == "center":                    # its own size, cut if bigger
        w, h = min(iw, mw), min(ih, mh)
        part = pic.crop((iw - w) // 2, (ih - h) // 2, w, h)
        wallpaper_put(pm, gc, depth, part, mx + (mw - w) // 2, my + (mh - h) // 2)
    elif mode == "tile":
        for ty in range(0, mh, ih):
            for tx in range(0, mw, iw):
                part = pic.crop(0, 0, min(iw, mw - tx), min(ih, mh - ty))
                wallpaper_put(pm, gc, depth, part, mx + tx, my + ty)
    else:                                     # fill: covers the monitor, the middle kept
        s = max(mw / iw, mh / ih)
        w, h = min(iw, round(mw / s)), min(ih, round(mh / s))
        part = pic.crop((iw - w) // 2, (ih - h) // 2, w, h)
        wallpaper_put(pm, gc, depth, part.scale(mw, mh), mx, my)


def wallpaper_update():
    """Puts pref.py's wallpaper on the screen, if it changed or isn't there any more."""
    def fail(msg):
        sys.stderr.write("pytwm: %s\n" % msg)
        return showerror(msg)

    wp = prefns.get("wallpaper")
    if not wp:
        return
    mode = prefns.get("wallpaper_mode", "fill")
    path = os.path.expanduser(str(wp))
    is_color = str(wp).startswith("#")
    try:
        stamp = None if is_color else os.stat(path).st_mtime
    except OSError:
        return fail("wallpaper: can't find %s" % wp)
    key = repr((wp, mode, stamp, [(m.mx, m.my, m.mw, m.mh) for m in mons]))
    mine = os.environ.get("PYTWM_WALLPAPER_PM")       # the pixmap pytwm set last
    if os.environ.get("PYTWM_WALLPAPER") == key and mine:
        shown = root.get_full_property(dpy.intern_atom("_XROOTPMAP_ID"), Xatom.PIXMAP)
        if shown and shown.value and str(shown.value[0]) == mine:
            return
    if mode not in WALLPAPER_MODES:
        return fail("wallpaper_mode: use one of %s" % ", ".join(WALLPAPER_MODES))
    os.environ.pop("PYTWM_WALLPAPER", None)            # set again only when it worked
    errs, d = [], None
    try:
        d = open_display()
        d.set_error_handler(lambda err, req: errs.append(err))

        def check(what):
            """The requests so far are answered; an X error in any of them is a failure."""
            d.sync()
            if errs:
                raise OSError("X error %s: %s" % (what, errs[0]))

        scr = d.screen()
        r = scr.root
        depth = scr.root_depth
        pm = r.create_pixmap(scr.width_in_pixels, scr.height_in_pixels, depth)
        color = wp if is_color else "#000000"
        rgb = [int(color[i:i + 2], 16) * 257 for i in (1, 3, 5)]
        gc = pm.create_gc(foreground=scr.default_colormap.alloc_color(*rgb).pixel)
        pm.fill_rectangle(gc, 0, 0, scr.width_in_pixels, scr.height_in_pixels)
        if not is_color:
            pic = Pixbuf(path)
            for m in mons:
                wallpaper_monitor(pm, gc, depth, pic, mode, m.mx, m.my, m.mw, m.mh)
        check("drawing the wallpaper")              # before the root shows it
        # the previous wallpaper's pixmap, left behind the same way, goes; only
        # pytwm's own: another id's client may be gone and its id reused by a live one
        atoms = [d.intern_atom(n) for n in ("_XROOTPMAP_ID", "ESETROOT_PMAP_ID")]
        old = r.get_full_property(atoms[1], Xatom.PIXMAP)
        if old and old.value and str(old.value[0]) == mine and old.value[0] != pm.id:
            d.create_resource_object("pixmap", old.value[0]).kill_client()
            os.environ.pop("PYTWM_WALLPAPER_PM", None)
        for a in atoms:
            r.change_property(a, Xatom.PIXMAP, 32, [pm.id])
        r.change_attributes(background_pixmap=pm)
        r.clear_area()
        d.set_close_down_mode(X.RetainPermanent)
        check("setting the wallpaper")              # after the mode: sent alone at close, it was lost
        d.close()
    except Exception as ex:
        traceback.print_exc()
        if d:
            try:
                d.set_close_down_mode(X.DestroyAll)  # not retained: its pixmap goes
                d.close()
            except Exception:
                pass
        return showerror("wallpaper: %s" % ex)
    os.environ["PYTWM_WALLPAPER"] = key
    os.environ["PYTWM_WALLPAPER_PM"] = str(pm.id)


# ============================================================================
#  The look of GTK apps
#
#  The gtk settings from pref.py go three ways:
#  - into the GTK 2, 3 and 4 settings files, which apps read when they start
#    (only these settings are changed; every other line is kept);
#  - into the desktop settings (gsettings), which some apps follow instead,
#    e.g. the ones that only know light or dark;
#  - to open apps, live, through XSETTINGS: pytwm owns the _XSETTINGS_S0
#    selection and keeps the settings in a property that GTK apps watch.
#  With "accent": True, the accent from pref.py also goes into the GTK 3 and
#  4 user style sheets (gtk.css, in a marked block; the rest of the file is
#  kept), as the colors themes like adw-gtk3 and libadwaita take from there.
#  Open apps re-read them when the theme name changes for a moment. With
#  a Papirus icon theme, the folders take the Papirus color nearest the
#  accent: a small icon theme of links in ~/.local/share/icons, one per color
#  ("Papirus-Dark-magenta"), with the folder icons pointing at that color and
#  everything else from Papirus itself.
#  The files and gsettings are only written on the real display
#  (session_display), never from a test in Xephyr.
# ============================================================================

xsettings_win = None
xsettings_serial = 0
icon_theme_used = None        # the folder-colored copy of the icon theme, when there is one


def icon_theme_name():
    return icon_theme_used or (prefns.get("gtk") or {}).get("icon_theme")


def gtk_settings():
    """The gtk dict from pref.py as GTK setting names, leaving out the None ones."""
    g = prefns.get("gtk") or {}
    names = {"theme": "gtk-theme-name", "icon_theme": "gtk-icon-theme-name",
             "cursor_theme": "gtk-cursor-theme-name", "font": "gtk-font-name"}
    v = {names[k]: g[k] for k in names if g.get(k)}
    if icon_theme_name():
        v["gtk-icon-theme-name"] = icon_theme_name()
    if g.get("dark") is not None:
        v["gtk-application-prefer-dark-theme"] = 1 if g["dark"] else 0
    return v


def set_settings_file(path, values, ini):
    """Set these keys in a GTK settings file, keeping every other line. ini:
    GTK 3/4's settings.ini ([Settings], key=value); otherwise GTK 2's gtkrc
    (key="value")."""
    def line(k, v):
        if ini or isinstance(v, int):
            return "%s=%s\n" % (k, v)
        return '%s="%s"\n' % (k, v)
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        lines = []
    if ini and not any(l.strip() == "[Settings]" for l in lines):
        lines.insert(0, "[Settings]\n")
    left = dict(values)
    out = []
    for l in lines:
        key = l.split("=", 1)[0].strip()
        if "=" in l and key in left:
            out.append(line(key, left.pop(key)))
        else:
            out.append(l)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out += [line(k, v) for k, v in left.items()]
    if out != lines:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.writelines(out)


CSS_START = "/* pytwm: accent from pref.py (edit it there) */\n"
CSS_END = "/* pytwm: end */\n"


def accent_css():
    """The accent colors for gtk.css, or "" when the gtk setting "accent" is off."""
    import colorsys
    a = str(prefns.get("accent", ""))
    if not (prefns.get("gtk") or {}).get("accent") or len(a) != 7 or not a.startswith("#"):
        return ""
    r, g, b = (int(a[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    # accent_color is for text and lines on the dark background: lighter, so it reads
    light = "#%02x%02x%02x" % tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, max(l, 0.7), s))
    fg = "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 0.6 else "#ffffff"
    return (CSS_START + "@define-color accent_bg_color %s;\n@define-color accent_fg_color %s;\n"
            "@define-color accent_color %s;\n" % (a, fg, light) + CSS_END)


def set_accent_css(path, block):
    """Put the block in a gtk.css (replacing the previous one, or removing it
    when block is ""). True if the file changed."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        text = ""
    if CSS_START in text and CSS_END in text.split(CSS_START, 1)[1]:
        before, rest = text.split(CSS_START, 1)
        new = before + block + rest.split(CSS_END, 1)[1]
    else:
        new = block + text if block else text
    if new == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(new)
    os.replace(path + ".tmp", path)
    return True


ICON_DIRS = [os.path.expanduser("~/.local/share/icons"), os.path.expanduser("~/.icons"),
             "/usr/share/icons"]


def papirus_colors(base):
    """The folder colors of a Papirus theme: {name: (r, g, b)}, from the shade
    of each folder-<color>.svg."""
    import re
    places = os.path.join(base, "48x48", "places")
    colors = {}
    for f in os.listdir(places):
        m = re.fullmatch(r"folder-([a-z]+)\.svg", f)
        path = os.path.join(places, f)
        if not m or os.path.islink(path) or m.group(1) == "yaru":
            continue
        with open(path) as fh:
            fills = re.findall(r"fill[:=]\"?#([0-9a-fA-F]{6})", fh.read())
        if len(fills) >= 3 and fills[1].lower() in ("e4e4e4", "eceff4"):   # a folder, not an emblem
            colors[m.group(1)] = tuple(int(fills[2][i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colors


def nearest_color(colors, accent):
    """The name of the color nearest the accent: by hue, or by lightness for grey ones."""
    import colorsys
    h, l, s = colorsys.rgb_to_hls(*(int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    def distance(rgb):
        ch, cl, cs = colorsys.rgb_to_hls(*rgb)
        if s < 0.15 or cs < 0.15:                 # grey: only lightness counts
            return abs(cl - l) + (0 if s < 0.15 and cs < 0.15 else 2)
        dh = abs(ch - h)
        return min(dh, 1 - dh) + abs(cs - s) * 0.1
    return min(colors, key=lambda c: distance(colors[c]))


def papirus_folders(theme, accent):
    """The name of a copy of the Papirus theme whose folders have the color
    nearest the accent (made the first time), or the theme itself."""
    import re, shutil
    base = next((os.path.join(d, theme) for d in ICON_DIRS
                 if os.path.isfile(os.path.join(d, theme, "48x48", "places", "folder-blue.svg"))), None)
    if not base or len(accent) != 7:
        return theme
    color = nearest_color(papirus_colors(base), accent)
    if color == "blue":                           # Papirus's own
        return theme
    name = "%s-%s" % (theme, color)
    dest = os.path.join(ICON_DIRS[0], name)
    stamp = str(os.stat(os.path.join(base, "index.theme")).st_mtime)
    try:
        with open(os.path.join(dest, ".pytwm")) as f:
            if f.read() == stamp:
                return name                       # made already, for this Papirus
    except OSError:
        pass
    tmp = dest + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    for size in os.listdir(base):
        places = os.path.join(base, size, "places")
        if not os.path.isdir(places):
            continue
        os.makedirs(os.path.join(tmp, size, "places"))
        for f in os.listdir(places):
            real = os.path.realpath(os.path.join(places, f))
            m = re.fullmatch(r"(folder|user)-blue(.*)\.svg", os.path.basename(real))
            target = m and os.path.join(os.path.dirname(real), "%s-%s%s.svg" % (m.group(1), color, m.group(2)))
            if target and os.path.exists(target):
                os.symlink(target, os.path.join(tmp, size, "places", f))
    with open(os.path.join(base, "index.theme")) as f:
        index = f.read()
    index = re.sub(r"(?m)^Name=.*$", "Name=%s" % name, index, count=1)
    index = re.sub(r"(?m)^Inherits=", "Inherits=%s," % theme, index, count=1)
    with open(os.path.join(tmp, "index.theme"), "w") as f:
        f.write(index)
    with open(os.path.join(tmp, ".pytwm"), "w") as f:
        f.write(stamp)
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)
    return name


GTK2_FALLBACK = "/usr/share/themes/%s/gtk-2.0"     # Adwaita's GTK 2 version (gnome-themes-extra)


def gtk2_theme_link():
    """GTK 2 apps look for <theme>/gtk-2.0. A theme made only for GTK 3 and 4
    (adw-gtk3) has none, and they fall back to GTK 2's plain default; so
    ~/.themes/<theme>/gtk-2.0 becomes a link to Adwaita's GTK 2 version,
    dark or light. GTK 3 and 4 still find the theme's own files."""
    g = prefns.get("gtk") or {}
    theme = g.get("theme")
    home_themes = os.path.expanduser("~/.themes")
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dark = g.get("dark") or "dark" in str(theme).lower()
    target = GTK2_FALLBACK % ("Adwaita-dark" if dark else "Adwaita")
    # links made earlier for another theme, or pointing elsewhere now, go
    if os.path.isdir(home_themes):
        for name in os.listdir(home_themes):
            d = os.path.join(home_themes, name)
            link = os.path.join(d, "gtk-2.0")
            if (os.path.islink(link) and os.readlink(link).startswith(GTK2_FALLBACK.split("%s")[0])
                    and os.listdir(d) == ["gtk-2.0"] and (name != theme or os.readlink(link) != target)):
                os.remove(link)
                os.rmdir(d)
    if not theme or not os.path.isdir(target):
        return
    if any(os.path.isdir(os.path.join(d, theme, "gtk-2.0"))
           for d in (home_themes, os.path.join(data, "themes"), "/usr/share/themes")):
        return                      # the theme has its own GTK 2 version (or the link is there)
    os.makedirs(os.path.join(home_themes, theme), exist_ok=True)
    os.symlink(target, os.path.join(home_themes, theme, "gtk-2.0"))


def apply_gtk_settings():
    """Write the gtk settings to the GTK 2/3/4 files and gsettings."""
    import shutil
    global icon_theme_used
    g = prefns.get("gtk") or {}
    icon_theme_used = None
    if g.get("accent") and g.get("icon_theme"):
        try:
            icon_theme_used = papirus_folders(g["icon_theme"], str(prefns.get("accent", "")))
        except OSError as ex:
            call_main(showerror, "gtk: icon colors: %s" % ex)
        call_main(xsettings_update)
    v = gtk_settings()
    if not v:
        return
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    block = accent_css()
    changed = [set_accent_css(os.path.join(config, version, "gtk.css"), block)
               for version in ("gtk-3.0", "gtk-4.0")]
    if any(changed) and xsettings_win:
        # open GTK 3 apps re-read gtk.css when the theme changes: for a moment
        call_main(xsettings_update, "Adwaita")
        time.sleep(0.3)
        call_main(xsettings_update)
    gtk2 = {k: x for k, x in v.items() if k != "gtk-application-prefer-dark-theme"}
    set_settings_file(os.path.expanduser("~/.gtkrc-2.0"), gtk2, ini=False)
    try:
        gtk2_theme_link()
    except OSError as ex:
        call_main(showerror, "gtk: GTK 2 theme: %s" % ex)
    for version in ("gtk-3.0", "gtk-4.0"):
        set_settings_file(os.path.join(config, version, "settings.ini"), v, ini=True)
    if not shutil.which("gsettings"):
        return
    g = prefns.get("gtk") or {}
    dark = g.get("dark")
    for key, value in (("gtk-theme", g.get("theme")), ("icon-theme", icon_theme_name()),
                       ("cursor-theme", g.get("cursor_theme")), ("font-name", g.get("font")),
                       ("color-scheme", None if dark is None
                                        else "prefer-dark" if dark else "default")):
        if value and output(["gsettings", "get", "org.gnome.desktop.interface", key]
                            ).strip().strip("'") != value:
            output(["gsettings", "set", "org.gnome.desktop.interface", key, value])


def apply_dpi():
    """Set Xft.dpi in the X resources (what `xrdb` does with ~/.Xresources), so
    pytwm and the X apps started after it use that text size. dpi = None
    leaves the resources as they are."""
    value = prefns.get("dpi")
    if not value:
        return
    res = xcall(root.get_full_property, Xatom.RESOURCE_MANAGER, Xatom.STRING)
    lines = bytes(res.value).decode("utf-8", "replace").splitlines() if res else []
    lines = [l for l in lines if not l.startswith("Xft.dpi:")] + ["Xft.dpi:\t%s" % value]
    root.change_property(Xatom.RESOURCE_MANAGER, Xatom.STRING, 8,
                         ("\n".join(lines) + "\n").encode())
    dpy.sync()


def xsettings_start():
    """Become the XSETTINGS manager, unless another program already is."""
    global xsettings_win
    sel = dpy.intern_atom("_XSETTINGS_S%d" % dpy.get_default_screen())
    owner = dpy.get_selection_owner(sel)
    if getattr(owner, "id", owner) not in (0, X.NONE):
        return
    w = root.create_window(-1, -1, 1, 1, 0, X.CopyFromParent, override_redirect=1)
    w.set_selection_owner(sel, X.CurrentTime)
    if getattr(dpy.get_selection_owner(sel), "id", None) != w.id:
        return
    xsettings_win = w
    xsettings_update()
    ev = xevent.ClientMessage(window=root, client_type=dpy.intern_atom("MANAGER"),
                              data=(32, [X.CurrentTime, sel, w.id, 0, 0]))
    root.send_event(ev, event_mask=X.StructureNotifyMask)
    dpy.flush()


def xsettings_update(theme=None):
    """Put the gtk settings and dpi on the manager window, in the XSETTINGS
    format. theme: another theme name, for a moment (see apply_gtk_settings)."""
    import struct
    global xsettings_serial
    if not xsettings_win:
        return
    g = dict(prefns.get("gtk") or {}, icon_theme=icon_theme_name())
    if theme:
        g["theme"] = theme
    settings = [(name, g[k]) for k, name in (("theme", "Net/ThemeName"),
                                             ("icon_theme", "Net/IconThemeName"),
                                             ("cursor_theme", "Gtk/CursorThemeName"),
                                             ("font", "Gtk/FontName")) if g.get(k)]
    if prefns.get("dpi"):
        settings.append(("Xft/DPI", int(prefns["dpi"] * 1024)))
    pad = lambda b: b + b"\0" * (-len(b) % 4)
    data = struct.pack("<B3xII", 0, xsettings_serial, len(settings))   # 0: little endian
    for name, value in settings:
        n = name.encode()
        if isinstance(value, int):                                     # 0: an integer
            data += struct.pack("<BxH", 0, len(n)) + pad(n)
            data += struct.pack("<Ii", xsettings_serial, value)
        else:                                                          # 1: a string
            v = str(value).encode()
            data += struct.pack("<BxH", 1, len(n)) + pad(n)
            data += struct.pack("<II", xsettings_serial, len(v)) + pad(v)
    atom = dpy.intern_atom("_XSETTINGS_SETTINGS")
    xsettings_win.change_property(atom, atom, 8, data)
    xsettings_serial += 1
    dpy.flush()


# ============================================================================
#  Compositor (picom)
#
#  With `compositor` set in pref.py (vsync, fades and blur; no shadows), pytwm
#  writes picom's settings to its own file in the runtime folder (the user's
#  ~/.config/picom is never touched), starts picom with it and remembers its
#  process id across reloads. On a reload picom is only restarted when the
#  settings changed; compositor = None stops it.
# ============================================================================

PICOM_CONF = os.path.join(RUNDIR, "pytwm-picom-%s.conf"
                          % os.environ.get("DISPLAY", ":0").replace(":", ""))


def picom_config(c):
    """picom's settings for the compositor dict from pref.py"""
    fade = float(c.get("fade", 0.1))
    step = 1.0 if fade <= 0 else min(1.0, 0.01 / fade)      # picom fades in 10 ms steps
    blur = int(c.get("blur") or 0)                          # dual_kawase strength, 1-20
    return "\n".join([
        'backend = "%s";' % c.get("backend", "xrender"),
        "vsync = %s;" % ("true" if c.get("vsync", True) else "false"),
        "unredir-if-possible = true;",                      # fullscreen windows skip picom
        'blur-method = "%s";' % ("dual_kawase" if blur > 0 else "none"),
        "blur-strength = %d;" % max(1, min(blur, 20)),
        "blur-background = %s;" % ("true" if blur > 0 else "false"),
        "fading = %s;" % ("true" if fade > 0 else "false"),
        "fade-delta = 10;",
        "fade-in-step = %.3f;" % step,
        "fade-out-step = %.3f;" % step,
        "shadow = false;",
        "rules = (",
        "  # the bar, menus and the lock: no fade, no blur",
        '  { match = "override_redirect"; fade = false; blur-background = false; },',
        ");",
        "",
    ])


def picom_pid():
    """The picom pytwm started (remembered across reloads), if it is still running.
    It must use this display's config file: the variable is inherited by every
    program started from the session, e.g. a pytwm tried in Xephyr, which must
    never take it for its own."""
    pid = os.environ.get("PYTWM_PICOM_PID")
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as f:
            args = f.read().split(b"\0")
        if args and os.path.basename(args[0]) == b"picom" and PICOM_CONF.encode() in args:
            return int(pid)
    except (OSError, TypeError, ValueError):
        pass
    return None


def compositor_update():
    """Start, restart or stop picom to match pref.py's compositor setting."""
    import shutil
    c = prefns.get("compositor")
    pid = picom_pid()
    text = picom_config(c) if c else None
    try:
        with open(PICOM_CONF) as f:
            old = f.read()
    except OSError:
        old = None
    if pid and (text is None or text != old):
        os.kill(pid, signal.SIGTERM)                 # stopped or settings changed
        pid = None
    if text is None:
        os.environ.pop("PYTWM_PICOM_PID", None)
        return
    if pid:
        return                                       # already running with these settings
    if not shutil.which("picom"):
        sys.stderr.write("pytwm: compositor is set in pref.py, but picom is not installed\n")
        return
    with open(PICOM_CONF, "w") as f:
        f.write(text)
    try:
        pid = os.posix_spawnp("picom", ["picom", "--config", PICOM_CONF], child_env(),
                              setsid=True, setsigdef=(signal.SIGCHLD,),
                              file_actions=[(os.POSIX_SPAWN_OPEN, 0, "/dev/null", os.O_RDONLY, 0)])
        os.environ["PYTWM_PICOM_PID"] = str(pid)
    except OSError as ex:
        sys.stderr.write("pytwm: could not start picom: %s\n" % ex)


def screen_off_update():
    """screen_off from pref.py: the screen turns off after that many idle
    minutes, or never (None). X's own blanking to black stays off."""
    minutes = prefns.get("screen_off")
    dpy.set_screen_saver(0, 0, X.DefaultBlanking, X.DefaultExposures)
    if dpy.has_extension("DPMS"):
        if minutes:
            dpy.dpms_set_timeouts(0, 0, int(minutes * 60))
            dpy.dpms_enable()
        else:
            dpy.dpms_disable()
    dpy.sync()


handler = {
    X.ButtonPress: buttonpress,
    X.ClientMessage: clientmessage,
    X.ConfigureRequest: configurerequest,
    X.ConfigureNotify: configurenotify,
    X.DestroyNotify: destroynotify,
    X.EnterNotify: enternotify,
    X.Expose: expose,
    X.FocusIn: focusin,
    X.KeyPress: keypress,
    X.KeyRelease: keyrelease,
    X.MappingNotify: mappingnotify,
    X.MapRequest: maprequest,
    X.MotionNotify: motionnotify,
    X.PropertyNotify: propertynotify,
    X.UnmapNotify: unmapnotify,
}


MODIFIERS = {"super": X.Mod4Mask, "shift": X.ShiftMask, "ctrl": X.ControlMask,
             "control": X.ControlMask, "alt": X.Mod1Mask}
CLICK_AREAS = {"tags": TAGBAR, "layout": LTSYMBOL, "title": WINTITLE, "status": STATUSTEXT,
               "window": CLIENTWIN, "desktop": ROOTWIN}


def combo(text):
    """"super+shift+t" -> (modifier mask, "t")"""
    *mods, key = [p.strip() for p in text.split("+")]
    mask = 0
    for m in mods:
        if m.lower() not in MODIFIERS:
            raise ValueError("unknown modifier %r in %r (use super, shift, ctrl, alt)" % (m, text))
        mask |= MODIFIERS[m.lower()]
    return mask, key


def action(value, where):
    """What a key or button does -> (function, argument). A function is called;
    (function, value) calls it with that value; a command list is run."""
    if callable(value):
        return value, None
    if isinstance(value, (list, tuple)) and value and callable(value[0]):
        return value[0], (value[1] if len(value) > 1 else None)
    if isinstance(value, (list, tuple)) and value:
        return spawn, list(value)
    raise ValueError("don't know what to do for %r: %r" % (where, value))


def read_keys():
    """keys and tag_keys from pref.py -> [(modifiers, keysym, function, argument)]"""
    table = prefns["keys"]
    table = table() if callable(table) else table
    out = []
    for text, value in table.items():
        mask, name = combo(text)
        out.append((mask, keysym(name)) + action(value, text))
    if prefns.get("tag_keys"):
        mask = combo(prefns["tag_keys"] + "+x")[0]
        for i in range(min(len(tags), 9)):
            sym = keysym(str(i + 1))
            out += [(mask, sym, view, 1 << i),
                    (mask | X.ControlMask, sym, toggleview, 1 << i),
                    (mask | X.ShiftMask, sym, tag, 1 << i),
                    (mask | X.ControlMask | X.ShiftMask, sym, toggletag, 1 << i)]
    return out


def read_buttons():
    """buttons from pref.py -> [(click area, modifiers, button, function, argument)]"""
    table = prefns["buttons"]
    table = table() if callable(table) else table
    out = []
    for text, value in table.items():
        area, _, rest = text.partition(":")
        if area.strip() not in CLICK_AREAS:
            raise ValueError("unknown place %r in %r (use %s)"
                             % (area.strip(), text, ", ".join(CLICK_AREAS)))
        mask, name = combo(rest)
        if not name.startswith("button") or not name[6:].isdigit():
            raise ValueError("%r: the last part must be button1, button2, button3, ..." % text)
        out.append((CLICK_AREAS[area.strip()], mask, int(name[6:])) + action(value, text))
    return out


def read_rules():
    """rules from pref.py -> [(class, instance, title, tag mask, floating, monitor)]"""
    out = []
    for text, opts in rules.items():
        what, _, pattern = text.partition(":")
        if pattern and what.strip() in ("title", "instance", "class"):
            what, pattern = what.strip(), pattern.strip()
        else:
            what, pattern = "class", text.strip()
        unknown = set(opts) - {"tag", "float", "monitor"}
        if unknown:
            raise ValueError("rule %r: unknown option %s (use tag, float, monitor)"
                             % (text, ", ".join(sorted(unknown))))
        tagnums = opts.get("tag", [])
        tagnums = tagnums if isinstance(tagnums, (list, tuple)) else [tagnums]
        mask = 0
        for n in tagnums:
            if not 1 <= n <= len(tags):
                raise ValueError("rule %r: there is no tag %r" % (text, n))
            mask |= 1 << (n - 1)
        out.append((pattern if what == "class" else None,
                    pattern if what == "instance" else None,
                    pattern if what == "title" else None,
                    mask, bool(opts.get("float", False)), opts.get("monitor", -1)))
    return out


def keysym(name):
    """Keysym from its name, e.g. "Return" or "XF86AudioMute" ("return" works too)."""
    sym = XK.string_to_keysym(name) or XK.string_to_keysym(name[:1].upper() + name[1:])
    if not sym and name.startswith("XF86"):
        sym = getattr(xf86, "XK_XF86_" + name[4:], 0)
    if not sym:
        raise ValueError("unknown key name %r in keys" % name)
    return sym


# ============================================================================
#  The keys, printed in a terminal: `pytwm.py keys [--watch] [--no-color]`
#
#  Reads keys(), tag_keys and buttons from pref.py the way the window manager
#  does, so the list always matches what is set; --watch redraws it whenever
#  pref.py or parts.py is saved. It needs no X display.
# ============================================================================

KEY_NAMES = {"Return": "Enter", "space": "Space", "Escape": "Esc", "Print": "PrtSc",
             "comma": ",", "period": ".", "BackSpace": "Backspace", "Tab": "Tab",
             "KP_Add": "Keypad +", "KP_Subtract": "Keypad -", "KP_Enter": "Keypad Enter",
             "XF86AudioRaiseVolume": "Vol+", "XF86AudioLowerVolume": "Vol-",
             "XF86AudioMute": "Mute", "XF86AudioMicMute": "Mic mute",
             "XF86MonBrightnessUp": "Bright+", "XF86MonBrightnessDown": "Bright-",
             "XF86Display": "Display"}
BUTTON_NAMES = {1: "left click", 2: "middle click", 3: "right click", 4: "scroll up", 5: "scroll down"}
CLICK_PLACES = {"tags": "on a tag", "layout": "on the layout symbol", "title": "on the title",
                "status": "on the status", "window": "on a window", "desktop": "on the desktop"}
PROGRAM_SETTINGS = ("terminal", "editor", "browser", "file_manager", "screenshot", "sound_settings",
                    "display_settings", "system_monitor", "package_manager")
KEY_SECTIONS = ("Programs and menus", "Sound and screen", "Windows", "Layout", "Tags",
                "Monitors", "Session", "Mouse")

def _tags_text(mask):
    if mask == ALLTAGS or mask & TAGMASK == TAGMASK:
        return None
    return ", ".join(str(i + 1) for i in range(len(tags)) if mask >> i & 1)


def _sign(arg, more, less):
    return more if (arg or 0) > 0 else less


def describe_binding(value, place=None):
    """(section, what it does) for a key or button binding from pref.py."""
    if isinstance(value, (list, tuple)) and value and callable(value[0]):
        func, arg = value[0], (value[1] if len(value) > 1 else None)
    elif callable(value):
        func, arg = value, None
    else:                                           # a command
        cmd = [str(c) for c in value]
        for name in PROGRAM_SETTINGS:
            setting = prefns.get(name)
            if isinstance(setting, (list, tuple)) and cmd in (list(setting), term(*setting)):
                label = name.replace("_", " ").capitalize()
                shown = [str(s) for s in setting]
                if shown[:len(term())] == term() and len(shown) > len(term()):
                    shown = shown[len(term()):]             # what runs in the terminal
                shown[0] = os.path.basename(shown[0])
                return "Programs and menus", "%s (%s)" % (label, " ".join(shown))
        if cmd[:2] == ["/bin/sh", "-c"]:
            cmd = cmd[2:]
        return "Programs and menus", "Run: " + " ".join(cmd)
    name = getattr(func, "__name__", "?")
    tagtext = _tags_text(arg) if isinstance(arg, int) else ""
    d = {
        "launcher": lambda: ("Programs and menus", "Program launcher"),
        "volume": lambda: ("Sound and screen", "Volume %s %d%%" % (_sign(arg, "up", "down"), abs(arg or 0))),
        "mute": lambda: ("Sound and screen", "Mute / unmute the sound"),
        "mic_mute": lambda: ("Sound and screen", "Mute / unmute the microphone"),
        "brightness": lambda: ("Sound and screen", "Brightness %s %d%%" % (_sign(arg, "up", "down"), abs(arg or 0))),
        "killclient": lambda: ("Windows", "Close the window"),
        "zoom": lambda: ("Windows", "Swap the window with the master"),
        "togglefloating": lambda: ("Windows", "Float / tile the window"),
        "focusdir": lambda: ("Windows", "Focus the window %s" % {"up": "above", "down": "below"}.get(
            arg, "to the %s" % arg)),
        "swapdir": lambda: ("Windows", "Move the window %s (swap)" % arg),
        "focusstack": lambda: ("Windows", "Focus the %s window" % _sign(arg, "next", "previous")),
        "setcfact": lambda: ("Windows", "Even the heights in the column" if arg == 0 else
                     "Make the window %s" % _sign(arg, "taller", "shorter")),
        "movemouse": lambda: ("Windows", "Move the window (drag)"),
        "resizemouse": lambda: ("Windows", "Resize the window (drag)"),
        "incnmaster": lambda: ("Layout", "%s windows in the master area" % _sign(arg, "More", "Fewer")),
        "setmfact": lambda: ("Layout", "%s master area" % _sign(arg, "Wider", "Narrower")),
        "togglebar": lambda: ("Layout", "Show / hide the bar"),
        "view": lambda: ("Tags", "Show the clicked tag" if arg is None and place == "tags" else
                 "Back to the tags shown before" if arg is None else
                 "Show all tags" if tagtext is None else "Show tag " + tagtext),
        "tag": lambda: ("Tags", "Move the window to the clicked tag" if arg is None else
                "Put the window on all tags" if tagtext is None else "Move the window to tag " + tagtext),
        "toggleview": lambda: ("Tags", "Show / hide the clicked tag too" if arg is None else
                       "Show / hide tag %s too" % tagtext),
        "toggletag": lambda: ("Tags", "Add / remove the window on the clicked tag" if arg is None else
                      "Add / remove the window on tag %s" % tagtext),
        "focusmon": lambda: ("Monitors", "Focus the %s monitor" % _sign(arg, "next", "previous")),
        "tagmon": lambda: ("Monitors", "Move the window to the %s monitor" % _sign(arg, "next", "previous")),
        "lock": lambda: ("Session", "Lock the screen"),
        "notify_next": lambda: ("Session", "Dismiss the notification"),
        "random_accent": lambda: ("Session", "New random accent color"),
        "reload": lambda: ("Session", "Restart pytwm"),
        "quit": lambda: ("Session", "Quit pytwm (log out)"),
    }.get(name)
    d = d and d()
    if d:
        return d
    if name == "setlayout":
        if arg is None:
            return "Layout", "Back to the previous layout"
        lts = prefns["layouts"]()
        if isinstance(arg, int) and 0 <= arg < len(lts):
            sym, fn = lts[arg]
            kind = "floating" if fn is None else getattr(fn, "__name__", "?").replace("tile", "tiled")
            return "Layout", "Layout: %s %s" % (kind, sym)
        return "Layout", "Layout %r" % arg
    if name.endswith("(not set)"):                  # a not_set_notify placeholder
        return "Programs and menus", name
    doc = (getattr(func, "__doc__", None) or "").strip().split("\n")[0].rstrip(".")
    text = doc if doc and len(doc) <= 50 else name.replace("_", " ").capitalize()
    return "Programs and menus", text + ("" if arg is None else " %s" % arg)


def keys_text(color, hint=None):
    """The keys of pref.py, as text for a terminal (with ANSI colors if color), with
    the line hint under the title."""
    if color:
        RESET, BOLD = "\x1b[0m", "\x1b[1m"
        # the terminal's own colors, as in dialogrc: blue titles and keys, yellow modifiers
        head, desc, dim, keyc, plus = "\x1b[34m", "\x1b[37m", "\x1b[90m", "\x1b[1;34m", "\x1b[90m"
        modc = dict.fromkeys(("Super", "Shift", "Ctrl", "Alt"), "\x1b[33m")
    else:
        RESET = BOLD = head = desc = dim = keyc = plus = ""
        modc = {}
    c = lambda color_, text: color_ + text + (RESET if color_ else "")

    def combo_text(text, key_label=None):
        *mods, key = [p.strip() for p in text.split("+")]
        names = [{"super": "Super", "shift": "Shift", "ctrl": "Ctrl", "control": "Ctrl",
                  "alt": "Alt"}.get(m.lower(), m) for m in mods]
        if key_label is None:
            key_label = KEY_NAMES.get(key, key.upper() if len(key) == 1 else key)
        parts = [c(modc.get(n, ""), n) for n in names] + [c(keyc, key_label)]
        visible = " + ".join(names + [key_label])
        return c(plus, " + ").join(parts), len(visible)

    rows = {s: [] for s in KEY_SECTIONS}
    table = prefns["keys"]
    table = table() if callable(table) else table
    for text, value in table.items():
        section, what = describe_binding(value)
        rows[section].append(combo_text(text) + (what,))
    if prefns.get("tag_keys"):
        n = min(len(tags), 9)
        label = "1-%d" % n if n > 1 else "1"
        mod = prefns["tag_keys"]
        for extra, what in (("", "Show tag n"), ("+ctrl", "Show / hide tag n too"),
                            ("+shift", "Move the window to tag n"),
                            ("+ctrl+shift", "Add / remove the window on tag n")):
            rows["Tags"].append(combo_text(mod + extra + "+n", label) + (what,))
    btable = prefns["buttons"]
    btable = btable() if callable(btable) else btable
    for text, value in btable.items():
        place, _, rest = text.partition(":")
        *mods, button = [p.strip() for p in rest.split("+")]
        num = int(button[6:]) if button[6:].isdigit() else 0
        label = "%s %s" % (BUTTON_NAMES.get(num, button).capitalize(), CLICK_PLACES.get(place.strip(), place))
        _, what = describe_binding(value, place.strip())
        rows["Mouse"].append(combo_text("+".join(mods + ["x"]), label) + (what,))

    width = max(48, min(shutil.get_terminal_size((88, 24)).columns, 100))
    col = max([w for r in rows.values() for _, w, _ in r] + [0]) + 3
    rule = c(dim, "─" * width)
    where = prefs_file.replace(os.path.expanduser("~"), "~", 1)
    out = [c(head + BOLD, "  pytwm keys"), c(dim, "  from %s, as it is now" % where),
           c(dim, "  change them in keys(), tag_keys and buttons in pref.py")]
    if hint:
        out.append(c(dim, "  " + hint))
    out.append(rule)
    for section in KEY_SECTIONS:
        if not rows[section]:
            continue
        out += ["", c(head + BOLD, " ▸ " + section), ""]
        for keys_, w, what in rows[section]:
            out.append("    " + keys_ + " " * (col - w) + c(desc, what))
    return "\n".join(out)


def keys_main(args):
    """`pytwm.py keys [--pager | --watch] [--no-color]`: print the keys; --pager
    shows them in less (scroll, / to search, q to close); --watch prints them
    again whenever pref.py or parts.py is saved."""
    watch_ = "--watch" in args
    color = sys.stdout.isatty() and "--no-color" not in args
    if "--pager" in args and shutil.which("less"):
        global TAGMASK
        try:
            load_prefs()
            TAGMASK = (1 << len(tags)) - 1
            text = keys_text(color, "arrows / PageDown to scroll, / to search, q to close")
        except Exception as ex:
            text = "pref.py: %s: %s" % (type(ex).__name__, ex)
        subprocess.run(["less", "-R"], input=text.encode())
        return

    def render():
        global TAGMASK
        try:
            load_prefs()
            TAGMASK = (1 << len(tags)) - 1             # setup() sets it in the window manager
            text = keys_text(color, watch_ and "updates when you save pref.py — Ctrl+C to close")
        except Exception as ex:
            text = "pref.py: %s: %s" % (type(ex).__name__, ex)
        if watch_ and sys.stdout.isatty():
            sys.stdout.write("\x1b[H\x1b[2J")       # clear the terminal
        print(text)
        sys.stdout.flush()

    render()
    if not watch_:
        return
    stamp = file_mtime()
    try:
        while True:
            time.sleep(0.5)
            if file_mtime() != stamp:
                stamp = file_mtime()
                render()
    except KeyboardInterrupt:
        pass


# ============================================================================
#  pytwm live reload
#
#  When this file is saved, pytwm checks that it compiles, writes its state
#  (which window is on which tag and monitor, layouts, focus) to a file and
#  replaces itself with the new version (os.execv). The new process adopts
#  every window again through scan(), as at startup, and then
#  restores the saved state. If the new version crashes while starting, it
#  falls back to the previous one, so a mistake never takes the session down.
# ============================================================================

STATEFILE = os.path.join(RUNDIR, "pytwm-%s.json" % os.environ.get("DISPLAY", ":0").replace(":", ""))
FALLBACKDIR = os.path.join(RUNDIR, "pytwm-last-good-%s" % os.environ.get("DISPLAY", ":0").replace(":", ""))
with open(SELF, "rb") as _f:
    SOURCE = _f.read()      # this version's code, kept for the fallback


def file_mtime():
    def mtime(path):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None
    return mtime(WATCH), mtime(prefs_file), mtime(parts_file)


def check_compiles():
    """Raises the error if pytwm.py, pref.py or parts.py doesn't compile."""
    for path in (WATCH, prefs_file, parts_file):
        if path == parts_file and not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            compile(f.read(), path, "exec")


def save_last_good(folder):
    """This version's files, for going back to if the next one fails to start."""
    os.makedirs(folder, exist_ok=True)
    for name, src in (("pytwm.py", SOURCE), ("pref.py", PREFSOURCE), ("parts.py", PARTSSOURCE)):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(src)


def reload_env(**more):
    """The environment for the restarted pytwm: the real files, to load and to watch."""
    return dict(os.environ, PYTWM_WATCH=WATCH, PYTWM_WATCH_PREF=prefs_file, PYTWM_PREF=prefs_file,
                PYTWM_WATCH_PARTS=parts_file, PYTWM_PARTS=parts_file, **more)


def savestate():
    li = {id(l): i for i, l in enumerate(LAYOUTS)}
    state = {"selmon": mons.index(selmon), "mons": []}
    for m in mons:
        pt = m.taglayouts
        state["mons"].append({
            "tagset": m.tagset, "seltags": m.seltags, "sellt": m.sellt,
            "lt": [li.get(id(l), 0) for l in m.lt], "mfact": m.mfact,
            "nmaster": m.nmaster, "showbar": m.showbar,
            "taglayouts": {"curtag": pt.curtag, "prevtag": pt.prevtag, "nmasters": pt.nmasters,
                       "mfacts": pt.mfacts, "sellts": pt.sellts, "showbars": pt.showbars,
                       "ltidxs": [[li.get(id(l), 0) for l in p] for p in pt.ltidxs]},
            "clients": [c.win.id for c in m.clients],
            "stack": [c.win.id for c in m.stack],
            "sel": m.sel.win.id if m.sel else None,
            "c": {str(c.win.id): {k: getattr(c, k) for k in (
                "tags", "isfloating", "isfullscreen", "oldstate", "bw", "oldbw",
                "x", "y", "w", "h", "oldx", "oldy", "oldw", "oldh", "cfact")} for c in m.clients},
        })
    state["notify"] = {"queue": notify_queue, "paused": notify_paused, "next_id": notify_next_id}
    with open(STATEFILE, "w") as f:
        json.dump(state, f)


def restorestate():
    global selmon
    try:
        with open(STATEFILE) as f:
            state = json.load(f)
        os.unlink(STATEFILE)
    except (OSError, ValueError):
        return
    allc = {c.win.id: c for m in mons for c in m.clients}
    nl = len(LAYOUTS)
    n = len(tags) + 1
    for m, s in zip(mons, state["mons"]):
        m.tagset = [t & TAGMASK or 1 for t in s["tagset"]]
        m.seltags, m.sellt = s["seltags"], s["sellt"]
        m.lt = [LAYOUTS[i % nl] for i in s["lt"]]
        m.mfact, m.nmaster, m.showbar = s["mfact"], s["nmaster"], s["showbar"]
        p, pt = s.get("taglayouts"), m.taglayouts
        if p and len(p["nmasters"]) == n:   # only if the number of tags did not change
            pt.curtag, pt.prevtag = p["curtag"], p["prevtag"]
            pt.nmasters, pt.mfacts = p["nmasters"], p["mfacts"]
            pt.sellts, pt.showbars = p["sellts"], p["showbars"]
            pt.ltidxs = [[LAYOUTS[i % nl] for i in lt] for lt in p["ltidxs"]]
        for wid, cs in s["c"].items():
            c = allc.get(int(wid))
            if not c:
                continue
            c.mon.clients.remove(c)
            c.mon.stack.remove(c)
            c.mon = m
            for k, v in cs.items():
                setattr(c, k, v)
            c.tags = c.tags & TAGMASK or m.tagset[m.seltags]
            if not c.isfullscreen:
                c.bw = borderpx
            m.clients.append(c)
            m.stack.append(c)
        order = {w: i for i, w in enumerate(s["clients"])}
        m.clients.sort(key=lambda c: order.get(c.win.id, -1))
        sorder = {w: i for i, w in enumerate(s["stack"])}
        m.stack.sort(key=lambda c: sorder.get(c.win.id, -1))
        m.sel = allc.get(s["sel"]) if allc.get(s["sel"]) in m.clients else None
        updatebarpos(m)
        m.barwin.configure(x=m.wx, y=m.by, width=m.ww, height=bh)
    selmon = mons[min(state["selmon"], len(mons) - 1)]
    global notify_queue, notify_paused, notify_next_id
    nt = state.get("notify")
    if nt:
        notify_queue, notify_paused, notify_next_id = nt["queue"], nt["paused"], nt["next_id"]
    # scan() already tiled every window onto the first tag; move them back
    # to their saved geometry so arrange() starts from the real positions
    for m in mons:
        for c in m.clients:
            resizeclient(c, c.x, c.y, c.w, c.h)
    updateclientlist()
    arrange(None)
    focus(selmon.sel)
    restack(selmon)


def reload(arg):
    """Restart in place with the code currently in pytwm.py and pref.py."""
    try:
        check_compiles()
    except Exception as ex:
        traceback.print_exc()
        showerror("pytwm: not reloaded, %s: %s" % (type(ex).__name__, ex))
        return
    handover = notify_handover()
    savestate()
    save_last_good(FALLBACKDIR)
    dpy.flush()
    stop_watches()
    env = reload_env(PYTWM_RESTORE="1", PYTWM_FALLBACK=FALLBACKDIR, **handover)
    os.execve(sys.executable, [sys.executable, WATCH], env)


def main():
    global dpy, last_error
    if len(sys.argv) == 2 and sys.argv[1] == "-v":
        die("pytwm-" + VERSION)
    elif sys.argv[1:] == ["lock"]:
        lock_main()
    elif sys.argv[1:2] == ["keys"]:
        keys_main(sys.argv[2:])
        sys.exit(0)
    elif len(sys.argv) != 1:
        die("usage: pytwm [-v | lock | keys [--pager | --watch] [--no-color]]")
    try:
        dpy = open_display()
    except Exception:
        die("pytwm: cannot open display")
    dpy.set_error_handler(xerror)
    globals()["root"] = dpy.screen().root
    os.environ.setdefault("DIALOGRC", os.path.join(HERE, "dialogrc"))   # dialog colors, for every program
    checkotherwm()
    load_prefs()
    apply_dpi()                     # before setup() opens the fonts
    setup()
    scan()
    if os.environ.pop("PYTWM_RESTORE", None):
        restorestate()
    else:
        # a fresh start, not a reload: autostart, only on the real display
        # (not a test in Xephyr)
        if "autostart" in prefns and ondisplay(prefns.get("session_display", ":0")):
            threading.Thread(target=_background, args=(lambda arg: prefns["autostart"](), None),
                             daemon=True).start()
    if "status" in prefns:
        threading.Thread(target=statusloop, daemon=True).start()
    notify_start()                  # the notification server
    xsettings_start()               # GTK apps follow pref.py's gtk settings live
    wallpaper_update()              # before picom, which could miss it while starting
    compositor_update()             # picom, if pref.py sets a compositor
    screen_off_update()
    if ondisplay(prefns.get("session_display", ":0")):
        threading.Thread(target=_background, args=(lambda arg: apply_gtk_settings(), None),
                         daemon=True).start()
    for k in ("PYTWM_FALLBACK", "PYTWM_NOTIFY_FD", "PYTWM_NOTIFY_NAME"):
        os.environ.pop(k, None)     # started fine, no fallback needed
    last_error = os.environ.pop("PYTWM_ERROR", None)
    if pref_clashes and not last_error:
        last_error = "pref.py: %s hides pytwm's function of that name; rename it" % ", ".join(pref_clashes)
    if last_error:
        drawbars()
    run()
    stop_watches()
    cleanup()
    dpy.close()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except error.ConnectionClosedError:
        die("pytwm: X connection closed")   # X server shut down
    except Exception:
        traceback.print_exc()
        fallback = os.environ.pop("PYTWM_FALLBACK", None)
        if fallback and os.path.exists(os.path.join(fallback, "pytwm.py")):
            # the new version failed to start: go back to the previous one
            sys.stderr.write("pytwm: new version failed to start, falling back\n")
            ex = sys.exc_info()[1]
            env = dict(os.environ, PYTWM_RESTORE="1",
                       PYTWM_PREF=os.path.join(fallback, "pref.py"),
                       PYTWM_PARTS=os.path.join(fallback, "parts.py"),
                       PYTWM_ERROR="pytwm: not reloaded, %s: %s" % (type(ex).__name__, ex))
            fb = os.path.join(fallback, "pytwm.py")
            os.execve(sys.executable, [sys.executable, fb] + sys.argv[1:], env)
        sys.exit(1)
