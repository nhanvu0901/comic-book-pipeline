#!/bin/bash
rm -rf /tmp/p1_work/chrome-profile
exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --remote-debugging-port=9333 \
  --user-data-dir=/tmp/p1_work/chrome-profile --window-size=1400,1000 --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check about:blank
