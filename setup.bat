@echo off
REM Clique duas vezes neste arquivo, ou rode "setup.bat" no Prompt de Comando.
REM Existe porque o cmd nao entende comandos do PowerShell (irm, iex...).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
echo.
pause
