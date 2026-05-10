#!/usr/bin/env bash
# Attach the RTL-SDR dongle to WSL via usbipd-win.
# Run after every physical replug.
#
# Prerequisite (one-time, from an elevated Windows PowerShell):
#   usbipd bind --busid <BUSID>
set -euo pipefail

USBIPD="/mnt/c/Program Files/usbipd-win/usbipd.exe"
WSL_DISTRO="${WSL_DISTRO:-Ubuntu}"

# RTL2832U chip IDs (RTL-SDR Blog v3 = 0bda:2838).
KNOWN_IDS=("0bda:2838" "0bda:2832" "0bda:2831")

if [[ ! -x "$USBIPD" ]]; then
    echo "usbipd.exe not found at: $USBIPD" >&2
    echo "Install usbipd-win from https://github.com/dorssel/usbipd-win/releases" >&2
    exit 1
fi

list_output=$("$USBIPD" list 2>/dev/null | tr -d '\r')

busid=""
matched_id=""
for vid_pid in "${KNOWN_IDS[@]}"; do
    busid=$(echo "$list_output" | grep -i "$vid_pid" | awk '{print $1}' | head -1)
    if [[ -n "$busid" ]]; then
        matched_id="$vid_pid"
        break
    fi
done

if [[ -z "$busid" ]]; then
    echo "RTL-SDR not found. Tried: ${KNOWN_IDS[*]}" >&2
    echo "" >&2
    echo "usbipd list:" >&2
    echo "$list_output" >&2
    exit 1
fi

echo "Found $matched_id at busid $busid -> attaching to WSL distro '$WSL_DISTRO'"

attach_out=$("$USBIPD" attach --wsl "$WSL_DISTRO" --busid "$busid" 2>&1 | tr -d '\r')
echo "$attach_out"

if echo "$attach_out" | grep -qi "not shared"; then
    echo "" >&2
    echo "Device isn't shared yet. From an ELEVATED Windows PowerShell, run once:" >&2
    echo "  usbipd bind --busid $busid" >&2
    exit 1
fi

if echo "$attach_out" | grep -qiE "error|failed"; then
    exit 1
fi
