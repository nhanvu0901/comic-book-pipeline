@echo off
cd /d D:\code\cbp-video-test-p3v
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
set ENABLE_VIDEO_CLIPS=1
set P3V_PORT=8561
D:\code\comic-book-pipeline\.venv\Scripts\python.exe -u _p3v_ui_server.py > _p3v_ui.log 2>&1
echo EXIT=%ERRORLEVEL% >> _p3v_ui.log
