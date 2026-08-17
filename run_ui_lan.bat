@echo off
REM Review UI served to the local network — see ui/__main__.py for the security note.
cd /d D:\code\comic-book-pipeline
.venv\Scripts\python.exe -m ui --lan %*
