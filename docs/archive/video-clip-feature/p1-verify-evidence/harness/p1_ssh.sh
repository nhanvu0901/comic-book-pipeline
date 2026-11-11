#!/bin/bash
# retry wrapper for the flaky winbox-lan SSH: p1_ssh.sh "<cmd.exe command>"
for i in 1 2 3 4 5 6; do
  out=$(ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 -o BatchMode=yes winbox-lan "$@" 2>&1); rc=$?
  if [ $rc -ne 255 ]; then echo "$out"; exit $rc; fi
  echo "[ssh retry $i rc=255: $(echo "$out" | tail -1)]" >&2
  sleep $((i*8))
done
echo "$out"; exit 255
