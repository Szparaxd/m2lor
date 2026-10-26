@echo off
rem Buduje 32-bitowe autopot.dll i inject.exe pod GameCoreTop.exe / python27.dll.
rem Uruchom przez dwuklik albo z dowolnego terminala: build.bat  (sam ustawia vcvars32).
setlocal enabledelayedexpansion
pushd "%~dp0"

set "VCV=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
if not exist "%VCV%" (
    echo [!] Nie znalazlem vcvars32.bat:
    echo     "%VCV%"
    echo     Zainstaluj "Desktop development with C++" albo popraw sciezke w build.bat.
    popd & exit /b 1
)
call "%VCV%" >nul
if errorlevel 1 ( echo [!] vcvars32 zawiodl & popd & exit /b 1 )

echo === Buduje dll\autopot.dll (32-bit) ===
cl /nologo /O2 /MT /LD dll\autopot_dll.cpp /Fe:dll\autopot.dll /Fo:dll\ ^
   /link user32.lib kernel32.lib
if errorlevel 1 ( echo [!] Blad budowy DLL & popd & exit /b 1 )

echo === Buduje injector\inject.exe (32-bit) ===
cl /nologo /O2 /MT injector\inject.cpp /Fe:injector\inject.exe /Fo:injector\ ^
   /link kernel32.lib
if errorlevel 1 ( echo [!] Blad budowy injektora & popd & exit /b 1 )

del dll\*.obj injector\*.obj dll\autopot.exp dll\autopot.lib 2>nul

echo.
echo === Gotowe ===
echo   dll\autopot.dll
echo   injector\inject.exe
popd
