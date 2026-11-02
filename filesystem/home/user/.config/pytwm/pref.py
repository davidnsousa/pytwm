"""
pytwm preferences: appearance, programs, keys, rules, layouts, autostart,
what the status bar shows and the system menu. The code behind the status
parts, the submenus and the watchers is in parts.py, next to this file.

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
import time

# ============================================================================
#  Appearance
#  Colors and fonts, the bar, menus, GTK apps, notifications, screen lock.
# ============================================================================

fonts = ["monospace:size=9", "JetBrainsMono Nerd Font Propo:size=9"]  # first font is the main one, the rest are fallbacks (the Nerd Font has the bar's icons)
dpi = None                    # text size: None is the normal size (96); 192 doubles it (QHD/4K)

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
snap = 32                     # snap pixel (move/resize with the mouse)
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
menu_case_insensitive = True
menu_worddelimiters = " "    # characters that separate words for Ctrl+w

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
lock_failonclear = True      # show "failed" when the input is cleared

screen_off = None            # minutes idle before the screen turns off; None: never


# ============================================================================
#  Programs
#  The programs pytwm starts: keys, menus and autostart use these.
#  Each is a command: the program and its options in [brackets].
# ============================================================================

terminal         = ["alacritty"]   # term("prog") runs prog in it
editor           = ["nvim"]
browser          = not_set_notify("Web browser")     # e.g. ["firefox"]
file_manager     = ["qfm"]
screenshot       = ["flameshot", "gui"]
sound_settings   = term("pulsemixer")
display_settings = term(os.path.join(os.path.dirname(pytwm_file), "monitors.sh"))
system_monitor   = term("btop")
package_manager  = ["yay"]         # or ["paru"]; checks for updates
vpn_connect      = not_set_notify("VPN")             # e.g. ["protonvpn", "connect"]
vpn_disconnect   = not_set_notify("VPN disconnect")  # e.g. ["protonvpn", "disconnect"]
cloud_services   = [not_set_notify("Cloud services")]  # started at login, e.g. [["mega-cmd"], ["syncthing"]]


# ============================================================================
#  Keys
#  "keys": what to do (a command, a function, or (function, value)).
# ============================================================================

def keys():   # a function, so it can use the menus defined further down
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
        "super+shift+t":         (setlayout, 0),         # tiled      (layouts, below)
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

# Mouse buttons: "place: keys+button" (places: tags, layout, title, status, window, desktop)
buttons = {
    "layout: button1":        setlayout,                 # back to the last layout
    "layout: button3":        (setlayout, 2),
    "title: button2":         zoom,
    "status: button2":        terminal,
    "window: super+button1":  movemouse,                 # drag to move
    "window: super+button2":  togglefloating,
    "window: super+button3":  resizemouse,               # drag to resize
    "tags: button1":          view,
    "tags: button3":          toggleview,
    "tags: super+button1":    tag,
    "tags: super+button3":    toggletag,
}


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
#  How windows are arranged; each tag remembers its own.
# ============================================================================

mfact = 0.55          # factor of master area size [0.05..0.95]
nmaster = 1           # number of clients in master area
resizehints = True    # True means respect size hints in tiled resizals
lockfullscreen = True # True will force focus on the fullscreen window

def layouts():
    """Index 0 is the default. A layout of None means floating."""
    return [
        # symbol  arrange function
        ("[]=",   tile),
        ("><>",   None),
        ("[M]",   monocle),
    ]


# ============================================================================
#  Autostart
#  Programs started once at login (only on session_display).
# ============================================================================

session_display = ":0"        # the display your session runs on (see: echo $DISPLAY)
battery_low = 10              # percent: battery_watch warns at or below this, unplugged

def autostart():
    launch(["/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1"])   # asks for your password for admin tasks
    launch(terminal)
    in_background(battery_watch)
    in_background(updates_check)
    in_background(pytwm_updates_check)

    wait_for_network()           # the apps below need it
    launch(vpn_connect)
    wait_for_vpn()               # and these need the VPN
    for cmd in cloud_services:
        launch(cmd)


# ============================================================================
#  Status
#  status() returns the text on the right of the bar.
# ============================================================================

status_interval = 1           # seconds

# Icons from the Nerd Font in fonts; https://www.nerdfonts.com/cheat-sheet has more.
icons = {
    "sound": "\uf028",          # volume
    "cpu": "\uf4bc",            # chip
    "ram": "\U000f0479",        # memory card
    "battery": "\U000f0079",
    "charging": "\U000f0084",   # battery with a plug
    "bluetooth": "\uf293",
    "ethernet": "\U000f0200",
    "wifi": "\uf1eb",
    "vpn": "\U000f0498",        # shield
    "date": "\U000f00ed",       # calendar
    "notifications": "\uf0f3",  # bell
}
off_color = "#ff5555"         # a part that is off: muted, disconnected, paused

def status():
    parts = [notifications_status(), sound_status(), cpu_status(), ram_status()]
    if battery():                                   # laptops only
        parts.append(battery_status())
    if has_bluetooth():
        parts.append(every(5, bluetooth_status))
    parts += [every(5, network_status), every(5, vpn_status), part("date", time.strftime("%H:%M, %a, %b %d "))]
    return " " + "  ".join(parts)


# ============================================================================
#  Menus
#  The system menu, system_menu (its submenus are in parts.py).
# ============================================================================

HOME = os.path.expanduser("~")
apps_installer = os.path.join(os.path.dirname(pytwm_file), "install_apps.sh")
doc_file = os.path.join(os.path.dirname(pytwm_file), "doc.md")

config_files = [
    prefs_file, parts_file,
    HOME + "/.xinitrc", HOME + "/.bashrc",
    HOME + "/.config/nvim/init.lua",
    HOME + "/.config/alacritty/alacritty.toml",
]


def system_menu(arg):
    """The system menu"""
    choose({
        "Toggle notifications": toggle_notifications,
        "Network":              network_menu,
        "Sound":                lambda: launch(sound_settings),
        "Display":              lambda: launch(display_settings),
        "System monitor":       lambda: launch(system_monitor),
        "Search files":         search_menu,
        "Printers & scanners":  printers_menu if shutil.which("lpstat") else None,   # CUPS installed
        "Configuration files":  lambda: launch(term(*editor, *config_files)),
        "Apps":                 lambda: launch(term(apps_installer)),
        "Shortcuts":            keys_menu,                # the keys and buttons set now
        "Help":                 lambda: launch(term("leaf", "--theme", "catppuccin-mocha", doc_file)),
        "Exit":                 exit_menu,
    }, prompt="System:")


def exit_menu():
    choose({
        "Lock":     lambda: lock(None),
        "Log out":  lambda: wm(quit),         # pytwm ends, so X ends and you're logged out
        "Reboot":   lambda: launch(["reboot"]),
        "Shutdown": lambda: launch(["shutdown", "now"]),
    }, prompt="Exit:")


# ============================================================================
#  pytwm
# ============================================================================

autoreload = True     # restart in place when pytwm.py, this file or parts.py is saved
