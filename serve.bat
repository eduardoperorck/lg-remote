@echo off
REM Liga o controle. Clique duas vezes, deixe a janela aberta e use o celular.
REM Para desligar: feche a janela ou aperte Ctrl+C.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve.ps1"
echo.
pause
