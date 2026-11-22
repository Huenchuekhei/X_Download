@echo off
setlocal
if "%~1"=="" goto web
if /i "%~1"=="--web" goto web
if /i "%~1"=="--gui" goto web
if /i "%~1"=="--lan" goto lan
if /i "%~1"=="--desktop" goto desktop
py -3.12 "C:\Users\Huenchuekhei\Tools\x_downloader.py" %*
exit /b %errorlevel%

:web
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'C:\Users\Huenchuekhei\AppData\Local\Programs\Python\Launcher\pyw.exe' -ArgumentList '-3.12','C:\Users\Huenchuekhei\Tools\xdl_gui.py','--web' -WindowStyle Hidden"
echo XDL Web started in background: http://127.0.0.1:8765/
echo Use 'xdl --lan' to allow mobile/LAN access, or stop the service from the web UI.
exit /b 0

:lan
py -3.12 "C:\Users\Huenchuekhei\Tools\xdl_gui.py" --web --lan
exit /b %errorlevel%

:desktop
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'C:\Users\Huenchuekhei\AppData\Local\Programs\Python\Launcher\pyw.exe' -ArgumentList '-3.12','C:\Users\Huenchuekhei\Tools\xdl_gui.py','--desktop' -WindowStyle Hidden"
echo XDL Desktop started in background.
exit /b 0
