@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"

echo Starting Qwen3-TTS backend and ComfyUI...
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\start-tts-and-comfyui.ps1" -VisibleComfyUIConsole
set "START_EXIT_CODE=%ERRORLEVEL%"

if not "%START_EXIT_CODE%"=="0" (
    echo.
    echo Startup failed with exit code %START_EXIT_CODE%.
    echo Review the messages above and logs in the project logs folder.
    pause
    popd
    exit /b %START_EXIT_CODE%
)

echo.
echo Services are ready:
echo   TTS backend: http://127.0.0.1:8020
echo   ComfyUI:     http://127.0.0.1:8188
echo.
echo You can close this window. Use stop.ps1 and scripts\stop-comfyui.ps1 to stop the services.
popd
endlocal
