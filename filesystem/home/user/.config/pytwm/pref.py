"""
pytwm preferences: appearance, programs, keys, rules, layouts and autostart.
The status bar, the menus and the layouts are in parts.py, next to this file;
to change one, copy it here and edit it.

  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Edit this file and SAVE it. The running pytwm restarts in place within   │
  │ half a second: windows, tags and layouts are kept. If you make a         │
  │ mistake, pytwm keeps the previous version running and shows the error    │
  │ in the bar. Fix it and save again.                                       │
  └──────────────────────────────────────────────────────────────────────────┘

Everything you can use here, with examples, is in doc.md next to this file
(system menu → Help).
"""

import os

# ============================================================================
#  Appearance
#  Colors and fonts, the bar, menus, GTK apps, notifications, screen lock.
# ============================================================================

fonts = ["monospace:size=9", "JetBrainsMono Nerd Font Propo:size=9"]  # first font is the main one, the rest are fallbacks (the Nerd Font has the bar's icons)
dpi = "auto"                  # size of text and GTK apps: "auto" from the monitors; 96 normal, 192 doubles it; None leaves it to ~/.Xresources

# Colors are "#rrggbb" strings.
background  = "#222222"       # bar and menu background
border      = "#444444"       # border of unfocused windows
text        = "#bbbbbb"       # bar and menu text
text_active = "#eeeeee"       # text on the selected tag, focused title, selected menu item
accent      = "#005577"       # selected tag, focused title and border, lock while typing

colors = {
    #          fg           bg          border
    "norm":   (text,        background, border),      # unselected tags, menu items, borders
    "sel":    (text_active, accent,     accent),      # selected tag and item, focused window
    "urgent": (text_active, "#990000",  "#990000"),   # critical notifications
}

# --- bar and windows ----------------------------------------------------------
borderpx = 3                  # border pixel of windows
gaps = 6                      # pixels between windows and around the screen edge; 0: none
showbar = True                # False means no bar
topbar = True                 # False means bottom bar
bar_title = False             # True shows the focused window's title in the bar
hide_vacant_tags = True       # show only tags with windows, and the one you're on

# --- wallpaper ---------------------------------------------------------------------
wallpaper = "#000000"         # a color, or a picture, e.g. "~/Pictures/mountains.jpg"
wallpaper_mode = "fill"       # fill (covers the screen), fit (all of it), stretch, center or tile

# --- menus (the launcher and your own) -----------------------------------------
menu_lines = 0               # 0: one horizontal line; N: vertical list of N items
menu_wide = False            # False: on the bar, where notifications show; True: across the screen
menu_topbar = True           # False puts the wide menu at the bottom
menu_prompt = "Exec:"        # prompt for the launcher; None for none

# --- apps (GTK) ------------------------------------------------------------------
gtk = {
    "theme": "adw-gtk3-dark",        # a folder in /usr/share/themes or ~/.themes
    "icon_theme": "Papirus-Dark",    # a folder in /usr/share/icons or ~/.icons
    "cursor_theme": None,            # e.g. "Adwaita"
    "font": "DejaVu Sans 10",        # "Family Size"
    "dark": True,                    # dark mode, for apps that only know light or dark
    "accent": True,                  # apps and folder icons take the accent color
}

# --- compositor (picom) -----------------------------------------------------------
# Smooth drawing (vsync) and fades. None turns it off.
compositor = {
    "vsync": True,                   # no tearing when scrolling or watching video
    "fade": 0.1,                     # seconds windows take to fade in and out; 0 = no fading
    "backend": "xrender",            # "xrender" (CPU) or "glx" (graphics card)
    "blur": 0,                       # blur behind see-through windows, 1-20 (needs "glx"); 0 = none
}

# --- notifications ---------------------------------------------------------------
# Shown on the bar, where the window title goes: low ones in the "norm" colors,
# normal ones in "sel", critical ones in "urgent". A click takes one away.

notification_timeout = 10    # seconds on the bar; critical ones stay until clicked

# --- screen lock (lock) -----------------------------------------------------------

lock_colors = {
    "init":   "black",       # after locking
    "input":  accent,        # while typing the password
    "failed": "#CC3333",     # after a wrong password
}

screen_off = None            # minutes idle before the screen turns off; None: never


# ============================================================================
#  Programs
#  The programs pytwm starts: keys, menus and autostart use these.
#  Each is a command: the program and its options in [brackets].
# ============================================================================

terminal         = ["alacritty"]   # term("prog") runs prog in it
editor           = ["nvim"]
browser          = default_browser   # or one, e.g. ["firefox"]
file_manager     = ["qfm"]
screenshot       = ["flameshot", "gui"]
sound_settings   = term("pulsemixer")
display_settings = term(os.path.join(os.path.dirname(pytwm_file), "monitors.sh"))
system_monitor   = term("btop")
package_manager  = ["yay"]         # or ["paru"]; counts the AUR updates
vpn_connect      = not_set_notify("VPN")             # e.g. ["protonvpn", "connect"]
vpn_disconnect   = not_set_notify("VPN disconnect")  # e.g. ["protonvpn", "disconnect"]
cloud_services   = [not_set_notify("Cloud services")]  # started at login, e.g. [["mega-cmd"], ["syncthing"]]


# ============================================================================
#  Keys
#  "keys": what to do (a command, a function, or (function, value)).
# ============================================================================

def keys():   # a function, so it uses the menus as they are after this file
    return {
        # --- your programs and menus --------------------------------------
        "super+t":               terminal,
        "super+e":               term(*editor),
        "super+f":               file_manager,
        "super+w":               browser,
        "Print":                 screenshot,
        "super+x":               launcher,               # the program launcher
        "super+s":               system_menu,
        "super+n":               notify_next,            # next notification
        "super+shift+l":         lock,
        "super+c":               random_accent,          # a random accent color

        # --- volume, microphone, brightness, display ------------------------
        "XF86AudioRaiseVolume":  (volume, +5),           # up to 100%
        "XF86AudioLowerVolume":  (volume, -5),
        "XF86AudioMute":         mute,
        "XF86AudioMicMute":      mic_mute,
        "XF86MonBrightnessUp":   (brightness, +5),       # laptop screen or monitor
        "XF86MonBrightnessDown": (brightness, -5),
        "XF86Display":           display_settings,

        # --- windows -------------------------------------------------------
        "super+Right":           (focusdir, "right"),    # focus the window to the right
        "super+Left":            (focusdir, "left"),
        "super+Down":            (focusdir, "down"),
        "super+Up":              (focusdir, "up"),
        "super+ctrl+Right":      (swapdir, "right"),     # move the window right (swap)
        "super+ctrl+Left":       (swapdir, "left"),
        "super+ctrl+Down":       (swapdir, "down"),
        "super+ctrl+Up":         (swapdir, "up"),
        "super+Return":          zoom,                   # swap with the master window
        "super+q":               killclient,             # close the window
        "super+shift+space":     togglefloating,         # float / tile the window
        "super+shift+i":         (incnmaster, +1),       # one more window in the master area
        "super+shift+d":         (incnmaster, -1),       # one less
        "super+shift+Right":     (setmfact, +0.01),      # wider master area
        "super+shift+Left":      (setmfact, -0.01),      # narrower
        "super+shift+Up":        (setcfact, +0.05),      # taller window, in its column
        "super+shift+Down":      (setcfact, -0.05),      # shorter
        "super+shift+t":         (setlayout, 0),         # tiled      (layouts() in parts.py)
        "super+shift+f":         (setlayout, 1),         # floating
        "super+shift+m":         (setlayout, 2),         # monocle: one window at a time
        "super+b":               togglebar,              # show / hide the bar

        # --- tags and monitors ---------------------------------------------
        "super+Tab":             view,                   # back to the tags shown before
        "super+0":               (view, ALLTAGS),        # show all tags
        "super+shift+0":         (tag, ALLTAGS),         # put the window on all tags
        "super+comma":           (focusmon, -1),         # focus the previous monitor
        "super+period":          (focusmon, +1),
        "super+shift+comma":     (tagmon, -1),           # move the window to the previous monitor
        "super+shift+period":    (tagmon, +1),
    }

# tag_keys+1..9 show tags, +shift moves the window, +ctrl / +ctrl+shift add a tag
tag_keys = "super"


# ============================================================================
#  Tags and rules
#  Tag names, and what to do with windows when they open.
# ============================================================================

tags = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]

# "class", "title: ...", "instance: ...": {"tag": 9, "float": True, "monitor": 1}
rules = {
    "Example1":                   {"float": True},
    "Example2":                   {"tag": 9},
}


# ============================================================================
#  Layouts
#  How windows are arranged; each tag remembers its own. The layouts
#  themselves, layouts(), are in parts.py.
# ============================================================================

mfact = 0.55          # factor of master area size [0.05..0.95]
nmaster = 1           # number of clients in master area


# ============================================================================
#  Autostart
#  Programs started once at login (only on session_display, in parts.py).
# ============================================================================

battery_low = 10              # percent: battery_watch warns at or below this, unplugged

def autostart():
    start_services()             # polkit agent and the watchers, in parts.py
    launch(terminal)

    wait_for_network()           # the apps below need it
    launch(vpn_connect)
    wait_for_vpn()               # and these need the VPN
    for cmd in cloud_services:
        launch(cmd)
