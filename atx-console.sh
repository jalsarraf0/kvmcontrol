#!/bin/bash
# Interactive ATX console for GL.iNet Comet (GL-RM1) + GL-ATXPC.
# Talks to /usr/sbin/atxpower over /dev/ttyACM0 (USB CDC ACM 1209:c550).
# Commands match GL.iNet staff listing on the forum (2025-12-24) and
# kvmd/plugins/atx/glatx.py in the local glkvm tree.
# This simulates the *physical* front-panel buttons of the attached PC.
# It does NOT power off the KVM itself.
#
# Do not run unattended. Hard off / reset can lose unsaved work.

set -u

ATXPOWER="${ATXPOWER:-/usr/sbin/atxpower}"
DEVICE="${ATX_DEVICE:-/dev/ttyACM0}"
HOST="$(hostname 2>/dev/null || echo unknown)"

# ANSI truecolor — no tput on these boxes. Synthwave palette.
R=$'\033[0m'
B=$'\033[1m'
PINK=$'\033[38;2;255;41;125m'      # hot pink   #ff297d
MAG=$'\033[38;2;242;34;255m'       # magenta    #f222ff
PURPLE=$'\033[38;2;157;78;221m'    # purple     #9d4edd
CYAN=$'\033[38;2;5;217;232m'       # cyan       #05d9e8
BLUE=$'\033[38;2;1;205;254m'       # elec. blue #01cdfe
ORANGE=$'\033[38;2;255;140;66m'    # sunset     #ff8c42
YEL=$'\033[38;2;255;209;0m'        # amber      #ffd100
GRN=$'\033[38;2;57;255;145m'       # neon green #39ff91
RED=$'\033[38;2;255;51;102m'       # neon red   #ff3366
MUTED=$'\033[38;2;138;120;172m'    # muted lavender-grey, replaces "dim"
W=$'\033[97m'
DIM="$MUTED"

# Gradient magenta(242,34,255) -> cyan(5,217,232), used by bar() and grule().
_grad() {
    # $1 = t (0-100) -> prints "\033[38;2;r;g;bm"
    local t="$1" rr gg bb
    rr=$(( 242 + (5 - 242) * t / 100 ))
    gg=$(( 34 + (217 - 34) * t / 100 ))
    bb=$(( 255 + (232 - 255) * t / 100 ))
    printf '\033[38;2;%d;%d;%dm' "$rr" "$gg" "$bb"
}

grule() {
    # $1 = width (default 50), gradient magenta -> cyan horizontal rule
    local width="${1:-50}" c=0 t
    printf "  "
    while [ "$c" -lt "$width" ]; do
        t=$(( c * 100 / width ))
        printf '%s─' "$(_grad "$t")"
        c=$((c + 1))
    done
    printf "%s\n" "$R"
}

bar() {
    # $1=label $2=seconds (integer, BusyBox sleep is 1s resolution)
    local label="$1"
    local secs="${2:-3}"
    local i pct filled c t width=30
    echo
    echo "  ${MUTED}${label}${R}"
    i=0
    while [ "$i" -le "$secs" ]; do
        pct=$(( i * 100 / secs ))
        filled=$(( width * i / secs ))
        printf "\r  ${PURPLE}▐${R}"
        c=0
        while [ "$c" -lt "$width" ]; do
            if [ "$c" -lt "$filled" ]; then
                t=$(( c * 100 / width ))
                printf '%s█' "$(_grad "$t")"
            else
                printf '%s░' "$MUTED"
            fi
            c=$((c + 1))
        done
        printf "${R}${PURPLE}▌${R} ${W}%3d%%${R}  " "$pct"
        [ "$i" -lt "$secs" ] && sleep 1
        i=$((i + 1))
    done
    printf "${R}\n"
}

ok()   { echo "  ${GRN}▸${R} $*"; }
warn() { echo "  ${YEL}▸${R} $*"; }
err()  { echo "  ${RED}▸${R} $*"; }

banner() {
    clear 2>/dev/null || printf '\033c'
    echo
    echo "                ${YEL}▄▄▄▄▄▄▄▄▄▄▄▄${R}"
    echo "             ${YEL}▄${ORANGE}▓▓▓▓▓▓▓▓▓▓▓▓▓${YEL}▄${R}"
    echo "           ${ORANGE}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓${R}"
    echo "          ${ORANGE}▓${PINK}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓${ORANGE}▓${R}"
    echo "          ${MUTED}░░░░░░░░░░░░░░░░░░░${R}"
    echo "          ${PINK}▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓${R}"
    echo "           ${PINK}▀${MAG}▓▓▓▓▓▓▓▓▓▓▓▓▓${PINK}▀${R}"
    echo "             ${MAG}▀▓▓▓▓▓▓▓▓▓▓▓▀${R}"
    echo "                ${PURPLE}▀▀▀▀▀▀▀▀▀▀${R}"
    echo "     ${PURPLE}╲    ╲     ╲    ${MUTED}│${PURPLE}    ╱     ╱    ╱${R}"
    echo "  ${PURPLE}╲    ╲     ╲     ╲  ${MUTED}│${PURPLE}  ╱     ╱     ╱    ╱${R}"
    grule 50
    echo "  ${PINK}${B}GL-ATXPC${R}  ${MUTED}//${R}  ${CYAN}remote power console${R}"
    echo "  kvm ${W}${B}${HOST}${R}"
    grule 50
    echo
    echo "  ${MUTED}This KVM presses the attached PC's power/reset wires.${R}"
    echo "  ${MUTED}dominus KVM → dominus-nobara    amarillokvm → amarillo${R}"
    echo
}

need_board() {
    if [ ! -e "$DEVICE" ]; then
        err "ATX board not found at ${DEVICE}"
        echo "  ${MUTED}Plug the GL-ATXPC USB-C into the KVM USB-Device (not 5V 2A).${R}"
        echo "  ${MUTED}Need a data cable — charge-only A-to-C will light the LED and still fail.${R}"
        return 1
    fi
    if [ ! -x "$ATXPOWER" ]; then
        err "missing ${ATXPOWER}"
        return 1
    fi
    return 0
}

# status only — does not change power
read_state() {
    if [ ! -e "$DEVICE" ]; then
        echo "NO-BOARD"
        return 2
    fi
    "$ATXPOWER" "$DEVICE" power_state 2>/dev/null | tr -d '\r' | awk 'NF{print $1; exit}'
}

show_status() {
    echo "  ${B}reading board…${R}"
    bar "UART  power_state" 2
    local st
    st="$(read_state)"
    echo
    if [ ! -e "$DEVICE" ]; then
        err "board absent  (${DEVICE} missing)"
        return
    fi
    ok "device  ${DEVICE} present"
    case "$st" in
        on)    ok  "PC power  ${GRN}${B}ON${R}   (ACPI / running or still shutting down)" ;;
        off)   warn "PC power  ${RED}${B}OFF${R}  (rails down — short click will start it)" ;;
        sleep) warn "PC power  ${YEL}${B}SLEEP${R}  (S3/S4-ish — short click usually wakes)" ;;
        *)     warn "PC power  unknown (${st:-empty})" ;;
    esac
}

confirm() {
    local prompt="$1"
    local ans
    printf "  ${YEL}%s${R}  [y/N] " "$prompt"
    read -r ans
    case "$ans" in
        y|Y|yes|YES) return 0 ;;
        *) echo "  ${MUTED}cancelled${R}"; return 1 ;;
    esac
}

run_atx() {
    local cmd="$1"
    local wait_s="${2:-3}"
    local title="$3"
    local out rc st

    echo
    echo "  ${B}what will happen${R}"
    echo "  ${MUTED}${title}${R}"
    echo
    echo "  ${CYAN}→${R} ${ATXPOWER} ${DEVICE} ${cmd}"
    echo

    need_board || return 1

    # run first so the bar covers UART latency + a short settle
    out="$("$ATXPOWER" "$DEVICE" "$cmd" 2>&1)"
    rc=$?
    bar "sending ${cmd}  /  waiting for board" "$wait_s"
    st="$(read_state)"

    echo
    if [ "$rc" -eq 0 ]; then
        ok "board accepted ${cmd}"
    else
        err "atxpower exited ${rc}"
        [ -n "$out" ] && echo "  ${MUTED}${out}${R}"
    fi
    echo "  ${B}reported state now:${R}  ${W}${st:-?}${R}"
    echo
    echo "  ${MUTED}ON/OFF in software can lag a few seconds behind ACPI.${R}"
}

do_on() {
    echo "  ${GRN}POWER ON${R} — short power-button pulse if the PC is off."
    echo "  ${MUTED}No-op if atxpower already sees 'on'. Does not force a hard reset.${R}"
    confirm "Pulse power ON on the machine attached to ${HOST}?" || return
    run_atx power_on 3 "Short click. Same as tapping the case power button from off."
}

do_off() {
    echo "  ${YEL}GRACEFUL OFF${R} — short power-button pulse while the PC is on."
    echo "  ${MUTED}Asks the OS to shut down (ACPI). May sit in 'on' until userspace exits.${R}"
    echo "  ${RED}If this KVM is 'dominus', that is THIS workstation.${R}"
    confirm "Ask the attached PC to shut down (graceful)?" || return
    run_atx power_off 4 "Short click while on. Same as a normal press of the power button."
}

do_off_hard() {
    echo "  ${RED}${B}HARD OFF${R} — long power-button hold. Cuts power. Unsaved work is gone."
    echo "  ${RED}Use only if the OS is wedged.${R}"
    confirm "FORCE the attached PC off (long press)?" || return
    confirm "Type y again — this is not ACPI, it is a hold." || return
    run_atx power_off_hard 5 "Long press. Equivalent to holding the case power button."
}

do_reset() {
    echo "  ${YEL}RESET${R} — pulses the motherboard reset header (not a clean reboot)."
    echo "  ${MUTED}Instant restart. Filesystems may need journal replay.${R}"
    confirm "Pulse RESET on the attached PC?" || return
    run_atx power_reset 3 "Reset header click. Hard reboot, not shutdown-then-on."
}

do_click_short() {
    echo "  ${W}RAW short click${R} — always send a short pulse, no on/off logic."
    confirm "Send click_power_short?" || return
    run_atx click_power_short 3 "Unconditional short POWER SW pulse."
}

do_click_long() {
    echo "  ${W}RAW long click${R} — always send a long pulse (hard-off if the PC is on)."
    confirm "Send click_power_long?" || return
    run_atx click_power_long 5 "Unconditional long POWER SW hold."
}

do_click_reset() {
    echo "  ${W}RAW reset click${R} — pulse RESET SW only."
    confirm "Send click_reset?" || return
    run_atx click_reset 3 "Unconditional RESET SW pulse."
}

do_sn() {
    echo "  ${B}board serial${R}"
    bar "UART  get_sn" 2
    echo
    if need_board; then
        "$ATXPOWER" "$DEVICE" get_sn 2>&1 | sed 's/^/  /'
    fi
}

menu() {
    grule 50
    echo "  ${PINK}${B} 1${R}  status          ${MUTED}read power_state (on / off / sleep)${R}"
    echo "  ${CYAN}${B} 2${R}  power on        ${MUTED}graceful start if off${R}"
    echo "  ${CYAN}${B} 3${R}  power off       ${MUTED}ACPI / short press  — preferred off${R}"
    echo "  ${CYAN}${B} 4${R}  power off HARD  ${MUTED}long press — last resort${R}"
    echo "  ${CYAN}${B} 5${R}  reset           ${MUTED}reset header — hard reboot${R}"
    grule 50
    echo "  ${PURPLE}${B} 6${R}  raw short click ${MUTED}no on/off check${R}"
    echo "  ${PURPLE}${B} 7${R}  raw long click"
    echo "  ${PURPLE}${B} 8${R}  raw reset click"
    echo "  ${PINK}${B} 9${R}  board serial    ${MUTED}get_sn${R}"
    echo "  ${MUTED}${B} q${R}  quit"
    grule 50
    printf "  ${MAG}${B}❯${R} ${W}choose${R} "
}

pause() {
    printf "  ${MUTED}enter to return to menu${R} "
    read -r _
}

main() {
    while true; do
        banner
        need_board || warn "actions that talk to the board will fail until it enumerates"
        echo
        menu
        read -r choice
        echo
        case "$choice" in
            1) show_status; pause ;;
            2) do_on; pause ;;
            3) do_off; pause ;;
            4) do_off_hard; pause ;;
            5) do_reset; pause ;;
            6) do_click_short; pause ;;
            7) do_click_long; pause ;;
            8) do_click_reset; pause ;;
            9) do_sn; pause ;;
            q|Q|0|quit|exit) echo "  ${MUTED}bye${R}"; echo; exit 0 ;;
            *) warn "not a menu item"; sleep 1 ;;
        esac
    done
}

main
