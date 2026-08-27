# Instala o que falta e chama o assistente de configuração.
# Uso: clique duas vezes em setup.bat, ou rode este arquivo no PowerShell.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Sem isto o console brasileiro (cp850) troca os acentos por lixo ao ler a saída do
# Python. PYTHONUTF8 cobre o lado do Python; OutputEncoding, o lado do PowerShell.
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "OK  $text" -ForegroundColor Green }

# Marca que este PC precisa do Python do sistema. Lido também pelo serve.ps1, senão
# o uv voltaria a escolher o Python gerenciado na próxima vez.
$SystemPythonMarker = Join-Path $PSScriptRoot ".use-system-python"
if (Test-Path $SystemPythonMarker) { $env:UV_NO_MANAGED_PYTHON = "1" }

# Roda um comando engolindo a saída e devolve só o código de saída.
#
# O `$ErrorActionPreference = "Stop"` lá em cima transforma em erro TERMINANTE qualquer
# comando nativo que escreva em stderr quando a saída é capturada com `2>&1`. Nas
# chamadas abaixo o stderr é justamente o que queremos medir (ou barulho de um rmdir que
# falhou), então sem baixar a guarda aqui o próprio diagnóstico derruba o instalador.
function Invoke-Silently([scriptblock] $action) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $action 2>&1 | Out-Null
        # Lido nesta função, onde o comando rodou: em escopo de fora pode vir defasado.
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

# O servidor do início automático roda sem janela nenhuma e segura o arquivo
# .venv\Scripts\lgremote.exe. Sem parar antes, o `uv sync` morre com "Acesso negado" e o
# `rmdir` com "arquivo já está sendo usado" — sem nada na tela explicando que a causa é o
# próprio controle, no ar desde o login. Ele volta no próximo login, e o assistente
# oferece subir de novo no fim.
function Stop-RunningServer {
    if (-not (Get-Process -Name "lgremote" -ErrorAction SilentlyContinue)) { return }

    Write-Host "O controle já estava no ar. Parando para poder atualizar..." -ForegroundColor Yellow
    # Por nome e com /T: o lgremote.exe é um lançador, e quem segura a porta (e as DLLs
    # do venv) é o python filho.
    [void](Invoke-Silently { cmd /c "taskkill /IM lgremote.exe /T /F" })
    # O Windows solta o handle do .exe um instante depois de o processo morrer.
    Start-Sleep -Milliseconds 500
}

function Remove-Venv {
    if (-not (Test-Path ".venv")) { return }
    Stop-RunningServer
    [void](Invoke-Silently { cmd /c "rmdir /s /q .venv" })
    if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue }
}

# O Smart App Control do Windows 11 bloqueia DLLs do Python que o uv baixa (ficam em
# AppData e não têm assinatura que ele aceite). O erro só aparece na hora de importar
# ctypes — ou seja, lá no fim, ao subir o servidor. Melhor descobrir agora.
function Test-PythonUsable {
    return (Invoke-Silently { uv run python -c "import ctypes" }) -eq 0
}

# O `rmdir /s /q` apaga o que consegue antes de parar num arquivo travado, então dá para
# sobrar um .venv pela metade. Nele o `uv sync` anuncia "Installed 46 packages" e mesmo
# assim o `import lgremote` não acha nada — o erro só aparece lá na frente, como
# ModuleNotFoundError sem contexto. Conferir o import separa "instalou" de "funciona".
function Test-ProjectImportable {
    return (Invoke-Silently { uv run python -c "import lgremote" }) -eq 0
}

function Find-SystemPython {
    foreach ($name in @("python3.13", "python3.12", "python")) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        # O "python" da Microsoft Store é um atalho que só abre a loja; ignore.
        if ($found -and $found.Source -notlike "*WindowsApps*python.exe") { return $found.Source }
    }
    return $null
}

# O PATH do processo atual é uma cópia feita quando o terminal abriu: se o uv acabou
# de ser instalado, só relendo o registro para enxergá-lo sem fechar a janela.
function Update-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($machine, $user, "$env:USERPROFILE\.local\bin") | Where-Object { $_ }
    $env:Path = ($parts -join ";")
}

Write-Step "Verificando o uv (gerenciador de Python)"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Update-PathFromRegistry
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv não encontrado. Instalando..." -ForegroundColor Yellow
    # Alguns Windows ainda negociam TLS 1.0 por padrão e o download falha sem isto.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Host "`nFalhou baixar o instalador: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Alternativa: winget install --id=astral-sh.uv -e" -ForegroundColor Yellow
        exit 1
    }

    Update-PathFromRegistry

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "`nO uv foi instalado mas não apareceu no PATH." -ForegroundColor Red
        Write-Host "Feche esta janela, abra outra e rode setup.bat de novo." -ForegroundColor Yellow
        exit 1
    }
}

Write-Ok "uv $(uv --version)"

Write-Step "Instalando as dependências do projeto"

Stop-RunningServer
uv sync --extra dev
if ($LASTEXITCODE -ne 0) {
    # Causa quase certa: um .venv criado por outro sistema (ex.: WSL) que o Windows não
    # consegue apagar, porque tem symlinks do Linux dentro. O .venv é descartável —
    # recriar custa segundos e não há nada seu lá dentro.
    Write-Host "`nO ambiente existente está inutilizável. Recriando do zero..." -ForegroundColor Yellow
    Remove-Venv

    if (Test-Path ".venv") {
        Write-Host "`nNão consegui remover a pasta .venv." -ForegroundColor Red
        Write-Host "Algum programa ainda está com um arquivo dela aberto. Para descobrir qual:" -ForegroundColor Yellow
        Write-Host "  Get-Process lgremote, python -ErrorAction SilentlyContinue" -ForegroundColor Cyan
        Write-Host "Feche-o e rode setup.bat de novo, ou apague a pasta na mão:" -ForegroundColor Yellow
        Write-Host "  rmdir /s /q `"$PSScriptRoot\.venv`"" -ForegroundColor Cyan
        exit 1
    }

    uv sync --extra dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not (Test-PythonUsable)) {
    Write-Host "`nO Windows bloqueou o Python que o uv baixou (Smart App Control)." -ForegroundColor Yellow
    Write-Host "Vou tentar usar um Python instalado no sistema." -ForegroundColor Yellow

    $systemPython = Find-SystemPython
    if (-not $systemPython) {
        Write-Host "`nNão há Python do sistema instalado." -ForegroundColor Red
        Write-Host "Instale um assinado (leva 1 minuto) e rode setup.bat de novo:" -ForegroundColor Yellow
        Write-Host "  winget install Python.Python.3.12" -ForegroundColor Cyan
        Write-Host "Ou pela Microsoft Store, buscando por `"Python 3.12`"." -ForegroundColor Yellow
        Write-Host "`n(Não desligue o Smart App Control: no Windows 11 isso é irreversível" -ForegroundColor Yellow
        Write-Host " sem reinstalar o sistema, e enfraquece a proteção da máquina toda.)" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Usando: $systemPython" -ForegroundColor Cyan
    $env:UV_NO_MANAGED_PYTHON = "1"
    Remove-Venv
    uv sync --extra dev --python "$systemPython"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not (Test-PythonUsable)) {
        Write-Host "`nO Python do sistema também foi bloqueado." -ForegroundColor Red
        Write-Host "Instale o Python pela Microsoft Store (assinado pela Microsoft)." -ForegroundColor Yellow
        exit 1
    }

    New-Item -ItemType File -Path $SystemPythonMarker -Force | Out-Null
}

# Depois do Python estar OK: aqui um import que falha é ambiente quebrado, não bloqueio.
if (-not (Test-ProjectImportable)) {
    Write-Host "`nO ambiente ficou pela metade. Recriando do zero..." -ForegroundColor Yellow
    Remove-Venv
    uv sync --extra dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not (Test-ProjectImportable)) {
        Write-Host "`nInstalei tudo, mas o Python ainda não enxerga o pacote." -ForegroundColor Red
        Write-Host "Apague o .venv com a janela fechada e rode setup.bat de novo:" -ForegroundColor Yellow
        Write-Host "  rmdir /s /q `"$PSScriptRoot\.venv`"" -ForegroundColor Cyan
        exit 1
    }
}

Write-Ok "dependências prontas"

Write-Step "Configurando"
Write-Host "Deixe a TV LIGADA e na mesma rede que este PC." -ForegroundColor Yellow

# O Windows costuma pedir liberação de firewall na primeira vez que o servidor abre
# a porta. Aceite para rede PRIVADA, senão o celular não alcança.
uv run lgremote setup
exit $LASTEXITCODE
