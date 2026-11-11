@echo off
cd /d D:\code\cbp-video-test-p1\repo
set ENABLE_VIDEO_CLIPS=0
set PYTHONUTF8=1
D:\code\comic-book-pipeline\.venv\Scripts\python.exe -m pytest -q -W ignore -p no:cacheprovider > D:\code\cbp-video-test-p1\suite_win.txt 2>&1
echo EXIT=%ERRORLEVEL% >> D:\code\cbp-video-test-p1\suite_win.txt
