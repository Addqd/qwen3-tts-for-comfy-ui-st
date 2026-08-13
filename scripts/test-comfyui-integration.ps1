[CmdletBinding()]
param([string]$Config = "config/config.local.yaml", [switch]$SkipSynthesis, [int]$TimeoutSeconds = 900)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "comfyui-common.ps1")
$Settings = Get-ComfyUISettings -Config $Config
$BackendUrl = "http://127.0.0.1:$($Settings.backend_port)"
$ComfyUrl = "http://127.0.0.1:$($Settings.port)"

function Invoke-JsonPost {
    param([string]$Uri, $Payload)
    $Json = $Payload | ConvertTo-Json -Depth 30 -Compress
    Invoke-RestMethod -Uri $Uri -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($Json)) -TimeoutSec 30
}

function Wait-ComfyPrompt {
    param([string]$PromptId, [int]$Timeout)
    $Deadline = (Get-Date).AddSeconds($Timeout)
    do {
        Start-Sleep -Milliseconds 500
        $History = Invoke-RestMethod -Uri "$ComfyUrl/history/$PromptId" -TimeoutSec 15
        $Property = $History.PSObject.Properties[$PromptId]
        if ($Property -and $Property.Value.status.completed) { return $Property.Value }
    } while ((Get-Date) -lt $Deadline)
    throw "ComfyUI prompt $PromptId did not complete within $Timeout seconds."
}

if (-not (Test-LocalHttp -Uri "$BackendUrl/health")) { throw "TTS facade is unavailable at $BackendUrl." }
if (-not (Test-LocalHttp -Uri "$ComfyUrl/system_stats")) { throw "ComfyUI is unavailable at $ComfyUrl." }
& (Join-Path $script:ProjectRoot "integrations\comfyui\test-install.ps1") -ComfyUIPath $Settings.install_path
$Health = Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 15
$Objects = Invoke-RestMethod -Uri "$ComfyUrl/object_info" -TimeoutSec 30
Assert-QwenTTSCloneVoiceSchema -Objects $Objects
$ExpectedNodes = @("QwenTTSServer", "QwenTTSRuntimeSettings", "QwenTTSSynthesize", "QwenTTSCloneVoice", "QwenTTSVoiceSelector", "QwenTTSModels", "QwenTTSHealth")
$Missing = @($ExpectedNodes | Where-Object { $null -eq $Objects.$_ })
if ($Missing.Count) { throw "ComfyUI missing Qwen nodes: $($Missing -join ', ')" }

$WorkflowPath = Join-Path $script:ProjectRoot "integrations\comfyui\example_workflows\voice_profile_from_wav_ru.json"
$Workflow = Get-Content -Raw -LiteralPath $WorkflowPath -Encoding UTF8 | ConvertFrom-Json
if ([int]$Workflow.extra.qwen_tts_workflow_schema -ne 4) { throw "Canonical workflow schema marker is stale." }
$MissingTypes = @($Workflow.nodes.type | Sort-Object -Unique | Where-Object { $null -eq $Objects.$_ })
if ($MissingTypes.Count) { throw "Canonical workflow has missing nodes: $($MissingTypes -join ', ')" }

$EmbeddedPython = Join-Path $Settings.install_path "python_embeded\python.exe"
$HasHeavyBackend = & $EmbeddedPython -c "import importlib.util; print(any(importlib.util.find_spec(x) is not None for x in ('qwen_tts','torch','transformers')))"
if ($LASTEXITCODE -ne 0 -or $HasHeavyBackend.Trim() -ne "False") { throw "A neural Python backend is unexpectedly installed in ComfyUI Python." }

$PromptId = $null
if (-not $SkipSynthesis) {
    $Text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("0J/RgNC+0LLQtdGA0LrQsCDQt9Cw0LLQtdGA0YjQtdC90LAuINCh0LjRgdGC0LXQvNCwINGA0LDQsdC+0YLQsNC10YIg0YHRgtCw0LHQuNC70YzQvdC+LCDQuCDQstGB0LUg0L3QsNGB0YLRgNC+0LnQutC4INGB0L7RhdGA0LDQvdC10L3Riy4="))
    $Prompt = [ordered]@{
        "1" = @{ class_type="QwenTTSServer"; inputs=@{ endpoint=$BackendUrl; timeout=$TimeoutSeconds; response_format="wav" } }
        "2" = @{ class_type="QwenTTSSynthesize"; inputs=@{ server=@("1",0); text=$Text; voice="clone:test_ru_dima_neutral"; speed=1.0; response_format="wav"; russian_normalization="Use Backend Default" } }
        "3" = @{ class_type="PreviewAudio"; inputs=@{ audio=@("2",0) } }
    }
    $Response = Invoke-JsonPost -Uri "$ComfyUrl/prompt" -Payload @{ prompt=$Prompt }
    $PromptId = [string]$Response.prompt_id
    $Job = Wait-ComfyPrompt -PromptId $PromptId -Timeout $TimeoutSeconds
    if ($Job.status.status_str -ne "success" -or @($Job.outputs.PSObject.Properties.Name) -notcontains "3") { throw "ComfyUI synthesis/PreviewAudio smoke failed." }
}

$Queue = Invoke-RestMethod -Uri "$ComfyUrl/queue" -TimeoutSec 15
if (@($Queue.queue_running).Count -or @($Queue.queue_pending).Count) { throw "ComfyUI queue is not empty after smoke test." }
[ordered]@{
    backend_status=$Health.status
    engine=$Health.engine
    default_voice=$Health.default_voice
    registered_nodes=$ExpectedNodes
    canonical_workflow=(Split-Path -Leaf $WorkflowPath)
    workflow_schema=$Workflow.extra.qwen_tts_workflow_schema
    heavy_backend_in_comfyui_python=[bool]::Parse($HasHeavyBackend.Trim())
    synthesis_skipped=[bool]$SkipSynthesis
    synthesis_prompt_id=$PromptId
} | ConvertTo-Json -Depth 6
