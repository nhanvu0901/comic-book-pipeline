#!/bin/bash
# keep an ssh -L tunnel to the P1 UI server (localhost:8560 on winbox-lan) alive through flaky SSH
while true; do
  ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o ConnectTimeout=20 \
      -L 8560:127.0.0.1:8560 winbox-lan
  echo "[tunnel] ssh exited rc=$? — retry in 5s"; sleep 5
done
