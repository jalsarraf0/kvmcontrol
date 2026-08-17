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
ITALIC=$'\033[3m'                  # SGR 3 — real italics on most modern terminal fonts
BGSEL=$'\033[48;2;46;28;74m'       # selection highlight background

# Gradient magenta(242,34,255) -> cyan(5,217,232), used by bar() and grule().
# Sets $_GRAD_ESC instead of printing to stdout — a plain function call,
# not a $(...) command substitution, so it doesn't fork a subshell. This
# runs in a tight per-character loop (grule/bar redraw on every keypress
# now that the menu is a TUI); forking 50+ subshells per redraw was slow
# enough on the appliance's modest ARM CPU to make the UI feel hung.
_grad() {
    # $1 = t (0-100) -> sets _GRAD_ESC to "\033[38;2;r;g;bm"
    local t="$1" rr gg bb
    rr=$(( 242 + (5 - 242) * t / 100 ))
    gg=$(( 34 + (217 - 34) * t / 100 ))
    bb=$(( 255 + (232 - 255) * t / 100 ))
    _GRAD_ESC=$'\033[38;2;'"${rr};${gg};${bb}m"
}

grule() {
    # $1 = width (default 50), gradient magenta -> cyan horizontal rule
    local width="${1:-50}" c=0 t
    printf "  "
    while [ "$c" -lt "$width" ]; do
        t=$(( c * 100 / width ))
        _grad "$t"
        printf '%s─' "$_GRAD_ESC"
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
        # \033[G (cursor to column 1), not bare \r — some terminal engines
        # mishandle rapid CR-without-LF redraws under fast repeated writes.
        printf "\033[G  ${PURPLE}▐${R}"
        c=0
        while [ "$c" -lt "$width" ]; do
            if [ "$c" -lt "$filled" ]; then
                t=$(( c * 100 / width ))
                _grad "$t"
                printf '%s█' "$_GRAD_ESC"
            else
                printf '%s░' "$MUTED"
            fi
            c=$((c + 1))
        done
        printf "${R}${PURPLE}▌${R} ${W}%3d%%${R}  \033[K" "$pct"
        [ "$i" -lt "$secs" ] && sleep 1
        i=$((i + 1))
    done
    printf "${R}\n"
}

ok()   { echo "  ${GRN}▸${R} $*"; }
warn() { echo "  ${YEL}▸${R} $*"; }
err()  { echo "  ${RED}▸${R} $*"; }

# ── TUI mechanics: raw mode, alt screen, arrow-key nav ──────────────
# `read -N` (not `-n`) is required: under a real tty, ICRNL/newline
# handling means Enter's byte can be silently swallowed as a line
# delimiter by `-n`, never reaching the case statement. Verified against
# a real pty (tmux), not just piped input, which does not exhibit this.
STTY_ORIG=""
TUI_ENTERED=0
tui_enter() {
    STTY_ORIG="$(stty -g 2>/dev/null || true)"
    stty -echo -icanon -icrnl -inlcr -ixon min 1 time 0 2>/dev/null || true
    printf '\033[?1049h\033[?25l'
    TUI_ENTERED=1
}
tui_leave() {
    [ "$TUI_ENTERED" -eq 1 ] || return 0
    printf '\033[?25h\033[?1049l'
    [ -n "$STTY_ORIG" ] && stty "$STTY_ORIG" 2>/dev/null || true
}
trap tui_leave EXIT INT TERM

# Sets $KEY instead of printing to stdout — called directly, never via
# $(...). A SIGINT trap doesn't reliably interrupt a blocking read inside
# a command-substitution subshell until that subshell itself exits, which
# can make Ctrl-C (and everything else) appear to do nothing.
read_key() {
    local k k2 k3
    IFS= read -rsN1 k || { KEY=QUIT; return; }
    case "$k" in
        $'\x1b')
            if read -rsN1 -t 0.05 k2 2>/dev/null; then
                if [ "$k2" = '[' ]; then
                    read -rsN1 -t 0.05 k3 2>/dev/null
                    case "$k3" in
                        A) KEY=UP ;; B) KEY=DOWN ;;
                        C) KEY=RIGHT ;; D) KEY=LEFT ;;
                        *) KEY=ESC ;;
                    esac
                else
                    KEY=ESC
                fi
            else
                KEY=ESC
            fi
            ;;
        $'\r'|$'\n') KEY=ENTER ;;
        q|Q) KEY=QUIT ;;
        *) KEY="$k" ;;
    esac
}

KEYS=(1 2 3 4 5 6 7 8 9)
LABELS=("status" "power on" "power off" "power off HARD" "reset" "raw short click" "raw long click" "raw reset click" "board serial")
HINTS=("read power_state" "graceful start if off" "ACPI / short press" "long hold — last resort" "reset header, hard reboot" "no on/off check" "no on/off check" "no on/off check" "get_sn")
SECTION=(0 0 0 0 0 1 1 1 1)   # 0=power 1=raw/info
BADGES=("READ" "START" "ACPI" "DANGER" "HARD" "RAW" "RAW/HOLD" "RAW" "READ")
BCOLORS=("$CYAN" "$CYAN" "$CYAN" "$RED" "$YEL" "$CYAN" "$RED" "$CYAN" "$CYAN")
sel=0

draw_menu() {
    printf '\033[H'
    printf '%s\n\033[K' "${YEL}  ██████╗ ██╗     ██╗  ██╗██╗   ██╗███╗   ███╗${R}"
    printf '%s\n\033[K' "${ORANGE} ██╔════╝ ██║     ██║ ██╔╝██║   ██║████╗ ████║${R}"
    printf '%s\n\033[K' "${PINK} ██║  ███╗██║     █████╔╝ ██║   ██║██╔████╔██║${R}"
    printf '%s\n\033[K' "${MAG} ██║   ██║██║     ██╔═██╗ ╚██╗ ██╔╝██║╚██╔╝██║${R}"
    printf '%s\n\033[K' "${PURPLE} ╚██████╔╝███████╗██║  ██╗ ╚████╔╝ ██║ ╚═╝ ██║${R}"
    printf '%s\n\033[K' "${CYAN}  ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚═╝     ╚═╝${R}"
    printf '\033[K\n'
    printf '  %sATX%s %s%sremote power console%s  %shost%s %s%s%s%s' \
        "$MUTED" "$R" "$MUTED" "$ITALIC" "$R" \
        "$MUTED" "$R" "$W" "$B" "$HOST" "$R"
    if [ -n "${ATX_TARGET_LABEL:-}" ]; then
        printf '  %starget%s %s%s%s%s' "$MUTED" "$R" "$W" "$B" "$ATX_TARGET_LABEL" "$R"
    fi
    if [ -e "$DEVICE" ]; then
        printf '  %sboard%s %s%sPRESENT%s' "$MUTED" "$R" "$GRN" "$B" "$R"
    else
        printf '  %sboard%s %s%sABSENT%s' "$MUTED" "$R" "$RED" "$B" "$R"
    fi
    printf '\033[K\n'
    grule 50
    printf '\033[K\n'

    local i cur_section=-1
    for i in "${!LABELS[@]}"; do
        if [ "${SECTION[$i]}" -ne "$cur_section" ]; then
            cur_section="${SECTION[$i]}"
            if [ "$cur_section" -eq 0 ]; then
                printf '  %s%sPOWER%s\033[K\n' "$PINK" "$B" "$R"
            else
                printf '  %s%sRAW / INFO%s\033[K\n' "$PURPLE" "$B" "$R"
            fi
        fi
        if [ "$i" -eq "$sel" ]; then
            printf '  %s▶ ‹%s› %-17s%-29s%s%s%9s%s\033[K\n' \
                "${BGSEL}${W}${B}" "${KEYS[$i]}" "${LABELS[$i]}" "${HINTS[$i]}" \
                "$R" "${BGSEL}${BCOLORS[$i]}${B}" "${BADGES[$i]}" "$R"
        else
            printf '    %s‹%s›%s %-17s %s%-29s%s%s%9s%s\033[K\n' \
                "$MUTED" "${KEYS[$i]}" "$R" "${LABELS[$i]}" "$MUTED" "${HINTS[$i]}" \
                "$R" "${BCOLORS[$i]}" "${BADGES[$i]}" "$R"
        fi
    done
    printf '\033[K\n'
    grule 50
    printf '  %s↑↓%s move   %sentr%s select   %s1-9%s jump   %sq%s quit\033[K\n' \
        "$W" "$MUTED" "$W" "$MUTED" "$W" "$MUTED" "$W" "$R"
    printf '\033[J'
}

confirm_dialog() {
    local prompt="$1" yes_sel=1   # 0=yes 1=no, default NO
    while true; do
        printf '\033[H\033[J\n\n'
        printf '    %s╭──────────────────────────────────────────╮%s\033[K\n' "$PINK" "$R"
        printf '    %s│%s  %-42s%s│%s\033[K\n' "$PINK" "$R" "$prompt" "$PINK" "$R"
        printf '    %s│%s' "$PINK" "$R"
        if [ "$yes_sel" -eq 0 ]; then
            printf '     %sYES%s        %sno%s' "${BGSEL}${W}${B}" "$R" "$MUTED" "$R"
        else
            printf '     %syes%s        %sNO%s' "$MUTED" "$R" "${BGSEL}${W}${B}" "$R"
        fi
        printf '                %s│%s\033[K\n' "$PINK" "$R"
        printf '    %s╰──────────────────────────────────────────╯%s\033[K\n' "$PINK" "$R"
        printf '\033[J'
        read_key
        case "$KEY" in
            LEFT|RIGHT|UP|DOWN) yes_sel=$((1 - yes_sel)) ;;
            ENTER) [ "$yes_sel" -eq 0 ] && return 0 || return 1 ;;
            y|Y) return 0 ;;
            n|N|ESC) return 1 ;;
            QUIT) return 1 ;;
        esac
    done
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
    confirm_dialog "$1"
}

run_atx() {
    local cmd="$1"
    local wait_s="${2:-3}"
    local title="$3"
    local out rc st

    printf '\033[H\033[J'
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
    echo "  ${RED}If the attached PC is the machine you're SSHed in from, this ends your session too.${R}"
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

main() {
    # A pty is required — raw mode + arrow-key reads are undefined
    # (and client-dependent) without one. `ssh host command` does NOT
    # allocate a pty by default (only `ssh host` with no command does);
    # this is what actually broke Termius, not a bug in the read loop
    # itself: without a real remote pty, `stty` silently no-ops, and
    # different SSH clients fill that gap differently — some just pass
    # bytes through, but at least one observed client instead echoed
    # raw escape sequences as visible garbage and stopped responding to
    # any key, including Ctrl-C. Confirmed empirically: `ssh host cmd`
    # -> stdin not a tty; `ssh -tt host cmd` or an interactive login
    # followed by running the script manually -> stdin is a tty.
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        echo "This needs a real terminal (pty), not a piped/non-interactive session." >&2
        echo "Log in normally first (ssh <host>), then run this script from that shell —" >&2
        echo "or if your SSH client runs this as a startup command, enable its" >&2
        echo "'force pseudo-terminal' / 'allocate TTY' option for that command." >&2
        exit 1
    fi
    tui_enter
    while true; do
        draw_menu
        read_key
        case "$KEY" in
            UP)   sel=$(( (sel - 1 + ${#LABELS[@]}) % ${#LABELS[@]} )) ;;
            DOWN) sel=$(( (sel + 1) % ${#LABELS[@]} )) ;;
            1|2|3|4|5|6|7|8|9) sel=$((KEY - 1)) ;;
            ENTER)
                printf '\033[H\033[J'
                case "$sel" in
                    0) show_status ;;
                    1) do_on ;;
                    2) do_off ;;
                    3) do_off_hard ;;
                    4) do_reset ;;
                    5) do_click_short ;;
                    6) do_click_long ;;
                    7) do_click_reset ;;
                    8) do_sn ;;
                esac
                echo
                printf '  %spress any key to return%s' "$MUTED" "$R"
                read_key
                ;;
            QUIT) break ;;
        esac
    done
}

main
