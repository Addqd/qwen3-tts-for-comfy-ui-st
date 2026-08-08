<#
.SYNOPSIS
Tests the running SillyTavern TTS proxy against the running Qwen3-TTS backend.
.DESCRIPTION
Performs transient localhost requests only. It does not start or stop either
service and does not modify SillyTavern settings, cards, chats, prompts, Regex,
or Voice Map. Audio is written only to the project's ignored artifacts folder.
.PARAMETER BackendUrl
Qwen3-TTS base URL. Only http://127.0.0.1:<port> is accepted.
.PARAMETER SillyTavernUrl
Already running SillyTavern URL. Only http://127.0.0.1:<port> is accepted.
.PARAMETER Voice
Existing neutral profile used as the voice-family base.
.PARAMETER TimeoutSeconds
Maximum duration of each synthesis request.
.EXAMPLE
.\scripts\test-sillytavern-integration.ps1 -Voice clone:test_ru_dima_neutral
#>
[CmdletBinding()]
param(
    [string]$BackendUrl = "http://127.0.0.1:8020",
    [string]$SillyTavernUrl = "http://127.0.0.1:8000",
    [string]$Voice = "clone:test_ru_dima_neutral",
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Assert-LocalBaseUrl {
    param([Parameter(Mandatory)][string]$Value)
    $Uri = [Uri]$Value
    if ($Uri.Scheme -ne "http" -or $Uri.Host -ne "127.0.0.1" -or $Uri.IsDefaultPort -or $Uri.AbsolutePath -ne "/") {
        throw "Only http://127.0.0.1:<port> base URLs are allowed: $Value"
    }
    return $Value.TrimEnd("/")
}

$BackendUrl = Assert-LocalBaseUrl -Value $BackendUrl
$SillyTavernUrl = Assert-LocalBaseUrl -Value $SillyTavernUrl
try { $Health = Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 15 } catch { throw "Qwen3-TTS is unavailable at $BackendUrl" }
try { Invoke-WebRequest -Uri $SillyTavernUrl -TimeoutSec 15 -UseBasicParsing | Out-Null } catch { throw "SillyTavern is unavailable at $SillyTavernUrl. Start its existing Start.bat yourself." }

$Models = Invoke-RestMethod -Uri "$BackendUrl/v1/models" -TimeoutSec 15
$Voices = Invoke-RestMethod -Uri "$BackendUrl/v1/voices" -TimeoutSec 15
if (@($Models.data.id) -notcontains "tts-1-ru") { throw "Backend model tts-1-ru is unavailable." }
if (@($Voices.data.voice_id) -notcontains $Voice) { throw "Backend voice is unavailable: $Voice" }

$ArtifactDirectory = Join-Path $ProjectRoot "artifacts\audio-tests"
New-Item -ItemType Directory -Force -Path $ArtifactDirectory | Out-Null
$ProxyPath = Join-Path $ArtifactDirectory "sillytavern-quote-aware-proxy-smoke.mp3"
$CsrfResponse = Invoke-WebRequest -Uri "$SillyTavernUrl/csrf-token" -SessionVariable SillySession -TimeoutSec 15 -UseBasicParsing
$Token = ($CsrfResponse.Content | ConvertFrom-Json).token
if (-not $Token) { throw "SillyTavern did not return a CSRF token." }

$Text = 'Она посмотрела в сторону двери. [voice:happy] "Ты пришёл!" Она улыбнулась. [voice:soft] "Я правда тебя ждала."'
$Payload = @{
    provider_endpoint = "$BackendUrl/v1/audio/speech"
    model = "tts-1-ru"
    input = $Text
    voice = $Voice
    response_format = "mp3"
    speed = 1
} | ConvertTo-Json -Compress
Invoke-WebRequest -Uri "$SillyTavernUrl/api/openai/custom/generate-voice" -WebSession $SillySession -Headers @{ "X-CSRF-Token" = $Token } -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($Payload)) -OutFile $ProxyPath -TimeoutSec $TimeoutSeconds -UseBasicParsing
if ((Get-Item -LiteralPath $ProxyPath).Length -lt 1024) { throw "SillyTavern proxy returned an unexpectedly small audio file." }

$Metrics = Invoke-RestMethod -Uri "$BackendUrl/metrics" -TimeoutSec 15
[ordered]@{
    backend_status = $Health.status
    backend_mode = $Health.mode
    model = "tts-1-ru"
    voice_family_base = $Voice
    sillytavern_proxy = "$SillyTavernUrl/api/openai/custom/generate-voice"
    audio_path = $ProxyPath
    audio_bytes = (Get-Item -LiteralPath $ProxyPath).Length
    styles = $Metrics.last.styles
    segment_types = $Metrics.last.segment_types
    selected_voices = $Metrics.last.voices
    router_warnings = $Metrics.last.router_warnings
    persistent_sillytavern_changes = $false
    browser_playback = "not_tested"
} | ConvertTo-Json -Depth 8
