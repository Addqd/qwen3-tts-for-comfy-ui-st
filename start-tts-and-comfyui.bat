@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"

echo Starting Qwen3-TTS backend and ComfyUI...
echo A separate ComfyUI Python console will open if ComfyUI is not already running.
echo Keep this launcher window open while you use the services.
echo Close this window or the ComfyUI Python console to stop BOTH services.
echo.
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\start-tts-and-comfyui.ps1" -VisibleComfyUIConsole -WaitForComfyUIExit
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
echo Launcher finished. Backend and ComfyUI have been stopped.
popd
endlocal
