@echo off
setlocal
cd /d "%~dp0"
echo Stopping SOPS Editor...
docker compose --env-file "docker\.env" --env-file "docker\.env.secrets" down
pause
