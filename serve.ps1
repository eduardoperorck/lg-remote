# Sobe o servidor do controle. Use depois que o setup.bat já rodou uma vez.
#
# -LogFile é usado pelo início automático: lá não existe janela, e sem gravar a saída
# num arquivo um erro na subida desapareceria sem deixar rastro nenhum.
param([string]$LogFile = "")

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# Escrito pelo setup.ps1 quando o Smart App Control bloqueou o Python do uv. Sem isto,
# o uv voltaria a escolher o Python gerenciado e o servidor quebraria de novo.
if (Test-Path (Join-Path $PSScriptRoot ".use-system-python")) {
    $env:UV_NO_MANAGED_PYTHON = "1"
}

if ($LogFile) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null
    "=== $(Get-Date -Format s) — subindo o controle ===" |
        Out-File -Append -FilePath $LogFile -Encoding utf8
    # O uv e o uvicorn registram tudo em stderr. Com "Stop" ligado, capturar esse stream
    # faz a PRIMEIRA linha de log virar erro terminante — o servidor morreria ao nascer,
    # e sem janela ninguém veria o porquê.
    $ErrorActionPreference = "Continue"
    uv run lgremote serve *>> $LogFile
} else {
    uv run lgremote serve
}

exit $LASTEXITCODE
