@echo off
cd /d D:\code\cbp-video-test-p1\repo
set ENABLE_VIDEO_CLIPS=%1
set CHATTERBOX_VENV=D:\code\comic-book-pipeline\.venv-chatterbox
rem the prod venv's yt-dlp (2026.07.04) gets HTTP 403 from YouTube even for a plain download; use a scratch, current one
set YTDLP_BIN=D:\code\cbp-video-test-p1\ytvenv\Scripts\yt-dlp.exe
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
D:\code\comic-book-pipeline\.venv\Scripts\python.exe -u D:\code\cbp-video-test-p1\ui_server_e2e.py 8560 > D:\code\cbp-video-test-p1\ui_8560.log 2>&1
