@echo off
chcp 65001 >nul
title EXEC_FILE_RENAME
cd /d "%~dp0"
where pythonw >nul 2>&1
if errorlevel 1 (
  start "EXEC_FILE_RENAME" python "%~dp0EXEC_FILE_RENAME.py"
) else (
  start "EXEC_FILE_RENAME" pythonw "%~dp0EXEC_FILE_RENAME.py"
)
