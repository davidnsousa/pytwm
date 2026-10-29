#!/bin/bash

PKGS=(
    gparted                        "GParted partition editor"                  off
    wget                           "Download files"                            off
    python-ansi2html               "Convert ANSI to HTML"                      off
    fastfetch                      "System info tool"                          off
    bitwarden                      "Password manager"                          off
    keepassxc                      "Password manager"                          off
    gigolo                         "Manage remote files"                       off
    filezilla                      "FTP client"                                off
    baobab                         "Disk usage analyzer"                       off
    mousepad                       "Text editor"                               off
    obsidian                       "Note-taking app"                           off
    atril                          "Document viewer"                           off
    zathura                        "Keyboard document viewer"                  off
    zathura-pdf-mupdf              "PDF support for zathura"                   off
    pdftk                          "PDF toolkit"                               off
    xournalpp                      "PDF annotation"                            off
    meld                           "Diff & merge tool"                         off
    zeal                           "Offline documentation browser"             off
    octave                         "GNU Octave"                                off
    visual-studio-code-bin         "VS Code"                                   off
    vscodium-bin                   "VSCodium"                                  off
    pulsar-bin                     "Pulsar editor"                             off
    rstudio-desktop-bin            "RStudio"                                   off
    arduino-ide-bin                "Arduino IDE"                               off
    pycharm-community-edition      "PyCharm CE"                                off
    thonny                         "Python IDE"                                off
    qucs-s                         "Circuit simulator"                         off
    fritzing                       "Circuit design"                            off
    kicad                          "KiCad EDA"                                 off
    kicad-library                  "KiCad libraries"                           off
    kicad-library-3d               "KiCad 3D libraries"                        off
    chromium                       "Chromium browser"                          off
    google-chrome                  "Google Chrome"                             off
    firefox                        "Firefox browser"                           off
    mullvad-browser-bin            "Mullvad Browser"                           off
    brave-bin                      "Brave browser"                             off
    vivaldi                        "Vivaldi browser"                           off
    transmission-gtk               "Transmission torrent client"               off
    zoom                           "Zoom video calls"                          off
    telegram-desktop               "Telegram"                                  off
    slack-desktop                  "Slack"                                     off
    signal-desktop                 "Signal"                                    off
    vlc                            "VLC media player"                          off
    mpv                            "Keyboard media player"                     off
    lmms                           "Music production"                          off
    audacity                       "Audio editor"                              off
    gpick                          "Color picker"                              off
    pinta                          "Image editor"                              off
    gimp                           "GIMP image editor"                         off
    inkscape                       "Vector graphics editor"                    off
    blender                        "3D software"                               off
    shotcut                        "Video editor"                              off
    libreoffice-fresh              "LibreOffice"                               off
    onlyoffice-bin                 "OnlyOffice"                                off
    zotero-bin                     "Reference manager"                         off
    virtualbox                     "VirtualBox"                                off
    virtualbox-host-dkms           "VirtualBox kernel modules"                 off
    virt-manager                   "Virtual machines (QEMU/KVM)"               off
    qemu-full                      "QEMU for virt-manager"                     off
    mullvad-vpn                    "Mullvad VPN"                               off
    proton-vpn-cli                 "Proton VPN"                                off
    protonmail-bridge              "Proton Mail Bridge"                        off
    rpi-imager                     "Raspberry Pi Imager"                       off
    docker                         "Docker"                                    off
    distrobox                      "Distrobox"                                 off
    tigervnc                       "VNC server"                                off
    sshfs                          "SSH filesystem"                            off
    fuse2                          "FUSE support"                              off
    syncthing                      "Sync files"                                off
    megacmd-bin                    "MEGA command-line client"                  off
    ollama                         "AI model manager"                          off
    thunderbird                    "Email client"                              off
)


SELECTION_INSTALL=$(dialog --title "Install packages" \
    --separate-output --checklist "Select packages:" 30 100 20 \
    "${PKGS[@]}" 3>&1 1>&2 2>&3)
clear
for PKG in $SELECTION_INSTALL; do
    yay -S --needed --noconfirm "$PKG"
done
echo
echo "Done!"
read -r -p "Press Enter to continue..."


