# 지워진 코드 검색엔진 - 대시보드·검색 한 번에 띄우기 (Windows PowerShell 5.1 이상)
#
# 탐색기에서는 run.cmd 를 더블클릭한다 (실행 정책을 이 실행에만 풀어 준다).
# 터미널에서는:
#   .\run.ps1
#   powershell -ExecutionPolicy Bypass -File .\run.ps1    # 실행 정책 때문에 막히면
#
# 필요한 것: Python 3.12, Node.js 20 이상. 데이터 자동 받기에는 GitHub CLI(gh, 로그인 상태)가 필요하다.
# 이 파일은 BOM 이 있는 UTF-8 이어야 한다. BOM 이 없으면 PowerShell 5.1 이 한글을 깨뜨려 읽는다.
param(
    [switch]$NoBrowser  # 브라우저를 열지 않는다 (확인·자동화용)
)

$Root = $PSScriptRoot
Set-Location $Root

$ApiPort = 8000
$WebPort = 5173
$ApiUrl = "http://127.0.0.1:$ApiPort"
$WebUrl = "http://localhost:$WebPort"
$WebTitle = "<title>Deleted Code Search</title>"  # web/index.html
$DataDir = "data\export\dataset-v0.9"
$IndexFile = "data\search\dataset-v0.9.sqlite"
$SemanticManifest = "data\search\semantic\manifest.json"
$ReleaseUrl = "https://github.com/Deleted-Code-Search/Deleted-Code-Search/releases/tag/dataset-v0.9"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

# 아래 파이썬 창들도 이 값을 물려받는다. 한글 로그가 깨지지 않게 한다.
$env:PYTHONUTF8 = "1"

function Say([string]$Message) { Write-Host "`n[run] $Message" -ForegroundColor Cyan }
function Note([string]$Message) { Write-Host "      $Message" -ForegroundColor Yellow }

function Stop-WithPause([string]$Message) {
    Write-Host "`n[run] 멈춤: $Message" -ForegroundColor Red
    try { Read-Host "엔터를 누르면 닫습니다" | Out-Null } catch { }
    exit 1
}

function Find-Python {
    # py 는 3.12 를 직접 고를 수 있어 먼저 본다. Microsoft Store 가짜 python 은 True 를 찍지 못해 걸러진다.
    $check = "import sys; print(sys.version_info >= (3, 12))"
    foreach ($candidate in @(@("py", "-3.12"), @("python"), @("python3"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $prefix = @($candidate | Select-Object -Skip 1)
        $answer = & $exe @prefix -c $check 2>$null
        if ($answer -eq "True") { return , $candidate }
    }
    return $null
}

function Test-Imports {
    & $VenvPython -c "import fastapi, uvicorn, fastembed, numpy" 2>$null
    return $LASTEXITCODE -eq 0
}

function Test-Port([string]$HostName, [int]$Port) {
    # TcpClient 기본 생성자는 IPv4 만 본다. Vite 는 localhost 를 ::1 에만 열 수 있어 주소마다 시도한다.
    foreach ($address in [Net.Dns]::GetHostAddresses($HostName)) {
        $client = New-Object Net.Sockets.TcpClient($address.AddressFamily)
        try {
            $client.Connect($address, $Port)
            return $true
        } catch {
            continue
        } finally {
            $client.Close()
        }
    }
    return $false
}

# 새 창에서 명령을 돌린다. -EncodedCommand 라 경로의 공백·따옴표·한글에 걸리지 않는다.
function Start-Window([string]$Title, [string]$Command) {
    $script = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location -LiteralPath '$($Root.Replace("'", "''"))'; $Command"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell -ArgumentList "-NoExit", "-EncodedCommand", $encoded | Out-Null
}

function Wait-Port([string]$HostName, [int]$Port, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port -HostName $HostName -Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# 포트가 열려 있어도 우리 서버라는 보장이 없다. 응답 내용으로 확인한다.
function Test-OurApi {
    try {
        $health = Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 5
        return ($health.status -eq "ok") -and ($null -ne $health.search_index)
    } catch {
        return $false
    }
}

function Test-OurWeb {
    try {
        $page = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 5
        return $page.Content.Contains($WebTitle)
    } catch {
        return $false
    }
}

# --- 0. 포트 ------------------------------------------------------------------------------------
# 설치·색인(약 5분)을 다 한 뒤에 충돌로 멈추지 않게 맨 앞에서 본다.
$ApiRunning = Test-Port -HostName "127.0.0.1" -Port $ApiPort
$WebRunning = Test-Port -HostName "localhost" -Port $WebPort
if ($ApiRunning -and -not (Test-OurApi)) {
    Stop-WithPause "포트 $ApiPort 을 다른 프로그램이 쓰고 있습니다 ($ApiUrl/api/health 응답이 우리 API 가 아닙니다). 그 프로그램을 끄고 다시 실행하세요."
}
if ($WebRunning -and -not (Test-OurWeb)) {
    Stop-WithPause "포트 $WebPort 을 다른 프로그램이 쓰고 있습니다 ($WebUrl 응답이 우리 웹이 아닙니다). 그 프로그램을 끄고 다시 실행하세요."
}

# --- 1. 파이썬 환경 -----------------------------------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Say ".venv 가 없어 만듭니다"
    $python = Find-Python
    if ($null -eq $python) {
        Stop-WithPause "Python 3.12 이상을 찾지 못했습니다. https://www.python.org/downloads/ 에서 설치하세요."
    }
    $prefix = @($python | Select-Object -Skip 1)
    & $python[0] @prefix -m venv .venv
    if (-not (Test-Path $VenvPython)) { Stop-WithPause ".venv 를 만들지 못했습니다." }
}
if (-not (Test-Imports)) {
    Say "파이썬 패키지를 설치합니다 (pip install -e .[web,embed], 처음 한 번)"
    & $VenvPython -m pip install -e ".[web,embed]"
    if ($LASTEXITCODE -ne 0) { Stop-WithPause "pip install 이 실패했습니다. 위 오류를 확인하세요." }
    if (-not (Test-Imports)) {
        Stop-WithPause ("설치했는데 불러오지 못합니다. 스마트 앱 컨트롤이 DLL 을 막았을 수 있습니다. " +
            "Docker Desktop 이 있으면 docker compose up api 로 API 를 띄우세요.")
    }
}

# --- 2. 검색 데이터 -----------------------------------------------------------------------------
if (Test-Path $IndexFile) {
    Say "키워드 색인이 있습니다 ($IndexFile)"
} else {
    Say "키워드 색인이 없습니다 ($IndexFile)"
    $downloadFailed = $false
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        Note "Release dataset-v0.9 의 jsonl.gz 를 받습니다 (약 700MB, 받은 파일은 건너뜁니다)"
        & gh release download dataset-v0.9 -p "*.jsonl.gz" -D $DataDir --skip-existing `
            --repo Deleted-Code-Search/Deleted-Code-Search
        $downloadFailed = $LASTEXITCODE -ne 0
    }
    $shards = @(Get-ChildItem -Path $DataDir -Filter "*.jsonl.gz" -ErrorAction SilentlyContinue)
    if ($downloadFailed) {
        # 일부만 받은 채 색인을 만들면, 다음 실행부터 색인이 있다고 보고 빠진 저장소를 영영 채우지 않는다.
        Note "데이터를 다 받지 못해 색인 만들기를 건너뜁니다. 대시보드만 동작합니다."
        Note "gh auth login 과 네트워크를 확인하고 다시 실행하세요. 받은 파일은 건너뜁니다."
        Note $ReleaseUrl
    } elseif ($shards.Count -eq 0) {
        Note "데이터가 없어 대시보드만 동작합니다. 검색을 쓰려면 아래에서 jsonl.gz 를 받아 $DataDir 에 넣고 다시 실행하세요."
        Note $ReleaseUrl
    } else {
        Note "색인을 만듭니다 (약 5분, 약 7GB)"
        & $VenvPython -m search.index
        if ($LASTEXITCODE -ne 0) { Note "색인을 만들지 못했습니다. 대시보드만 동작합니다." }
    }
}

if (Test-Path $SemanticManifest) {
    Say "의미 검색 임베딩이 있습니다"
} else {
    Say "의미 검색 임베딩이 없습니다"
    Note "키워드 검색만 동작합니다. 의미 검색은 python -m search.embed (약 5시간) 뒤에 켜집니다."
}

# --- 3. API --------------------------------------------------------------------------------------
if ($ApiRunning) {
    Say "API 가 이미 떠 있어 그대로 씁니다 ($ApiUrl)"
} else {
    Say "API 를 새 창에서 띄웁니다 ($ApiUrl)"
    $apiCommand = "& '.venv\Scripts\python.exe' -m uvicorn search.api:app --host 127.0.0.1 --port $ApiPort"
    Start-Window -Title "Deleted Code Search - API" -Command $apiCommand
    if (-not (Wait-Port -HostName "127.0.0.1" -Port $ApiPort -Seconds 90)) {
        Stop-WithPause "API 가 90초 안에 뜨지 않았습니다. API 창의 오류를 확인하세요."
    }
}

# --- 4. 웹 --------------------------------------------------------------------------------------
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Stop-WithPause "npm 을 찾지 못했습니다. Node.js 20 이상을 https://nodejs.org 에서 설치하세요."
}
if (-not (Test-Path "web\node_modules")) {
    Say "웹 패키지를 설치합니다 (npm install, 처음 한 번)"
    Push-Location web
    & npm install
    $npmExit = $LASTEXITCODE
    Pop-Location
    if ($npmExit -ne 0) { Stop-WithPause "npm install 이 실패했습니다. 위 오류를 확인하세요." }
}
if ($WebRunning) {
    Say "웹이 이미 떠 있어 그대로 씁니다 ($WebUrl)"
} else {
    Say "웹을 새 창에서 띄웁니다 ($WebUrl)"
    # localhost 로 API 를 부르면 Windows 가 IPv6 부터 시도해 첫 연결이 늦다 (docs/reports/search_latency.md).
    $webCommand = "`$env:VITE_API_BASE = '$ApiUrl'; Set-Location web; npm run dev"
    Start-Window -Title "Deleted Code Search - Web" -Command $webCommand
    if (-not (Wait-Port -HostName "localhost" -Port $WebPort -Seconds 60)) {
        Stop-WithPause "웹이 60초 안에 뜨지 않았습니다. 웹 창의 오류를 확인하세요."
    }
}

# --- 5. 브라우저 ---------------------------------------------------------------------------------
if (-not $NoBrowser) { Start-Process $WebUrl }

Say "준비됐습니다"
Write-Host "      화면 $WebUrl  (대시보드·검색 탭)"
Write-Host "      API  $ApiUrl/api/health"
Write-Host "      끝낼 때는 API·Web 창을 닫으세요."
try { Read-Host "`n엔터를 누르면 이 창만 닫습니다" | Out-Null } catch { }
