@echo off
rem Panel bota w przegladarce: uruchamia hub (recon\hub.py) JAKO ADMIN i otwiera http://127.0.0.1:8765
rem Wszystko inne (gra, logowanie, radar, opcje) uruchamiasz i ustawiasz juz w przegladarce.
rem Admin jest potrzebny do wstrzykniecia DLL i odczytu pamieci gry przez radary startowane z panelu.
setlocal
pushd "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo Prosze o uprawnienia administratora...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    popd & exit /b
)

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
title Panel bota - hub (zamkniecie okna wylacza panel, radary dzialaja dalej)
%PY% -u recon\hub.py --open
popd
pause
