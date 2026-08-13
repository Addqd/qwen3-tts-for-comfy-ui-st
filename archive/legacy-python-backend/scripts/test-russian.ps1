[CmdletBinding()]
param(
    [string]$Voice = "clone:QwenDemoRussianNeutral",
    [string]$Url = "http://127.0.0.1:8020",
    [string]$Output = "artifacts/audio-tests/russian-neutral.wav",
    [string]$Text = ""
)
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OutputPath = if ([System.IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $ProjectRoot $Output }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$EscapedText = "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435! \u042d\u0442\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e\u0439 \u0440\u0443\u0441\u0441\u043a\u043e\u0439 \u0440\u0435\u0447\u0438 \u2014 \u0441 \u0432\u043e\u043f\u0440\u043e\u0441\u043e\u043c, \u043c\u043d\u043e\u0433\u043e\u0442\u043e\u0447\u0438\u0435\u043c... \u0438 \u0447\u0438\u0441\u043b\u0430\u043c\u0438: \u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c \u043e\u0434\u0438\u043d."
$InputJson = if ($Text) { $Text | ConvertTo-Json -Compress } else { "`"$EscapedText`"" }
$Body = "{`"model`":`"tts-1-ru`",`"voice`":`"$Voice`",`"input`":$InputJson,`"response_format`":`"wav`",`"speed`":1.0}"
Invoke-WebRequest -UseBasicParsing -Uri "$Url/v1/audio/speech" -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($Body)) -OutFile $OutputPath -TimeoutSec 900
ffprobe -v error -show_entries format=duration -show_entries stream=codec_name,sample_rate,channels -of json $OutputPath
Write-Host "Saved: $OutputPath"
