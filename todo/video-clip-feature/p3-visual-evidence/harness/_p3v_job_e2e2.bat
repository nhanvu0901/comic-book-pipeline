@echo off
cd /d D:\code\cbp-video-test-p3v
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
set ENABLE_VIDEO_CLIPS=1
set AUTO_GENERATE_BG_MUSIC=false
set YTDLP_BIN=D:\code\cbp-video-test-p3v\_venv_ytdlp\Scripts\yt-dlp.exe
set CHATTERBOX_VENV=D:\code\comic-book-pipeline\.venv-chatterbox
D:\code\comic-book-pipeline\.venv\Scripts\python.exe -u _p3v_e2e.py > _p3v_e2e2.log 2>&1
echo EXIT=%ERRORLEVEL% >> _p3v_e2e2.log
