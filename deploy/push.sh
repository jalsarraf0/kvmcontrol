#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Deploy to explicitly named SSH hosts, one at a time. Never copies git or notes.
set -eu
if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <ssh-host> [<ssh-host> ...]" >&2
    exit 2
fi
DEPLOY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for host in "$@"; do
    case "$host" in *[!a-zA-Z0-9_.@-]*|-*|'') echo "Invalid SSH host" >&2; exit 2 ;; esac
    echo "Deploying to $host"
    stage=$(mktemp -d /tmp/kvmcontrol-stage.XXXXXX)
    trap 'rm -rf "$stage"' EXIT
    tar -C "$DEPLOY_ROOT" -cf - atx_console.py atx-console.sh automation web deploy LICENSE | tar -xf - -C "$stage"
    if [ -f "$DEPLOY_ROOT/local/fleet.$host.json" ]; then
        mkdir -p "$stage/local"
        cp "$DEPLOY_ROOT/local/fleet.$host.json" "$stage/local/fleet.json"
    fi
    remote_dir=$(ssh -o BatchMode=yes -o ConnectTimeout=12 "$host" 'mktemp -d /tmp/kvmcontrol-install.XXXXXX')
    case "$remote_dir" in /tmp/kvmcontrol-install.*) ;; *) echo "Invalid staging path" >&2; exit 2 ;; esac
    # Tar over SSH works with Dropbear without requiring an SFTP subsystem.
    tar -C "$stage" -cf - . |
        ssh -o BatchMode=yes -o ConnectTimeout=12 "$host" "tar -xf - -C '$remote_dir'"
    ssh -o BatchMode=yes -o ConnectTimeout=12 "$host" "python3 '$remote_dir/deploy/install.py'"
    ssh -o BatchMode=yes -o ConnectTimeout=12 "$host" "rm -rf '$remote_dir'"
    rm -rf "$stage"
    trap - EXIT
done
