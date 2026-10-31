"""
pytwm parts: the code behind the status bar, the menus and the watchers that
pref.py uses. It is loaded just before pref.py, into the same namespace, so
pref.py can use everything here and these functions see pref.py's settings
(terminal, vpn_connect, battery_low, ...). A function of the same name in
pref.py replaces the one here. Saving this file applies it, like pref.py.

Everything pytwm offers is described in doc.md, next to this file.
"""

# ============================================================================
#  Status
#  The parts status() in pref.py puts on the bar.
# ============================================================================

def plain(cmd, timeout=5):
    """output(), with the program's messages untranslated (English), so they
    can be read the same way in any language."""
    if isinstance(cmd, str):
        return output("LC_ALL=C.UTF-8 " + cmd, timeout)
    return output(["env", "LC_ALL=C.UTF-8", *cmd], timeout)


def part(icon, text="", on=True):
    """A part of the status: the icon named icon in icons, then text; all of it
    in off_color when it is not on."""
    s = icons[icon] + (" " + text if text else "")
    return s if on else colored(s, off_color)


_sound = {"text": None, "watching": False}

def sound_status():
    """The volume ("30%"), or the icon in off_color when muted. pactl is asked
    only after sound_changed() reports a change (volume keys, pulsemixer,
    headphones), not every second."""
    if not _sound["watching"]:
        _sound["watching"] = True
        in_background(watch, ["pactl", "subscribe"], sound_changed)
    if _sound["text"] is None:
        if plain("pactl get-sink-mute @DEFAULT_SINK@").split()[1:2] == ["yes"]:
            _sound["text"] = part("sound", on=False)
        else:
            levels = [w for w in plain("pactl get-sink-volume @DEFAULT_SINK@").split() if w.endswith("%")]
            _sound["text"] = part("sound", levels[0] if levels else "")
    return _sound["text"]


def sound_changed(line):
    if " on sink #" in line or " on server" in line:    # the output, or which one is used
        _sound["text"] = None
        update_status()


_cpu_last = [0, 0]

def cpu_status():
    """CPU use since the last call, from /proc/stat."""
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    idle, total = v[3] + v[4], sum(v[:8])
    dt, di = total - _cpu_last[0], idle - _cpu_last[1]
    _cpu_last[:] = [total, idle]
    return part("cpu", "%d%%" % (100 * (dt - di) // dt if dt else 0))


def ram_status():
    """Memory in use, from /proc/meminfo (like free: total minus available)."""
    m = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0])
    return part("ram", "%d%%" % ((m["MemTotal"] - m["MemAvailable"]) * 100 // m["MemTotal"]))


def battery_status():
    """The battery's charge, with a plug icon while it charges."""
    bat = battery()
    charging = battery_read(bat, "status") == "Charging"
    return part("charging" if charging else "battery", battery_read(bat, "capacity") + "%")


def has_bluetooth():
    """True when the machine has a Bluetooth adapter."""
    return os.path.isdir("/sys/class/bluetooth") and bool(os.listdir("/sys/class/bluetooth"))


def bluetooth_on():
    return "Powered: yes" in plain("bluetoothctl show")


def bluetooth_status():
    """The connected device's name; in off_color when Bluetooth is off."""
    if not bluetooth_on():
        return part("bluetooth", on=False)
    names = [l.split(" ", 2)[2] for l in plain("bluetoothctl devices Connected").splitlines()
             if l.startswith("Device ") and l.count(" ") >= 2]
    return part("bluetooth", names[0] if names else "")


def ip_address(dev):
    for l in plain(["ip", "-4", "address", "show", "dev", dev]).splitlines():
        if "inet " in l and "127.0.0.1" not in l:
            return l.split()[1].split("/")[0]
    return ""


def nmcli_rows(fields, *args, timeout=5):
    """nmcli's script-friendly output (-t) as rows of fields: nmcli_rows("TYPE,DEVICE",
    "connection", "show", "--active") -> [["802-3-ethernet", "eno1"], ...]"""
    rows = []
    for line in plain(["nmcli", "-t", "-f", fields, *args], timeout).splitlines():
        cells = line.replace("\\:", "\0").split(":")       # "\:" is a ":" inside a value
        rows.append([c.replace("\0", ":") for c in cells])
    return rows


def network_status():
    """The cable or Wi-Fi icon, with details when connected; in off_color when not."""
    active = nmcli_rows("TYPE,DEVICE", "connection", "show", "--active")
    eth = [dev for kind, dev in active if kind == "802-3-ethernet"]
    if eth:
        return part("ethernet", ip_address(eth[0]))
    wifi = [dev for kind, dev in active if kind == "802-11-wireless"]
    if wifi:
        # --rescan no: only what NetworkManager already knows, no new scan
        used = [r for r in nmcli_rows("IN-USE,SIGNAL,SSID", "device", "wifi", "list", "--rescan", "no")
                if r[0] == "*"]
        signal, ssid = used[0][1:3] if used else ("", "")
        return part("wifi", "%s%% %s %s" % (signal, ssid, ip_address(wifi[0])))
    return part("wifi" if wifi_device() else "ethernet", on=False)


def vpn_names():
    """The active VPN connections' names in NetworkManager (any VPN it runs:
    OpenVPN, WireGuard, Proton, ...)."""
    return [name for name, kind in nmcli_rows("NAME,TYPE", "connection", "show", "--active")
            if kind.startswith(("tun", "wireguard", "vpn"))]


def vpn_active():
    return bool(vpn_names())


def vpn_status():
    """The VPN's name without a leading "...VPN" word ("ProtonVPN PT#79" -> "PT#79");
    in off_color when there is none."""
    names = vpn_names()
    if not names:
        return part("vpn", on=False)
    words = names[0].split()
    if len(words) > 1 and "vpn" in words[0].lower():
        words = words[1:]
    return part("vpn", " ".join(words))


def notifications_status():
    return part("notifications", on=not notifications_paused())


# ============================================================================
#  Menus
#  The submenus of the system menu (system_menu in pref.py).
# ============================================================================

def toggle_notifications():
    if notifications_paused():
        pause_notifications(False)
        notify("Notifications on")
    else:
        notify("Notifications off", 1000)
        time.sleep(1)
        pause_notifications(True)


def wifi_device():
    """The Wi-Fi device (e.g. "wlan0"), or None when the machine has none."""
    return next((dev for dev, kind in nmcli_rows("DEVICE,TYPE", "device") if kind == "wifi"), None)


def network_menu():
    wifi = wifi_device()            # the Wi-Fi entries only when there is Wi-Fi
    choose({
        "List networks":              wifi_menu if wifi else None,
        "Connect/Disconnect network": (lambda: toggle_wifi_connection(wifi)) if wifi else None,
        "Enable/Disable wifi":        toggle_wifi if wifi else None,
        "Enable/Disable Networking":  toggle_networking,
        "Toggle VPN":                 toggle_vpn,
        "Bluetooth":                  bluetooth_menu if has_bluetooth() else None,
    }, prompt="Network:")


def toggle_wifi_connection(dev):
    state = dict(nmcli_rows("DEVICE,STATE", "device")).get(dev, "")
    output(["nmcli", "device", "disconnect" if state == "connected" else "connect", dev])


def toggle_wifi():
    on = plain("nmcli radio wifi").strip() == "enabled"
    output(["nmcli", "radio", "wifi", "off" if on else "on"])


def toggle_networking():
    on = plain("nmcli networking").strip() == "enabled"
    output(["nmcli", "networking", "off" if on else "on"])


def wifi_menu():
    """The networks in range, each once, strongest first."""
    # nmcli scans again when its last scan is 30 s old, which can take over 5 s
    rows = nmcli_rows("SIGNAL,SSID", "device", "wifi", "list", timeout=20)
    ssids = []
    for signal, ssid in sorted(rows, key=lambda r: -int(r[0]) if r[0].isdigit() else 0):
        if ssid and ssid not in ssids:
            ssids.append(ssid)
    ssid = menu(ssids, prompt="Networks:")
    if ssid:
        launch(term("nmcli", "device", "wifi", "connect", ssid, "--ask"))


def bluetooth_menu():
    devices = [l.split(" ", 1)[1] for l in plain("bluetoothctl devices").splitlines()
               if l.startswith("Device ")]
    choice = menu(devices + ["bluetoothctl", "Toggle"], prompt="Bluetooth:")
    if choice == "bluetoothctl":
        launch(term("bluetoothctl"))
    elif choice == "Toggle":
        output(["bluetoothctl", "power", "off" if bluetooth_on() else "on"])
    elif choice:
        mac, _, name = choice.partition(" ")
        if "Connection successful" in plain(["bluetoothctl", "connect", mac], timeout=20):
            notify("Connected to " + name)
        else:
            notify("Could not connect to " + name)


def toggle_vpn():
    if vpn_active():
        output(vpn_disconnect)
        notify("VPN off")
    else:
        output(vpn_connect)
        notify("VPN on")


def printers_menu():
    choose({
        "Printers": open_cups,
        "Scan":     (lambda: launch(["xsane"])) if shutil.which("xsane") else None,
    }, prompt="Printers & scanners:")


def open_cups():
    """CUPS's page, in your browser: add and manage printers there."""
    url = "http://localhost:631"
    if callable(browser):           # not set yet: it says so
        browser()
    else:
        launch([*browser, url])


def keys_menu():
    """Your keys, in a terminal (pytwm.py keys): scroll, / to search, q to close."""
    launch(term(sys.executable, pytwm_file, "keys", "--pager"))


def search_menu():
    """Every file in your home, as a path from there; hidden folders are skipped."""
    files = []
    for folder, subfolders, names in os.walk(HOME):
        subfolders[:] = sorted(s for s in subfolders if not s.startswith("."))
        rel = os.path.relpath(folder, HOME)
        files += [os.path.normpath(os.path.join(rel, n)) for n in sorted(names)]
    chosen = menu(files, prompt="Search ~:")
    if chosen and os.path.exists(os.path.join(HOME, chosen)):
        launch(["xdg-open", os.path.join(HOME, chosen)])


# ============================================================================
#  Watchers
#  Loops that send a notification when something happens.
# ============================================================================

power_supply = "/sys/class/power_supply"


def battery():
    """The battery's folder, or None on a machine without one."""
    if os.path.isdir(power_supply):
        for name in sorted(os.listdir(power_supply)):
            if name.startswith("BAT"):
                return os.path.join(power_supply, name)
    return None


def battery_read(bat, what):
    with open(os.path.join(bat, what)) as f:
        return f.read().strip()


_battery = {"path": None, "status": None}

def battery_watch():
    """Says when the charger is plugged in or out (Linux reports it, through
    udevadm monitor) and warns about a low battery (checked every minute)."""
    bat = _battery["path"] = battery()
    if not bat:
        return
    _battery["status"] = battery_read(bat, "status")
    in_background(battery_low_watch, bat)
    watch(["udevadm", "monitor", "--udev", "--subsystem-match=power_supply"], battery_changed)


def battery_changed(line):
    if "(power_supply)" not in line:     # udevadm's first lines are just a heading
        return
    battery_status_check()
    time.sleep(2)                   # some batteries report their new status a moment later
    battery_status_check()


def battery_status_check():
    status = battery_read(_battery["path"], "status")
    if status == "Discharging" and _battery["status"] != "Discharging":
        notify("Battery discharging")
    elif status == "Charging" and _battery["status"] != "Charging":
        notify("Battery Charging")
    _battery["status"] = status


def battery_low_watch(bat):
    while True:
        on_battery = battery_read(bat, "status").lower() in ("discharging", "not charging")
        if on_battery and int(battery_read(bat, "capacity")) <= battery_low:
            notify("Battery low!", urgency="critical")
        time.sleep(60)


def updates_check():
    """Once the network is up, says whether there are package updates."""
    wait_for_network()
    if output([*package_manager, "-Qu"]).strip():
        notify("Updates available!")


def pytwm_updates_check():
    """Once the network is up, says whether pytwm has a newer commit on GitHub
    than the one setup.sh installed."""
    from datetime import datetime
    try:
        with open(os.path.expanduser("~/.local/state/pytwm/installed")) as f:
            installed = datetime.fromisoformat(f.read().strip())
    except (OSError, ValueError):       # not installed by setup.sh from a git clone
        return
    wait_for_network()
    reply = output(["curl", "-sf", "https://api.github.com/repos/davidnsousa/pytwm/commits/main"], 15)
    try:
        latest = datetime.fromisoformat(json.loads(reply)["commit"]["committer"]["date"])
    except (ValueError, KeyError, TypeError):   # no answer, or GitHub refused
        return
    if latest > installed:
        notify("pytwm updates available!")


def wait_for_network():
    """Waits until NetworkManager says the internet is reachable: "full" after its
    own check, or just "connected" when that check is turned off ("unknown")."""
    while not any(conn == "full" or (state == "connected" and conn == "unknown")
                  for state, conn in nmcli_rows("STATE,CONNECTIVITY", "general")):
        time.sleep(5)


def wait_for_vpn(notice_after=60):
    """Waits until a VPN is active. Notifies once if it takes longer than
    notice_after seconds, so you know why those apps have not opened."""
    waited = 0
    while not vpn_active():
        if waited == notice_after:
            notify("No VPN yet: the apps that need it start once it's on")
        time.sleep(5)
        waited += 5
