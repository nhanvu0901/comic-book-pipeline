@echo off
rem usage: job_py.cmd <logfile-name> <script.py> <args...>
set LOGF=D:\code\cbp-video-test-p1\%1
set SCRIPT=D:\code\cbp-video-test-p1\%2
for /f "tokens=2*" %%a in ("%*") do set REST=%%b
D:\code\comic-book-pipeline\.venv\Scripts\python.exe -u %SCRIPT% %REST% > %LOGF% 2>&1
echo EXIT=%ERRORLEVEL% >> %LOGF%
