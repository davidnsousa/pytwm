#!/bin/bash

# Installs or updates pytwm. Run from the repository's folder. Once dialog is
# installed (after the first run), it asks which steps to run, all ticked; to
# just update pytwm, keep only "pytwm" ticked.

export DIALOGRC=filesystem/home/user/.config/pytwm/dialogrc   # dialog colors

# PKGS FOR DE

PKGS=(
  python
  libxft
  gdk-pixbuf2
  xorg-server
  xorg-xinit
  xorg-xev
  xdg-utils
  xdg-desktop-portal
  xdg-desktop-portal-gtk
  xclip
  alacritty
  gvfs
  udiskie
  btop
  pulsemixer
  networkmanager
  brightnessctl
  ddcutil
  adw-gtk-theme
  gnome-themes-extra-gtk2
  papirus-icon-theme
  ttf-dejavu
  bluez
  bluez-utils
  man-db
  xorg-xrandr
  pipewire-pulse
  dialog
  libnotify
  pacman-contrib
  xarchiver
  zip
  unzip
  flameshot
  nsxiv
  neovim
  leaf-markdown-viewer-bin
  helium-browser-bin
  picom
  ttf-jetbrains-mono-nerd
  ufw
  polkit-gnome
  gnome-keyring
  seahorse
)

PRINT_PKGS=(cups sane sane-airscan ipp-usb xsane)

# CHOOSE STEPS: they run in this order. Without dialog (the first run) all of
# them run, and printing is asked about on its own.

if command -v dialog >/dev/null; then
    menu=yes
    printing=on      # ticked while some printing packages are missing
    pacman -Q "${PRINT_PKGS[@]}" >/dev/null 2>&1 && printing=off
    steps=$(dialog --title "pytwm setup" --separate-output --checklist "Steps to run:" 15 76 6 \
        packages "System update, yay and pytwm's packages"            on \
        qfm      "qfm file manager"                                   on \
        printing "Printing and scanning (CUPS, SANE)"                 $printing \
        pytwm    "pytwm files and its Python packages"                on \
        system   "User groups, touchpad, services, firewall, keyring" on \
        defaults "Image viewer, browser, folder app, btop theme"      on \
        3>&1 1>&2 2>&3) || { clear; exit; }
    clear
else
    menu=no
    steps="packages qfm printing pytwm system defaults"
fi

want() { grep -qx "$1" <<< "${steps// /$'\n'}"; }   # want STEP: STEP was chosen

# PACKAGES: yay (built in a temporary folder, removed afterwards), a system
# update and pytwm's packages

if want packages; then
    sudo pacman -S --needed --noconfirm base-devel git
    command -v yay >/dev/null || (
      build=$(mktemp -d)
      git clone https://aur.archlinux.org/yay.git "$build/yay" &&
        cd "$build/yay" && makepkg -si --noconfirm
      rm -rf "$build"
    )
    yay --save --cleanmenu=false --diffmenu=false
    yay --noconfirm
    yay -S --needed --noconfirm "${PKGS[@]}"
    fc-cache -fv
fi

# FILE MANAGER: qfm, in /opt/qfm

if want qfm; then
    build=$(mktemp -d)
    git clone --depth 1 https://github.com/davidnsousa/qfm.git "$build/qfm" && "$build/qfm/install.sh"
    rm -rf "$build"
fi

# PRINTING: chosen in the menu, or asked (first run) while some packages are missing

if want printing && { [ $menu = yes ] || { ! pacman -Q "${PRINT_PKGS[@]}" >/dev/null 2>&1 &&
       dialog --yesno "Do you want printing/scanning support?" 7 50; }; }; then
    yay -S --needed --noconfirm "${PRINT_PKGS[@]}"
    sudo systemctl enable cups.service
    sudo systemctl enable ipp-usb.service
fi

# PYTWM: its files and Python packages
# Everything is copied over your files, except pref.py (your settings): if yours
# differs from the repository's, you can see the differences and choose which to keep,
# or use another file (a backup, say).

if want pytwm; then
    pref=$HOME/.config/pytwm/pref.py
    repo_pref=filesystem/home/user/.config/pytwm/pref.py
    keep=""   # file that becomes pref.py after the copy; empty: the repository's

    # asked in dialog (installed by the packages step); without it, yours is kept
    if [ -e "$pref" ] && ! cmp -s "$pref" "$repo_pref"; then
        keep=$pref
        while command -v dialog >/dev/null; do
            answer=$(dialog --title "pref.py" --default-item keep \
                --menu "Your pref.py differs from the repository's." 12 60 4 \
                keep  "Keep yours" \
                repo  "Take the repository's" \
                diff  "Show the differences" \
                other "Use another file (a backup, say)" \
                3>&1 1>&2 2>&3) || break                       # cancel: keep yours
            case $answer in
                keep) break ;;
                repo) keep=""; break ;;
                diff) diff -u --label "yours" --label "repository" "$pref" "$repo_pref" \
                          > /tmp/pytwm-pref.diff
                      dialog --title "yours (-) and the repository's (+)" \
                          --textbox /tmp/pytwm-pref.diff 0 0
                      rm -f /tmp/pytwm-pref.diff ;;
                other) file=$(dialog --title "File to use as pref.py" \
                           --fselect "$HOME/" 14 70 3>&1 1>&2 2>&3) || continue
                       if [ -f "$file" ]; then keep=$file; break; fi
                       dialog --msgbox "Not a file: $file" 6 60 ;;
            esac
        done
        clear
    fi

    [ -n "$keep" ] && cp "$keep" /tmp/pytwm-pref.py
    cp -r filesystem/home/user/. "$HOME"
    [ -n "$keep" ] && mv /tmp/pytwm-pref.py "$pref"
    rm -rf "$HOME/.config/pytwm/__pycache__"
    chmod +x "$HOME"/.config/pytwm/*.sh       # in case the copy lost it (USB stick, sync)

    # bash: ~/.bashrc stays yours; one line is added to it (once) to read pytwm's
    # additions from ~/.config/pytwm/bashrc_extras
    grep -q pytwm/bashrc_extras "$HOME/.bashrc" 2>/dev/null ||
      echo '[ -f ~/.config/pytwm/bashrc_extras ] && . ~/.config/pytwm/bashrc_extras' >> "$HOME/.bashrc"

    # Python packages, in ~/.local/share/pytwm/venv
    venv=$HOME/.local/share/pytwm/venv
    python -m venv "$venv"
    "$venv/bin/pip" install --upgrade pip
    "$venv/bin/pip" install -r "$HOME/.config/pytwm/requirements.txt"

    # the date of the commit installed, for updates_watch() (not saved when this
    # copy of the repository is not a git clone)
    state=$HOME/.local/state/pytwm
    mkdir -p "$state"
    date=$(git log -1 --format=%cI 2>/dev/null) && echo "$date" > "$state/installed"

    systemctl --user daemon-reload     # in case the copy changed a user service
fi

# SYSTEM SETTINGS AND SERVICES

if want system; then
    # brightness: group video for laptop backlights (brightnessctl);
    # module i2c-dev and group i2c for external monitors (ddcutil)
    sudo gpasswd -a "$USER" video
    echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf >/dev/null
    sudo groupadd -f i2c
    sudo gpasswd -a "$USER" i2c

    sudo cp filesystem/etc/X11/xorg.conf.d/30-touchpad.conf /etc/X11/xorg.conf.d/

    # system services (sudo): start at boot, for the whole machine
    sudo systemctl enable NetworkManager.service
    sudo systemctl enable bluetooth.service
    sudo systemctl enable ufw.service

    sudo ufw enable
    sudo rfkill unblock bluetooth

    # user services: start when you log in, in X or on a text console; their files
    # are copied into ~/.config/systemd/user by the pytwm step (udiskie mounts
    # removable drives)
    systemctl --user daemon-reload
    systemctl --user enable --now udiskie.service

    # KEYRING: gnome-keyring keeps the passwords apps save (Proton, Electron apps...),
    # encrypted with your login password; PAM unlocks it when you log in and
    # passwd changes its password with yours
    grep -q pam_gnome_keyring /etc/pam.d/login || printf '%s\n' \
      'auth       optional     pam_gnome_keyring.so' \
      'session    optional     pam_gnome_keyring.so auto_start' | sudo tee -a /etc/pam.d/login >/dev/null
    grep -q pam_gnome_keyring /etc/pam.d/passwd || \
      echo 'password   optional     pam_gnome_keyring.so' | sudo tee -a /etc/pam.d/passwd >/dev/null
fi

# DEFAULT APPS AND THEMES

if want defaults; then
    # images open in nsxiv and web pages in Helium, unless you already chose others
    has_default() {   # has_default TYPE: ~/.config/mimeapps.list sets a default app for it
        awk -v t="$1=" '/^\[/ { s = ($0 == "[Default Applications]") } s && index($0, t) == 1 { f = 1 }
                        END { exit !f }' "$HOME/.config/mimeapps.list" 2>/dev/null
    }
    has_default image/png || xdg-mime default nsxiv.desktop image/png image/jpeg image/jpg image/gif \
      image/webp image/bmp image/tiff image/avif image/jxl image/heic image/heif image/svg+xml
    has_default x-scheme-handler/https || xdg-settings set default-web-browser helium.desktop

    # folders open in qfm, which also answers other apps' "Show in folder" (qfm's own
    # setting "Open folders with qfm", which can turn it off again)
    [ -x /opt/qfm/.venv/bin/python ] && /opt/qfm/.venv/bin/python -c '
import sys; sys.path.insert(0, "/opt/qfm")
from qfmlib import fm1, store
s = store.Store(); fm1.enable(s.options); s.options["default_fm"] = True; s.save_options()'

    # btop: Tokyo Night theme, unless you chose another one (btop fills in the rest of
    # its file when you quit it with q)
    btop_conf=$HOME/.config/btop/btop.conf
    if [ ! -e "$btop_conf" ]; then
        mkdir -p "$(dirname "$btop_conf")" && echo 'color_theme = "tokyo-night"' > "$btop_conf"
    else
        sed -i 's/^color_theme = "Default"$/color_theme = "tokyo-night"/' "$btop_conf"
    fi
fi

echo
if want packages || want system; then
    echo "Finished! Reboot."
else
    echo "Finished! Reload pytwm to use the new files."
fi
