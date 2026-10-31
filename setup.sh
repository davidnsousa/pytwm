#!/bin/bash

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
  pcmanfm
  xarchiver
  zip
  unzip
  flameshot
  nsxiv
  neovim
  leaf-markdown-viewer-bin
  picom
  ttf-jetbrains-mono-nerd
  ufw
  polkit-gnome
  gnome-keyring
  seahorse
)

fc-cache -fv

sudo pacman -S --needed --noconfirm base-devel git

# INSTALL yay (built in a temporary folder, removed afterwards)

command -v yay >/dev/null || (
  build=$(mktemp -d)
  git clone https://aur.archlinux.org/yay.git "$build/yay" &&
    cd "$build/yay" && makepkg -si --noconfirm
  rm -rf "$build"
)
yay --save --cleanmenu=false --diffmenu=false

# UPDATE SYSTEM

yay --noconfirm

# INSTALL PKGS

yay -S --needed --noconfirm "${PKGS[@]}"

# OPTIONAL: Printing/scanning support

export DIALOGRC=filesystem/home/user/.config/pytwm/dialogrc   # dialog colors

PRINT_PKGS=(cups sane sane-airscan ipp-usb xsane)

# asked only while some of them are missing
if ! pacman -Q "${PRINT_PKGS[@]}" >/dev/null 2>&1 &&
   dialog --yesno "Do you want printing/scanning support?" 7 50; then
    yay -S --needed --noconfirm "${PRINT_PKGS[@]}"
    sudo systemctl enable cups.service
    sudo systemctl enable ipp-usb.service
fi

# brightness: group video for laptop backlights (brightnessctl);
# module i2c-dev and group i2c for external monitors (ddcutil)

sudo gpasswd -a "$USER" video
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf >/dev/null
sudo groupadd -f i2c
sudo gpasswd -a "$USER" i2c

# COPY FILES
# Everything is copied over your files, except pref.py (your settings): if yours
# differs from the repository's, you can see the differences and choose which to keep,
# or use another file (a backup, say).

pref=$HOME/.config/pytwm/pref.py
repo_pref=filesystem/home/user/.config/pytwm/pref.py
keep=""   # file that becomes pref.py after the copy; empty: the repository's

if [ -e "$pref" ] && ! cmp -s "$pref" "$repo_pref"; then
    echo "Your pref.py differs from the repository's."
    read -r -p "Show the differences? [y/n] " answer
    [ "$answer" = y ] &&
        diff --color=always -u --label "yours" --label "repository" "$pref" "$repo_pref" | less -RFX
    read -r -p "[k]eep yours, take the [r]epository's or use an[o]ther file? [K/r/o] " answer
    case $answer in
        r) ;;
        o) while read -r -e -p "File: " keep; keep=${keep/#\~/$HOME}; [ ! -f "$keep" ]; do
               echo "Not a file: $keep"
           done ;;
        *) keep=$pref ;;
    esac
fi

[ -n "$keep" ] && cp "$keep" /tmp/pytwm-pref.py
cp -r filesystem/home/user/. "$HOME"
[ -n "$keep" ] && mv /tmp/pytwm-pref.py "$pref"
rm -rf "$HOME/.config/pytwm/__pycache__"
chmod +x "$HOME"/.config/pytwm/*.sh       # in case the copy lost it (USB stick, sync)

sudo cp filesystem/etc/X11/xorg.conf.d/30-touchpad.conf /etc/X11/xorg.conf.d/

# bash: ~/.bashrc stays yours; one line is added to it (once) to read pytwm's
# additions from ~/.config/pytwm/bashrc_extras

grep -q pytwm/bashrc_extras "$HOME/.bashrc" 2>/dev/null ||
  echo '[ -f ~/.config/pytwm/bashrc_extras ] && . ~/.config/pytwm/bashrc_extras' >> "$HOME/.bashrc"

# DEFAULT APPS: images open in nsxiv, unless you already chose an image viewer

has_default() {   # has_default TYPE: ~/.config/mimeapps.list sets a default app for it
    awk -v t="$1=" '/^\[/ { s = ($0 == "[Default Applications]") } s && index($0, t) == 1 { f = 1 }
                    END { exit !f }' "$HOME/.config/mimeapps.list" 2>/dev/null
}
has_default image/png || xdg-mime default nsxiv.desktop image/png image/jpeg image/jpg image/gif \
  image/webp image/bmp image/tiff image/avif image/jxl image/heic image/heif image/svg+xml

# btop: Tokyo Night theme, unless you chose another one (btop fills in the rest of
# its file when you quit it with q)

btop_conf=$HOME/.config/btop/btop.conf
if [ ! -e "$btop_conf" ]; then
    mkdir -p "$(dirname "$btop_conf")" && echo 'color_theme = "tokyo-night"' > "$btop_conf"
else
    sed -i 's/^color_theme = "Default"$/color_theme = "tokyo-night"/' "$btop_conf"
fi

# ENABLE SERVICES

# system services (sudo): start at boot, for the whole machine
sudo systemctl enable NetworkManager.service
sudo systemctl enable bluetooth.service
sudo systemctl enable ufw.service

sudo ufw enable
sudo rfkill unblock bluetooth

# user services: start when you log in, in X or on a text console; their files
# were copied into ~/.config/systemd/user above (udiskie mounts removable drives)
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

# SET UP pytwm (its Python packages, in ~/.local/share/pytwm/venv)

venv=$HOME/.local/share/pytwm/venv
python -m venv "$venv"
"$venv/bin/pip" install --upgrade pip
"$venv/bin/pip" install -r "$HOME/.config/pytwm/requirements.txt"

# the date of the commit installed, for pytwm_updates_check() (not saved when this
# copy of the repository is not a git clone)

state=$HOME/.local/state/pytwm
mkdir -p "$state"
date=$(git log -1 --format=%cI 2>/dev/null) && echo "$date" > "$state/installed"

echo
echo "Finished! Reboot."
