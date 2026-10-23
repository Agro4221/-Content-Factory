# Скрипт проверки после обновления драйвера NVIDIA.
# Запуск: powershell -NoProfile -File tools\check_gpu_setup.ps1
#
# Что делает:
# 1) проверяет, что видеокарта видна и драйвер поддерживает CUDA 12;
# 2) переключает LM Studio на CUDA-бэкенд;
# 3) грузит модель с оптимальными параметрами (GPU на 100%, контекст 16384,
#    один поток предсказания);
# 4) замеряет скорость ответа и проверяет, что модель отвечает.

$ErrorActionPreference = "Stop"

$LMS = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
$MODEL = "huihui-qwythos-9b-claude-mythos-5-1m-abliterated"
$API = "http://127.0.0.1:1234/v1/chat/completions"

function Say($text) { Write-Host $text }

Say "=== Проверка GPU-настройки ==="

# --- 1. Драйвер и CUDA ---
$smi = Join-Path $env:System32 "nvidia-smi.exe"
if (-not (Test-Path $smi)) {
    Say "!! NVIDIA не найдена. Продолжаю с проверкой по остальным пунктам."
} else {
    $info = & $smi 2>&1 | Out-String
    if ($info -match "CUDA Version:\s*(\d+\.\d+)") {
        $cuda = $Matches[1]
        Say "Драйвер поддерживает CUDA $cuda"
        $cudaMajor = [int]($cuda -split "\.")[0]
        if ($cudaMajor -lt 12) {
            Say "!! CUDA $cuda старее, чем нужно LM Studio (нужен CUDA 12)."
            Say "   Обновите драйвер NVIDIA до версии 525.60.13 или новее."
        } else {
            Say "OK: CUDA $cuda подходит для бэкенда LM Studio."
        }
    }
    $gpu = & $smi --query-gpu=name,memory.total,memory.used --format=csv,noheader 2>&1 | Select-Object -First 1
    Say "Видеокарта: $gpu"
}

# --- 2. Движок LM Studio ---
if (Test-Path $LMS) {
    Say ""
    Say "Переключаю движок на CUDA 12..."
    & $LMS runtime select "llama.cpp-win-x86_64-nvidia-cuda12-avx2@2.62.1" | Out-Null
    & $LMS unload --all | Out-Null
    Start-Sleep -Seconds 5

    Say "Загружаю модель: GPU 100%, контекст 16384, один поток..."
    & $LMS load $MODEL --gpu max --context-length 16384 --parallel 1 -y | Out-Null
    Start-Sleep -Seconds 10

    $ps = Get-CimInstance Win32_Process | Where-Object { $_.Name -like "*llama*" }
    foreach ($p in $ps) {
        $args = ($p.CommandLine -split " --" | Where-Object { $_ -match "threads|n-gpu-layers|ctx-size" })
        Say "  args: $($args -join '  ')"
        if ($p.CommandLine -match "--n-gpu-layers 0") {
            Say "  ВНИМАНИЕ: модель осталась на CPU (n-gpu-layers 0)."
        }
    }

    $vram = & $smi --query-gpu=memory.used --format=csv,noheader 2>&1 | Select-Object -First 1
    Say "  VRAM занято: $vram"
} else {
    Say "!! lms.exe не найден — LM Studio не установлен или другой путь."
}

# --- 3. Замер скорости ---
Say ""
Say "Замеряю скорость ответа..."
$body = @{
    model = $MODEL
    messages = @(@{ role = "user"; content = "Ответь одним словом: работает" })
    temperature = 0.4
} | ConvertTo-Json -Depth 5

$watch = [Diagnostics.Stopwatch]::StartNew()
try {
    $response = Invoke-RestMethod -Uri $API -Method Post -Body $body -ContentType "application/json" -TimeoutSec 600
    $watch.Stop()
    $usage = $response.usage
    Say ("OK: ответ за {0:N1} с" -f $watch.Elapsed.TotalSeconds)
    Say ("  токены промпта: {0}, ответа: {1} (из них размышлений: {2})" -f `
        $usage.prompt_tokens, $usage.completion_tokens, $usage.completion_tokens_details.reasoning_tokens)
    if ($usage.completion_tokens -gt 0) {
        $tps = $usage.completion_tokens / $watch.Elapsed.TotalSeconds
        Say ("  скорость: {0:N1} токенов/с" -f $tps)
    }
} catch {
    $watch.Stop()
    Say ("!! Ошибка запроса: {0}" -f $_.Exception.Message)
}

Say ""
Say "Готово."
