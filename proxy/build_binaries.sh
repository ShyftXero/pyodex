#!/usr/bin/env bash
# Build standalone python_websocket_proxy binaries with haru-pack (thick tier: offline, no deps on target).
#   bash proxy/build_binaries.sh                 # host + windows-x86_64
#   bash proxy/build_binaries.sh linux-aarch64   # explicit target list
set -euo pipefail
cd "$(dirname "$0")"
OUT=../dist/proxy
mkdir -p "$OUT"
uvx haru-pack doctor >/dev/null 2>&1 || uvx haru-pack bootstrap

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(host windows-x86_64)

for t in "${targets[@]}"; do
  ext=""; [[ "$t" == windows* ]] && ext=".exe"
  name="python_websocket_proxy"; [[ "$t" != host ]] && name="python_websocket_proxy-$t"
  uvx haru-pack python_websocket_proxy.py --thick --target "$t" --out "$OUT/$name$ext"
done
ls -la "$OUT"
