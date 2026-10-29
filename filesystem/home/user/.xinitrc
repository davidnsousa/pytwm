#!/bin/sh

userresources=$HOME/.Xresources
usermodmap=$HOME/.Xmodmap
sysresources=/etc/X11/xinit/.Xresources
sysmodmap=/etc/X11/xinit/.Xmodmap

if [ -f $sysresources ]; then
    xrdb -merge $sysresources
fi

if [ -f $sysmodmap ]; then
    xmodmap $sysmodmap
fi

if [ -f "$userresources" ]; then
    xrdb -merge "$userresources"
fi

if [ -f "$usermodmap" ]; then
    xmodmap "$usermodmap"
fi

if [ -d /etc/X11/xinit/xinitrc.d ] ; then
    for f in /etc/X11/xinit/xinitrc.d/?*.sh ; do
        [ -x "$f" ] && . "$f"
    done
    unset f
fi

export QT_ENABLE_HIGHDPI_SCALING=0

# pytwm; its errors go to ~/.local/state/pytwm/pytwm.log (the previous session's: pytwm.log.old)
log="$HOME/.local/state/pytwm/pytwm.log"
mkdir -p "$(dirname "$log")"
[ -f "$log" ] && mv -f "$log" "$log.old"
exec ~/.local/share/pytwm/venv/bin/python ~/.config/pytwm/pytwm.py 2>"$log"
