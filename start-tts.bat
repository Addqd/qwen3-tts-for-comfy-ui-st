@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"

echo Starting standalone Qwen3-TTS backend...
echo Local API: http://127.0.0.1:8020
echo This launcher does not start SillyTavern or ComfyUI.
echo.
powershell.exe -NoLogo -NoProfile -File "%~dp0start.ps1"
set "START_EXIT_CODE=%ERRORLEVEL%"
if not "%START_EXIT_CODE%"=="0" (
    echo.
    echo Qwen3-TTS startup failed with exit code %START_EXIT_CODE%.
    echo Review logs in the project logs folder.
    pause
)
popd
exit /b %START_EXIT_CODE%
