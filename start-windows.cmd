@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === SOPS Editor (Docker) ===

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker CLI not found. Install Docker Desktop and try again.
  pause
  exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Start it, then run this shortcut again.
  pause
  exit /b 1
)

if defined SOPS_EDITOR_WORKSPACE set "EXTERNAL_WS=1"
set "NEW_ENV="
if not exist "docker\.env" (
  copy /y "docker\.env.example" "docker\.env" >nul
  echo Created docker\.env
  set "NEW_ENV=1"
)
if not exist "docker\.env.secrets" (
  copy /y "docker\.env.secrets.example" "docker\.env.secrets" >nul
  echo Created docker\.env.secrets
)

for %%V in (SOPS_EDITOR_WORKSPACE SOPS_EDITOR_KEYS SOPS_EDITOR_DATA SOPS_EDITOR_BIND SOPS_EDITOR_PORT SOPS_EDITOR_IMAGE SOPS_EDITOR_HOST_SSH_DIR SOPS_EDITOR_HOST_GITCONFIG) do (
  if not defined %%V (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"%%V=" "docker\.env" 2^>nul`) do set "%%V=%%B"
  )
)
if defined NEW_ENV if not defined EXTERNAL_WS set "SOPS_EDITOR_WORKSPACE="

set "PROMPTED_WS="
if not defined SOPS_EDITOR_WORKSPACE (
  set /p "SOPS_EDITOR_WORKSPACE=Folder with SOPS files [%CD%\workspace]: "
  set "PROMPTED_WS=1"
)
if "%SOPS_EDITOR_WORKSPACE%"=="" set "SOPS_EDITOR_WORKSPACE=%CD%\workspace"
set "SOPS_EDITOR_WORKSPACE=%SOPS_EDITOR_WORKSPACE:\=/%"
if not exist "%SOPS_EDITOR_WORKSPACE%" mkdir "%SOPS_EDITOR_WORKSPACE%" 2>nul
if defined PROMPTED_WS (
  findstr /v /b /c:"SOPS_EDITOR_WORKSPACE=" "docker\.env" > "%TEMP%\ai-sops-editor-env.tmp"
  >> "%TEMP%\ai-sops-editor-env.tmp" echo SOPS_EDITOR_WORKSPACE=%SOPS_EDITOR_WORKSPACE%
  move /y "%TEMP%\ai-sops-editor-env.tmp" "docker\.env" >nul
)

if not defined SOPS_EDITOR_KEYS set "SOPS_EDITOR_KEYS=%CD%/keys"
if not defined SOPS_EDITOR_DATA set "SOPS_EDITOR_DATA=%CD%/storage"
if not defined SOPS_EDITOR_BIND set "SOPS_EDITOR_BIND=127.0.0.1"
if not defined SOPS_EDITOR_PORT set "SOPS_EDITOR_PORT=8787"
if not defined SOPS_EDITOR_HOST_SSH_DIR if exist "%USERPROFILE%\.ssh" set "SOPS_EDITOR_HOST_SSH_DIR=%USERPROFILE%\.ssh"
if defined SOPS_EDITOR_HOST_SSH_DIR set "SOPS_EDITOR_HOST_SSH_DIR=%SOPS_EDITOR_HOST_SSH_DIR:\=/%"
if not defined SOPS_EDITOR_HOST_GITCONFIG if exist "%USERPROFILE%\.gitconfig" set "SOPS_EDITOR_HOST_GITCONFIG=%USERPROFILE%\.gitconfig"
if defined SOPS_EDITOR_HOST_GITCONFIG set "SOPS_EDITOR_HOST_GITCONFIG=%SOPS_EDITOR_HOST_GITCONFIG:\=/%"
set "SOPS_EDITOR_KEYS=%SOPS_EDITOR_KEYS:\=/%"
set "SOPS_EDITOR_DATA=%SOPS_EDITOR_DATA:\=/%"

set "PORT=%SOPS_EDITOR_PORT%"
if "%PORT%"=="" set "PORT=8787"

echo.
echo Workspace: %SOPS_EDITOR_WORKSPACE%
echo Key dir  : %SOPS_EDITOR_KEYS%  (optional: age.txt)
if defined SOPS_EDITOR_HOST_SSH_DIR (
  echo SSH dir  : %SOPS_EDITOR_HOST_SSH_DIR%
) else (
  echo SSH dir  : not set ^(HTTPS token only^)
)
if defined SOPS_EDITOR_HOST_GITCONFIG echo Git config: %SOPS_EDITOR_HOST_GITCONFIG%
echo URL      : http://localhost:%PORT%
echo.
echo First build can take a few minutes...
set "IMAGE=%SOPS_EDITOR_IMAGE%"
if "%IMAGE%"=="" for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"SOPS_EDITOR_IMAGE=" "docker\.env" 2^>nul`) do set "IMAGE=%%B"
if "%IMAGE%"=="" set "IMAGE=ai-sops-editor:local"
docker compose --env-file "docker\.env" --env-file "docker\.env.secrets" build
if errorlevel 1 (
  echo Compose build failed; falling back to docker build...
  docker build -t "%IMAGE%" .
  if errorlevel 1 (
    echo.
    echo docker build failed.
    pause
    exit /b 1
  )
)
docker compose --env-file "docker\.env" --env-file "docker\.env.secrets" up -d
if errorlevel 1 (
  echo.
  echo docker compose failed.
  pause
  exit /b 1
)

echo Waiting for health...
set /a TRIES=0
:wait
set /a TRIES+=1
for /f "tokens=*" %%H in ('docker inspect --format "{{.State.Health.Status}}" ai-sops-editor 2^>nul') do set "HEALTH=%%H"
if "%HEALTH%"=="healthy" goto ready
if %TRIES% GEQ 60 goto timeout
timeout /t 2 /nobreak >nul
goto wait

:ready
echo Ready.
start "" "http://localhost:%PORT%"
exit /b 0

:timeout
echo.
echo Container did not become healthy. Last logs:
docker compose --env-file "docker\.env" --env-file "docker\.env.secrets" logs --tail=40 ai-sops-editor
pause
exit /b 1
