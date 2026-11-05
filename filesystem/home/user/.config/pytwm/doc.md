# pytwm

pytwm is a tiling window manager for X11, written in Python, with a program launcher, status bar, screen lock and notifications.

| File | What it is |
|---|---|
| `~/.config/pytwm/pref.py` | your settings |
| `~/.config/pytwm/parts.py` | the code behind the status bar, submenus and watchers |
| `~/.config/pytwm/pytwm.py` | the window manager |
| `~/.config/pytwm/doc.md` | this document (system menu → Help) |
| `~/.config/pytwm/install_apps.sh` | optional apps to install (system menu → Apps) |
| `~/.config/pytwm/monitors.sh` | monitor layout: resolution, position, on / off (system menu → Display) |
| `~/.config/pytwm/requirements.txt` | Python packages, installed in `~/.local/share/pytwm/venv` |

Saving `pref.py`, `parts.py` or `pytwm.py` restarts pytwm in place. Windows stay where they are.

Keys are set in `keys()` in `pref.py`, so this document names functions, not keys. System menu → Shortcuts lists your current keys (the system menu is Super+s by default).

## Contents

- [Editing pref.py](#editing-prefpy)
- [Appearance](#appearance)
- [Programs](#programs)
- [Keys](#keys)
- [Tags and rules](#tags-and-rules)
- [Layouts](#layouts)
- [Autostart](#autostart)
- [Status](#status)
- [Menus](#menus)
- [Watchers](#watchers)
- [Functions for pref.py](#functions-for-prefpy)
- [What happens when you save](#what-happens-when-you-save)
- [Testing in a window](#testing-in-a-window)
- [How it runs](#how-it-runs)
- [Updating](#updating)

## Editing pref.py

`pref.py` is plain Python. Its sections: Appearance, Programs, Keys, Tags and rules, Layouts, Autostart, Status, Menus.

`parts.py` is loaded first, into the same namespace: `pref.py` can use its functions and replace them by defining one with the same name.

pytwm's functions can be used in `pref.py` without an import. If a setting has the same name as one of them, the bar tells you to rename it.

System menu → Configuration files opens `pref.py`, `parts.py` and other config files.

## Appearance

### Colors and fonts

| Setting | Used for |
|---|---|
| `background` | bar and menu background |
| `border` | border of unfocused windows |
| `text` | bar and menu text |
| `text_active` | text on the selected tag, focused title and selected menu item |
| `accent` | selected tag, focused title and border, lock while typing |
| `colors` | `"norm"`, `"sel"` and `"urgent"`, each `(text, background, border)` |
| `fonts` | e.g. `["monospace:size=9"]`; the first is the main font, the rest are fallbacks |
| `dpi` | size of text and GTK apps: `"auto"` works it out from the monitors, `96` is normal, `192` doubles it, `None` leaves it to `~/.Xresources` |

Colors are `"#rrggbb"`. Fonts are `"family:size=N"`.

GTK apps scale their widgets, icons and cursor by the whole number nearest `dpi / 96` (2 at 192), so they keep their proportions; the text follows `dpi` exactly (`168` gives 2× widgets with 1.75× text).

With `dpi = "auto"`, each monitor that is on gets a scale of 1, 2 or 3 from its pixel density (a laptop panel aims a little denser, as it is seen from closer). X has one size for apps on all monitors, so the largest scale is used, and xrandr draws the other monitors larger (`--scale 2x2` on a plain monitor next to a 2× laptop) so apps look the same size on every monitor, a little softer on those. When monitors are turned on or off and the scale changes, pytwm restarts in place at the new size; apps already open keep their size until you start them again.

### Bar and windows

| Setting | What it does |
|---|---|
| `borderpx` | border width in pixels |
| `gaps` | pixels between windows and around the screen edge |
| `snap` | distance at which a dragged window snaps to the edge |
| `showbar` | `False` hides the bar |
| `topbar` | `False` puts the bar at the bottom |
| `bar_title` | `True` shows the focused window's title in the middle of the bar |
| `hide_vacant_tags` | `True` shows only tags with windows and the ones in view |

### Wallpaper

```python
wallpaper = "~/Pictures/mountains.jpg"    # a picture, or a color like "#222222"; None: not set
wallpaper_mode = "fill"                   # fill, fit, stretch, center or tile
```

`fill` covers the screen and crops; `fit` shows the whole picture; `stretch` distorts it to fit; `center` uses its own size; `tile` repeats it.

### Menus

Menus use `fonts` and `colors`, unless you set `menufont` or `menu_colors = {"norm": (fg, bg), "sel": (fg, bg), "out": (fg, bg)}`.

| Setting | What it does |
|---|---|
| `menu_lines` | `0`: one line; `10`: a list of 10 items |
| `menu_wide` | `False`: menus open on the bar, where notifications show; `True`: across the screen |
| `menu_topbar` | `False` puts wide menus at the bottom |
| `menu_prompt` | text before the launcher's list (`"Exec:"`); `None` for none |
| `menu_case_insensitive` | `True` ignores case when filtering; `False` matches case (the launcher always ignores it) |
| `menu_worddelimiters` | characters that separate words for Ctrl+w |

Keys in a menu:

| Key | What it does |
|---|---|
| typing | filters the list; the typed words are highlighted in the items (shown alone when nothing matches) |
| arrows | previous / next item |
| Tab | completes with the selected item |
| Enter | chooses |
| Shift+Enter | uses the typed text |
| Ctrl+Enter | chooses and keeps the menu open |
| Escape | closes |
| Ctrl+a / Ctrl+e | start / end of the line |
| Ctrl+u / Ctrl+k | delete left / right |
| Ctrl+w | delete a word |
| Ctrl+y | paste |

Dead keys and compose don't work in menus.

### GTK apps

```python
gtk = {
    "theme": "adw-gtk3-dark",
    "icon_theme": "Papirus-Dark",
    "cursor_theme": None,
    "font": "DejaVu Sans 10",
    "dark": True,      # dark mode
    "accent": True,    # apps and folder icons use the accent color
}
```

`None` leaves a setting unchanged. Open GTK apps update when you save. pytwm also writes these settings to the GTK settings files and gsettings, for apps started later. libadwaita apps follow only `dark` and the accent.

Old GTK 2 apps get Adwaita's GTK 2 look (package gnome-themes-extra-gtk2) when the theme has no GTK 2 version, like adw-gtk3.

### Compositor

```python
compositor = {
    "vsync": True,          # no tearing
    "fade": 0.1,            # fade time in seconds; 0: none
    "backend": "xrender",   # "xrender" (CPU) or "glx" (graphics card)
    "blur": 0,              # blur behind see-through windows, 1-20; needs "glx"
}
```

pytwm runs picom with these settings. `compositor = None` turns it off. Use `"xrender"` if `"glx"` doesn't work, e.g. in a virtual machine. A see-through terminal needs the compositor.

### Notifications

Notifications show in the middle of the bar, one at a time. `(+2)` means two more are waiting. Critical ones come first and stay until dismissed. pytwm's own errors show this way too.

```python
notification_timeout = 10    # seconds
```

Clicking a notification dismisses it and runs its default action, e.g. opens the browser tab it came from. `notify_next` only dismisses it.

"Toggle notifications", in the system menu, pauses them: they wait until turned back on. Their sounds are muted too, for the apps that send them.

### Screen lock

`lock` locks the screen. Type your password and press Enter; Escape clears the input.

| Setting | What it does |
|---|---|
| `lock_colors` | screen color: `"init"`, `"input"` while typing, `"failed"` after a wrong password |
| `lock_failonclear` | `True` shows `"failed"` when the input is cleared |

### Screen off

`screen_off = 10` turns the screen off after 10 idle minutes. `None` keeps it on.

## Programs

Each program is a command: the program and its options, e.g. `["flameshot", "gui"]`.

| Setting | Used by |
|---|---|
| `terminal` | its key, and `term()` |
| `editor` | its key, and system menu → Configuration files |
| `browser`, `file_manager`, `screenshot` | their keys; `browser` also opens system menu → Printers & scanners → Printers |
| `sound_settings`, `display_settings`, `system_monitor` | system menu |
| `package_manager` | the updates check |
| `vpn_connect`, `vpn_disconnect` | autostart, and system menu → Network → Toggle VPN |
| `cloud_services` | commands started at login once the network is up, e.g. `[["syncthing"]]` |

`not_set_notify("Web browser")` marks a program as not set: using it shows a notification.

## Keys

```python
def keys():
    return {
        "super+w":       ["vivaldi"],            # run a command
        "super+e":       term("nvim"),           # run a program in the terminal
        "XF86AudioMute": sh("pactl set-sink-mute @DEFAULT_SINK@ toggle"),   # a shell line
        "super+shift+l": lock,                   # call a function
        "super+Right":   (focusdir, "right"),    # call a function with a value
    }
```

Modifiers: `super`, `shift`, `ctrl`, `alt`. Key names are X key names; run `xev -event keyboard` to see them.

### Functions for keys

| Function | What it does |
|---|---|
| `launcher` | program launcher: the installed apps; anything else typed runs as a command |
| `lock` | lock the screen |
| `(volume, +5)` | volume up 5% (`-5`: down) |
| `mute`, `mic_mute` | mute / unmute sound or microphone |
| `(brightness, +5)` | brightness up 5% (`-5`: down) |
| `notify_next` | dismiss the notification |
| `random_accent` | new random accent color, saved in `pref.py` |
| `reload` | restart pytwm |
| `quit` | quit pytwm and log out |
| `killclient` | close the window |
| `zoom` | swap the window with the master |
| `togglefloating` | float / tile the window |
| `togglebar` | show / hide the bar |
| `(focusdir, "left")` | focus the window to the left (`"right"`, `"up"`, `"down"`) |
| `(swapdir, "left")` | swap the window with the one to the left |
| `(focusstack, +1)` | focus the next window (`-1`: previous) |
| `(incnmaster, +1)` | one more window in the master area (`-1`: fewer) |
| `(setmfact, +0.01)` | wider master area (`-0.01`: narrower) |
| `(setcfact, +0.05)` | taller window (`-0.05`: shorter, `0`: even) |
| `(setlayout, n)` | layout `n` of `layouts()`; `setlayout` alone: previous layout |
| `view` | previous tags |
| `(view, tagmask(3))` | show tag 3 (`ALLTAGS`: all) |
| `(tag, tagmask(3))` | move the window to tag 3 (`ALLTAGS`: all) |
| `(toggleview, tagmask(3))` | show / hide tag 3 |
| `(toggletag, tagmask(3))` | add / remove the window on tag 3 |
| `(focusmon, +1)` | focus the next monitor (`-1`: previous) |
| `(tagmon, +1)` | move the window to the next monitor (`-1`: previous) |
| `movemouse`, `resizemouse` | move / resize with the mouse |

### Tag keys

`tag_keys = "super"` adds keys for tags 1 to 9: modifier+n shows tag n, +shift moves the window there, +ctrl toggles tag n in view, +ctrl+shift toggles the window on tag n.

### Mouse buttons

```python
buttons = {
    "window: super+button1":  movemouse,
    "tags: button1":          view,
}
```

Places: `tags`, `layout`, `title`, `status`, `window`, `desktop`. Buttons: `button1` left, `button2` middle, `button3` right. On `tags`, a function without a value gets the clicked tag.

## Tags and rules

`tags` is the list of tag names; the first 9 get keys.

`rules` place windows when they open:

```python
rules = {
    "Gimp":                       {"float": True},       # by class
    "Firefox":                    {"tag": 9},
    "title: Picture-in-Picture":  {"float": True},       # by title
    "instance: scratch":          {"tag": [1, 2], "monitor": 1},
}
```

A rule matches when the class, title or instance contains the text (case-sensitive). Every matching rule applies. `xprop | grep WM_CLASS` shows a window's instance and class.

## Layouts

| Setting | What it does |
|---|---|
| `mfact` | master area's share of the screen, `0.05` to `0.95` |
| `nmaster` | number of windows in the master area |
| `resizehints` | `True`: tiled windows keep the sizes they ask for |
| `lockfullscreen` | `True`: focus stays on a fullscreen window |
| `layouts()` | the layouts as `(symbol, function)`; the first is the default |

Layout functions: `tile`, `monocle` (one window at a time) and `None` (floating). Each tag keeps its own layout.

Tiled windows are told they are maximized, so apps that draw their own title bar and shadow (ONLYOFFICE, some GTK and Electron apps) fill their tile. Floating windows keep their own frame.

## Autostart

`autostart()` runs once at login, on `session_display` only (not in Xephyr, not on reload). By default it starts the polkit agent, a terminal, the watchers and the VPN, and once the VPN is on, `cloud_services`.

## Status

`status()` returns the text on the right of the bar, every `status_interval` seconds. The default shows volume, CPU, memory, Bluetooth, network, VPN, notifications and time. Each part is a function in `parts.py`. The icons in front of each part are in `icons`, in `pref.py`; they come from the Nerd Font in `fonts`. A part that is off (muted, disconnected, paused) is drawn in `off_color`. `colored(text, "#rrggbb")` draws any text on the bar in another color.

Without `status()`, the bar shows the root window name (set with `xsetroot -name`).

## Menus

The system menu is `system_menu` in `pref.py`; its submenus are in `parts.py`. Write your own with `choose()` or `menu()`:

```python
def power_menu(arg):
    choose({
        "Lock":    lambda: lock(None),
        "Suspend": lambda: launch(["systemctl", "suspend"]),
    }, prompt="Power:")

# in keys():
"super+p": power_menu,
```

Your functions bound to keys run in the background, so a slow command doesn't freeze the windows. Errors show in the bar.

`config_files` is the list system menu → Configuration files opens.

System menu → Printers & scanners shows only when CUPS is installed (`setup.sh` offers it). Printers opens CUPS in your browser, to add and manage printers; it asks for your login password. Scan opens xsane.

## Watchers

Loops in `parts.py`, started from `autostart()`:

- `battery_watch()`: notifies when the charger is plugged in or out, and warns when the battery is at or below `battery_low` percent.
- `updates_check()`: notifies when package updates are available.
- `pytwm_updates_check()`: notifies when there is a newer version of pytwm on GitHub (see [Updating](#updating)).

## Functions for pref.py

A command is a list (`["prog", "arg"]`) or a shell line (`"prog | prog"`).

| Function | What it does |
|---|---|
| `launch(cmd, tag=None)` | start a command; with `tag`, its windows open on that tag, e.g. `launch(terminal, 2)` |
| `output(cmd, timeout=5)` | run a command and return its output (`""` after the timeout) |
| `sh("cmd")` | a shell line as a command |
| `term("prog")` | a command that runs `prog` in the terminal |
| `notify(text, ms=None, urgency=None)` | show a notification; `urgency`: `"low"`, `"normal"` or `"critical"` |
| `menu(items, prompt=None, lines=None)` | show a menu; returns the item picked, or `None` |
| `choose({"text": action}, prompt=None)` | show a menu and run the action picked |
| `in_background(func, *args)` | run a function in the background |
| `watch(cmd, func)` | call `func(line)` for each line a command prints; start it with `in_background` |
| `wait_for_network()` | wait until the internet is reachable |
| `wait_for_vpn()` | wait until a VPN is active; notifies once after a minute |
| `every(seconds, func)` | `func()`'s result, recomputed at most every `seconds` |
| `update_status()` | update the status now |
| `wm(func, value)` | call a window function from a menu, e.g. `wm(view, tagmask(3))` |
| `lock(None)` | lock the screen |
| `pause_notifications(True)` | pause notifications and their sounds (`False`: resume) |
| `notifications_paused()` | `True` while paused |
| `tagmask(1, 2)` | the tags with these numbers |
| `not_set_notify("name")` | a placeholder for a program not set |

`launch(cmd, tag)` doesn't place windows of a program that hands them to a copy already running, e.g. a second browser window.

## What happens when you save

1. pytwm checks that the files compile. If not, nothing changes and the error shows in the bar.
2. Otherwise it restarts with the new code. Windows don't move.
3. If the new version fails to start, pytwm goes back to the previous one and shows the error.

If a file is broken at login, pytwm exits. Test big changes in Xephyr first. `autoreload = False` turns off restarting on save.

## Testing in a window

With `xorg-server-xephyr` installed:

```sh
Xephyr :5 -screen 1280x720 -ac &
DISPLAY=:5 ~/.local/share/pytwm/venv/bin/python ~/.config/pytwm/pytwm.py &
```

Press Ctrl+Shift in the Xephyr window to send your keys to it; again to release them.

## How it runs

- `~/.xinitrc` starts pytwm. Errors go to `~/.local/state/pytwm/pytwm.log` (previous session: `pytwm.log.old`).
- `pytwm.py keys` prints your keys in a terminal; `--watch` reprints them when you save.
- Images open in nsxiv. Removable drives are mounted by udiskie.
- Saved passwords are kept in gnome-keyring, unlocked at login. Electron apps use it through `~/.config/electron-flags.conf`.
- Bash gets its prompt, history search (Up/Down), Alt+Left/Right and `calc` from `~/.config/pytwm/bashrc_extras`. `~/.bashrc` stays yours; `setup.sh` only adds the line that reads that file.
- After adding a Python package: `~/.local/share/pytwm/venv/bin/pip freeze > ~/.config/pytwm/requirements.txt`.

## Updating

At login, `pytwm_updates_check()` notifies you when there is a newer version of pytwm on GitHub. To update, get it into the copy of the repository you installed from and run `setup.sh` again:

```sh
cd pytwm
git pull
bash setup.sh
```

`setup.sh` replaces all files except `pref.py`: if yours differs, it offers to show the differences and asks which to keep.

`setup.sh` saves the date of the commit it installed in `~/.local/state/pytwm/installed`; the check compares it with the latest commit on GitHub. Your own commits don't count as updates, pushed or not. Without that file (pytwm not installed by `setup.sh` from a git clone) there is no check.
