@echo off
rem usage: job_cli.cmd <ENABLE_VIDEO_CLIPS 0|1> <logfile-name> <module> <args...>
cd /d D:\code\cbp-video-test-p1\repo
set ENABLE_VIDEO_CLIPS=%1
set LOGF=D:\code\cbp-video-test-p1\%2
set MODULE=%3
for /f "tokens=3*" %%a in ("%*") do set REST=%%b
set CHATTERBOX_VENV=D:\code\comic-book-pipeline\.venv-chatterbox
set YTDLP_BIN=D:\code\cbp-video-test-p1\ytvenv\Scripts\yt-dlp.exe
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
D:\code\comic-book-pipeline\.venv\Scripts\python.exe -u -m %MODULE% %REST% > %LOGF% 2>&1
echo EXIT=%ERRORLEVEL% >> %LOGF%
