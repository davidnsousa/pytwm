#!/bin/bash
# monitors.sh: arrange the monitors from the terminal (like arandr), with xrandr.
# Pick a monitor, then what to change. Each change asks to be kept, and goes back
# to the old layout by itself after 15 seconds, in case the screen goes black.

choose() {   # choose TITLE TEXT TAG DESCRIPTION...: a menu; prints the chosen tag
    local title=$1 text=$2 lines=$(( ($# - 2) / 2 )); shift 2
    [ $lines -gt 20 ] && lines=20
    dialog --title "$title" --menu "$text" 0 0 $lines "$@" 3>&1 1>&2 2>&3
}

monitor_state() {   # monitor_state NAME: "2560x1440+0+0 primary", "off" or "not connected"
    xrandr --query | awk -v m="$1" '$1 == m {
        if ($2 != "connected") { print "not connected"; exit }
        state = "off"
        for (i = 3; i <= NF; i++) if ($i ~ /^[0-9]+x[0-9]+\+/) state = $i
        if ($3 == "primary") state = state " primary"
        print state; exit }'
}

modes() {   # modes NAME: its resolutions, one per line, the current one marked with *
    xrandr --query | awk -v m="$1" '
        /^[^ ]/ { inside = ($1 == m); next }
        inside { print $1 (index($0, "*") ? " *" : "") }'
}

rates() {   # rates NAME RESOLUTION: its refresh rates
    xrandr --query | awk -v m="$1" -v r="$2" '
        /^[^ ]/ { inside = ($1 == m); next }
        inside && $1 == r { for (i = 2; i <= NF; i++) { gsub(/[*+]/, "", $i); if ($i != "") print $i } }'
}

apply() {   # apply XRANDR-ARGS...: change the layout, and undo it unless it is kept
    local before
    # the mode is the one marked *, not the size on the screen, which is larger
    # on a monitor pytwm scales (dpi = "auto"); pytwm sets the scale again itself
    before=$(xrandr --query | awk '
        function out() { if (name != "") printf "--output %s %s%s ", name,
            (mode != "" && pos != "" ? "--mode " mode " --pos " pos : "--off"), primary }
        /^[^ ]/ { out(); name = ""
            if ($2 != "connected") next
            name = $1; mode = ""; pos = ""; primary = ($3 == "primary" ? " --primary" : "")
            for (i = 3; i <= NF; i++) if ($i ~ /^[0-9]+x[0-9]+\+/) {
                split($i, p, /[x+]/); pos = p[3] "x" p[4] }
            next }
        name != "" && index($0, "*") { mode = $1 }
        END { out() }')
    xrandr "$@" || { dialog --msgbox "xrandr could not do that." 6 40; return; }
    if ! dialog --timeout 15 --yesno "Keep this layout?\n\nIt goes back by itself in 15 seconds." 8 44; then
        xrandr $before
    fi
}

while true; do
    items=()
    for m in $(xrandr --query | awk '/ connected/ { print $1 }'); do
        items+=("$m" "$(monitor_state "$m")")
    done
    monitor=$(choose "Monitors" "Pick a monitor:" "${items[@]}") || break

    others=()
    for m in $(xrandr --query | awk -v m="$monitor" '/ connected/ && $1 != m { print $1 }'); do
        others+=("$m")
    done

    what=$(choose "$monitor" "$(monitor_state "$monitor")" \
        resolution "Resolution and refresh rate" \
        position   "Where it is, next to another monitor" \
        primary    "Make it the main monitor" \
        on         "Turn it on (best resolution)" \
        off        "Turn it off") || continue

    case $what in
        resolution)
            items=()
            while read -r res mark; do items+=("$res" "$mark"); done < <(modes "$monitor")
            res=$(choose "$monitor" "Resolution (* = now):" "${items[@]}") || continue
            items=()
            for r in $(rates "$monitor" "$res"); do items+=("$r" "Hz"); done
            rate=$(choose "$monitor" "Refresh rate for $res:" "${items[@]}") || continue
            apply --output "$monitor" --mode "$res" --rate "$rate" ;;
        position)
            [ ${#others[@]} -gt 0 ] || { dialog --msgbox "There is no other monitor." 6 40; continue; }
            items=()
            for m in "${others[@]}"; do
                # no "--" in front: dialog would take it for one of its own options
                items+=("left-of $m" "left of $m" "right-of $m" "right of $m"
                        "above $m" "above $m" "below $m" "below $m" "same-as $m" "the same as $m")
            done
            where=$(choose "$monitor" "Place $monitor:" "${items[@]}") || continue
            read -r how other <<< "$where"
            apply --output "$monitor" --auto "--$how" "$other" ;;
        primary) apply --output "$monitor" --primary ;;
        on)      apply --output "$monitor" --auto ;;
        off)     apply --output "$monitor" --off ;;
    esac
done
clear
