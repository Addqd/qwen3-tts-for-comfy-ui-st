[CmdletBinding()]
param(
    [string]$Config = "config/config.local.yaml",
    [switch]$SkipSynthesis,
    [switch]$AllowComfyUIInputWrite,
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
$HasQwenBackend = & $EmbeddedPython -c "import importlib.util; print(importlib.util.find_spec('qwen_tts') is not None)"
if ($LASTEXITCODE -ne 0 -or $HasQwenBackend.Trim() -ne "False") { throw "The qwen_tts neural backend is unexpectedly installed in ComfyUI Python." }

$PromptId = $null
if (-not $SkipSynthesis) {
    if (-not $AllowComfyUIInputWrite) {
        throw "Real synthesis writes a temporary WAV to ComfyUI input. Re-run with -AllowComfyUIInputWrite to confirm, or use -SkipSynthesis."
    }
    $ReferenceDir = Join-Path $script:ProjectRoot "voice_library\profiles\testrudima\neutral"
    $ReferencePath = Join-Path $ReferenceDir "reference.wav"
    $MetadataPath = Join-Path $ReferenceDir "metadata.json"
    if (-not (Test-Path -LiteralPath $ReferencePath) -or -not (Test-Path -LiteralPath $MetadataPath)) {
        throw "Local primary profile is required only for this real integration smoke: $ReferenceDir"
    }
    $ReferenceMetadata = Get-Content -Raw -LiteralPath $MetadataPath -Encoding UTF8 | ConvertFrom-Json
    $SmokeId = "qwentts_comfy_smoke_$([Guid]::NewGuid().ToString('N'))"
    $ComfyInput = Join-Path $Settings.install_path "ComfyUI\input"
    $InputName = "$SmokeId.wav"
    $InputPath = Join-Path $ComfyInput $InputName
    $SmokeProfile = Join-Path $script:ProjectRoot "voice_library\profiles\$SmokeId"
    New-Item -ItemType Directory -Force -Path $ComfyInput | Out-Null
    Copy-Item -LiteralPath $ReferencePath -Destination $InputPath
    try {
        $Text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("0J/RgNC+0LLQtdGA0LrQsCDQt9Cw0LLQtdGA0YjQtdC90LAuINCh0LjRgdGC0LXQvNCwINGA0LDQsdC+0YLQsNC10YIg0YHRgtCw0LHQuNC70YzQvdC+LCDQuCDQstGB0LUg0L3QsNGB0YLRgNC+0LnQutC4INGB0L7RhdGA0LDQvdC10L3Riy4="))
        $Prompt = [ordered]@{
            "1" = @{ class_type="QwenTTSServer"; inputs=@{ endpoint=$BackendUrl; timeout=$TimeoutSeconds; response_format="wav" } }
            "2" = @{ class_type="QwenTTSRuntimeSettings"; inputs=@{ server=@("1",0); apply_and_save=$false; language="Russian"; russian_normalization="Full Russian"; seed=-1; max_new_tokens=4096; temperature=0.75; top_k=40; top_p=0.9; repetition_penalty=1.05; pronunciation_defaults="" } }
            "3" = @{ class_type="LoadAudio"; inputs=@{ audio=$InputName } }
            "4" = @{ class_type="QwenTTSCloneVoice"; inputs=@{ server=@("2",0); reference_audio=@("3",0); ref_text=[string]$ReferenceMetadata.ref_text; profile_name=$SmokeId; character_name="Integration Smoke"; language="Russian"; overwrite=$false } }
            "5" = @{ class_type="QwenTTSSynthesize"; inputs=@{ server=@("2",0); text=$Text; voice=@("4",0); speed=1.0; response_format="wav"; russian_normalization="Use Backend Default" } }
            "6" = @{ class_type="PreviewAudio"; inputs=@{ audio=@("5",0) } }
        }
        $Response = Invoke-JsonPost -Uri "$ComfyUrl/prompt" -Payload @{ prompt=$Prompt }
        $PromptId = [string]$Response.prompt_id
        $Job = Wait-ComfyPrompt -PromptId $PromptId -Timeout $TimeoutSeconds
        if ($Job.status.status_str -ne "success" -or @($Job.outputs.PSObject.Properties.Name) -notcontains "6") { throw "Canonical ComfyUI clone/synthesis/PreviewAudio smoke failed." }
    } finally {
        Remove-Item -LiteralPath $InputPath -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $SmokeProfile) {
            $ResolvedProfiles = [IO.Path]::GetFullPath((Join-Path $script:ProjectRoot "voice_library\profiles")).TrimEnd([char[]]"\/")
            $ResolvedSmoke = [IO.Path]::GetFullPath($SmokeProfile)
            if (-not $ResolvedSmoke.StartsWith($ResolvedProfiles + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
                (Split-Path -Leaf $ResolvedSmoke) -notlike "qwentts_comfy_smoke_*") {
                throw "Refusing unsafe integration-smoke cleanup path: $ResolvedSmoke"
            }
            Remove-Item -LiteralPath $ResolvedSmoke -Recurse -Force
        }
    }
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
    canonical_full_graph=(-not [bool]$SkipSynthesis)
    heavy_backend_in_comfyui_python=[bool]::Parse($HasQwenBackend.Trim())
    synthesis_skipped=[bool]$SkipSynthesis
    synthesis_prompt_id=$PromptId
} | ConvertTo-Json -Depth 6
