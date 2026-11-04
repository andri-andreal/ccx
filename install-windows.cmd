@echo off
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -X utf8 "%~dp0windows\install_windows.py" %*
exit /b %errorlevel%
:python
python -X utf8 "%~dp0windows\install_windows.py" %*
exit /b %errorlevel%
