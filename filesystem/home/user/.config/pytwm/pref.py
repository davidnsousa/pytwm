"""
pytwm preferences: appearance, programs, keys, rules, layouts and autostart.
The status bar, the menus, the layouts, the window keys and the settings that
rarely change are in parts.py, next to this file; to change one, copy it here
and edit it.

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
#  Colors and fonts, wallpaper, GTK apps, the screen.
# ============================================================================

fonts = ["monospace:size=9", "JetBrainsMono Nerd Font Propo:size=9"]  # first font is the main one, the rest are fallbacks (the Nerd Font has the bar's icons)
dpi = "auto"                  # size of text and GTK apps: "auto" from the monitors; 96 normal, 192 doubles it; None leaves it to ~/.Xresources

# Colors are "#rrggbb" strings.
background  = "#222222"       # bar and menu background
border      = "#444444"       # border of unfocused windows
text        = "#bbbbbb"       # bar and menu text
text_active = "#eeeeee"       # text on the selected tag, focused title, selected menu item
accent      = "#005577"       # selected tag, focused title and border, lock while typing

# --- wallpaper ---------------------------------------------------------------------
wallpaper = "#000000"         # a color, or a picture, e.g. "~/Pictures/mountains.jpg"
wallpaper_mode = "fill"       # fill (covers the screen), fit (all of it), stretch, center or tile

# --- apps (GTK) ------------------------------------------------------------------
gtk = {
    "theme": "adw-gtk3-dark",        # a folder in /usr/share/themes or ~/.themes
    "icon_theme": "Papirus-Dark",    # a folder in /usr/share/icons or ~/.icons
    "cursor_theme": None,            # e.g. "Adwaita"
    "font": "DejaVu Sans 10",        # "Family Size"
    "dark": True,                    # dark mode, for apps that only know light or dark
    "accent": True,                  # apps and folder icons take the accent color
}

# --- screen ---------------------------------------------------------------------
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
#  The keys for windows, layouts, tags and monitors are window_keys() in
#  parts.py; a key here replaces one there, and None takes it away.
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
#  Autostart
#  Programs started once at login (only on session_display, in parts.py).
# ============================================================================

def autostart():
    start_services()             # the polkit agent, in parts.py
    launch(terminal)

    wait_for_network()           # the apps below need it
    launch(vpn_connect)
    wait_for_vpn()               # and these need the VPN
    for cmd in cloud_services:
        launch(cmd)
