[CmdletBinding()]
param(
    [string]$Config = "config/config.local.yaml",
    [switch]$SkipSynthesis,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
$Settings = Get-ComfyUISettings -Config $Config
$BackendUrl = "http://127.0.0.1:$($Settings.backend_port)"
$ComfyUrl = "http://127.0.0.1:$($Settings.port)"

function Invoke-JsonPost {
    param([string]$Uri, $Payload)
    $Json = $Payload | ConvertTo-Json -Depth 30 -Compress
    return Invoke-RestMethod -Uri $Uri -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($Json)) -TimeoutSec 30
}

function Wait-ComfyPrompt {
    param([string]$PromptId, [int]$Timeout)
    $Deadline = (Get-Date).AddSeconds($Timeout)
    do {
        Start-Sleep -Milliseconds 500
        $History = Invoke-RestMethod -Uri "$ComfyUrl/history/$PromptId" -TimeoutSec 15
        $Property = $History.PSObject.Properties[$PromptId]
        if ($Property) {
            $Job = $Property.Value
            if ($Job.status.completed) { return $Job }
        }
    } while ((Get-Date) -lt $Deadline)
    throw "ComfyUI prompt $PromptId did not complete within $Timeout seconds."
}

function Submit-ComfyPrompt {
    param($Prompt, [int]$Timeout = 120)
    $Response = Invoke-JsonPost -Uri "$ComfyUrl/prompt" -Payload @{ prompt = $Prompt }
    if (-not $Response.prompt_id) { throw "ComfyUI did not return prompt_id: $($Response | ConvertTo-Json -Depth 10)" }
    return Wait-ComfyPrompt -PromptId ([string]$Response.prompt_id) -Timeout $Timeout
}

if (-not (Test-LocalHttp -Uri "$BackendUrl/health")) { throw "TTS backend is unavailable at $BackendUrl. Run scripts/start-tts-and-comfyui.ps1 first." }
if (-not (Test-LocalHttp -Uri "$ComfyUrl/system_stats")) { throw "ComfyUI is unavailable at $ComfyUrl. Run scripts/start-tts-and-comfyui.ps1 first." }

& (Join-Path $script:ProjectRoot "integrations\comfyui\test-install.ps1") -ComfyUIPath $Settings.install_path
$Health = Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 15
$Models = Invoke-RestMethod -Uri "$BackendUrl/v1/models" -TimeoutSec 15
$Voices = Invoke-RestMethod -Uri "$BackendUrl/v1/voices" -TimeoutSec 15
$Objects = Invoke-RestMethod -Uri "$ComfyUrl/object_info" -TimeoutSec 30
$ExpectedNodes = @("QwenTTSServer", "QwenTTSSynthesize", "QwenTTSCloneVoice", "QwenTTSVoiceSelector", "QwenTTSEmotionScript", "QwenTTSModels", "QwenTTSHealth")
$Missing = @($ExpectedNodes | Where-Object { $null -eq $Objects.$_ })
if ($Missing.Count) { throw "ComfyUI missing Qwen nodes: $($Missing -join ', ')" }
$WorkflowDirectory = Join-Path $script:ProjectRoot "integrations\comfyui\example_workflows"
$WorkflowFiles = @(Get-ChildItem -LiteralPath $WorkflowDirectory -Filter "*.json" -File)
foreach ($WorkflowFile in $WorkflowFiles) {
    $Workflow = Get-Content -Raw -LiteralPath $WorkflowFile.FullName -Encoding UTF8 | ConvertFrom-Json
    $MissingTypes = @($Workflow.nodes.type | Sort-Object -Unique | Where-Object { $null -eq $Objects.$_ })
    if ($MissingTypes.Count) { throw "Workflow $($WorkflowFile.Name) has missing nodes: $($MissingTypes -join ', ')" }
}
$EmbeddedPython = Join-Path $Settings.install_path "python_embeded\python.exe"
$HasQwenBackend = & $EmbeddedPython -c "import importlib.util; print(importlib.util.find_spec('qwen_tts') is not None)"
if ($LASTEXITCODE -ne 0 -or $HasQwenBackend.Trim() -ne "False") { throw "qwen_tts is unexpectedly installed in ComfyUI embedded Python." }

$DiagnosticPrompt = [ordered]@{
    "1" = @{ class_type = "QwenTTSServer"; inputs = @{ endpoint = $BackendUrl; timeout = 30; model = "tts-1-ru"; response_format = "wav" } }
    "2" = @{ class_type = "QwenTTSHealth"; inputs = @{ server = @("1", 0) } }
    "3" = @{ class_type = "QwenTTSModels"; inputs = @{ server = @("1", 0) } }
    "4" = @{ class_type = "QwenTTSVoiceSelector"; inputs = @{ server = @("1", 0); voice = "clone:QwenDemoRussianNeutral" } }
    "5" = @{ class_type = "QwenTTSEmotionScript"; inputs = @{ text = "[voice:neutral] Тихо. [voice:happy] Радостно! [voice:unknown] Безопасный fallback."; character_profile_mapping = "{`"neutral`":`"clone:QwenDemoRussianNeutral`",`"happy`":`"clone:QwenDemoHappyCandidate`"}" } }
    "6" = @{ class_type = "QwenTTSVoiceSelector"; inputs = @{ server = @("1", 0); voice = "clone:DefinitelyMissingProfile" } }
}
$DiagnosticJob = Submit-ComfyPrompt -Prompt $DiagnosticPrompt -Timeout 120
if ($DiagnosticJob.status.status_str -ne "success") { throw "Diagnostic workflow failed: $($DiagnosticJob.status | ConvertTo-Json -Depth 20)" }
$DiagnosticOutputs = @($DiagnosticJob.outputs.PSObject.Properties.Name)
foreach ($Id in @("2", "3", "4", "5", "6")) {
    if ($DiagnosticOutputs -notcontains $Id) { throw "Diagnostic history has no output for node $Id." }
}
$HealthOutput = $DiagnosticJob.outputs.PSObject.Properties["2"].Value.qwen_tts_health[0]
if ($HealthOutput.status -ne "ok") { throw "Health node did not report backend status ok." }
$ModelsOutput = @($DiagnosticJob.outputs.PSObject.Properties["3"].Value.qwen_tts_models[0])
if (@($ModelsOutput.id) -notcontains "tts-1-ru") { throw "Models node did not return tts-1-ru." }
$EmotionOutput = $DiagnosticJob.outputs.PSObject.Properties["5"].Value.qwen_tts_emotion[0]
if ([string]$EmotionOutput.clean_text -match "\[voice:") { throw "Emotion node left a service voice tag in clean text." }
$MissingVoiceOutput = [string]$DiagnosticJob.outputs.PSObject.Properties["6"].Value.qwen_tts_voices[0]
if ($MissingVoiceOutput -notmatch "not currently available") { throw "Missing voice profile did not produce a clear availability message." }

$SynthesisPromptId = $null
if (-not $SkipSynthesis) {
    $VoiceIds = @($Voices.data | ForEach-Object { $_.voice_id })
    if ($VoiceIds -notcontains "clone:QwenDemoRussianNeutral") { throw "The documented synthetic technical profile clone:QwenDemoRussianNeutral is unavailable; real synthesis was not attempted." }
    $SynthesisPrompt = [ordered]@{
        "1" = @{ class_type = "QwenTTSServer"; inputs = @{ endpoint = $BackendUrl; timeout = $TimeoutSeconds; model = "tts-1-ru"; response_format = "wav" } }
        "2" = @{ class_type = "QwenTTSSynthesize"; inputs = @{ server = @("1", 0); text = "Проверка реального взаимодействия ComfyUI с локальным Qwen TTS backend."; voice = "clone:QwenDemoRussianNeutral"; speed = 1.0; model = "tts-1-ru"; response_format = "wav"; preprocessing_mode = "all"; emotion_script = "" } }
        "3" = @{ class_type = "PreviewAudio"; inputs = @{ audio = @("2", 0) } }
    }
    $Response = Invoke-JsonPost -Uri "$ComfyUrl/prompt" -Payload @{ prompt = $SynthesisPrompt }
    $SynthesisPromptId = [string]$Response.prompt_id
    $SynthesisJob = Wait-ComfyPrompt -PromptId $SynthesisPromptId -Timeout $TimeoutSeconds
    if ($SynthesisJob.status.status_str -ne "success") { throw "Synthesis workflow failed: $($SynthesisJob.status | ConvertTo-Json -Depth 20)" }
    if (@($SynthesisJob.outputs.PSObject.Properties.Name) -notcontains "3") { throw "PreviewAudio output is absent from synthesis history." }
}

$Queue = Invoke-RestMethod -Uri "$ComfyUrl/queue" -TimeoutSec 15
if (@($Queue.queue_running).Count -or @($Queue.queue_pending).Count) { throw "ComfyUI queue is not empty after integration tests." }
[ordered]@{
    backend_status = $Health.status
    backend_model_loaded_after_test = (Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 15).model_loaded
    models = @($Models.data | ForEach-Object { $_.id })
    voices = @($Voices.data | ForEach-Object { $_.voice_id })
    registered_nodes = $ExpectedNodes
    validated_workflows = @($WorkflowFiles.Name)
    qwen_tts_in_comfyui_python = [bool]::Parse($HasQwenBackend.Trim())
    diagnostic_status = $DiagnosticJob.status.status_str
    synthesis_skipped = [bool]$SkipSynthesis
    synthesis_prompt_id = $SynthesisPromptId
    queue_running = @($Queue.queue_running).Count
    queue_pending = @($Queue.queue_pending).Count
} | ConvertTo-Json -Depth 8
