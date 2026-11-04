<#
  Wolf DevSecOps — instalador asistido / guided installer (ES + EN) — Windows PowerShell

  Un solo comando levanta toda la suite (Postgres + backend + frontend + SonarQube +
  DefectDojo + Trivy). One command brings up the whole suite.

    pwsh deploy/install.ps1                 # asistido / interactive
    pwsh deploy/install.ps1 -Lang en -Yes   # no interactivo / non-interactive
    pwsh deploy/install.ps1 -Repo C:\src\mi-app   # score real de TU repo (Sonar+Trivy)
    pwsh deploy/install.ps1 -Image nginx:1.27     # score Trivy de TU imagen
    pwsh deploy/install.ps1 -Demo                 # escaneo demo (python:3.9-slim), opt-in

  No pisa un deploy/.env existente. Never overwrites an existing deploy/.env.
#>
[CmdletBinding()]
param(
  [ValidateSet('es','en')] [string]$Lang = 'es',
  [Alias('Profile')] [string]$InstallProfile = '',   # lite | standard | full ('' = preguntar)
  [string]$Repo = '',                                # ruta local de TU repo a escanear
  [string]$Image = '',                               # imagen de contenedor a escanear
  [string]$Org = '',                                 # organización para agrupar el proyecto (def. Local)
  [string]$Key = '',                                 # project_key (def.: derivada del repo/imagen)
  [string]$Name = '',
  [switch]$Demo,                                     # escaneo demo python:3.9-slim (opt-in)
  [switch]$Yes
)
$ErrorActionPreference = 'Stop'
$Here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Here
$EnvFile = Join-Path $Here '.env'
$Projects = Join-Path $RepoRoot 'config/projects.yaml'
if ($env:WOLF_LANG) { $Lang = $env:WOLF_LANG }
if ($env:WOLF_PROFILE) { $InstallProfile = $env:WOLF_PROFILE }
if ($InstallProfile -notin @('lite','standard','full','')) { $InstallProfile = '' }
if ($env:WOLF_REPO) { $Repo = $env:WOLF_REPO }
if ($env:WOLF_IMAGE) { $Image = $env:WOLF_IMAGE }
if ($env:WOLF_ORG) { $Org = $env:WOLF_ORG }
if ($env:WOLF_DEMO -eq '1') { $Demo = [switch]$true }

function T($es,$en){ if($Lang -eq 'en'){$en}else{$es} }
function Step($es,$en){ Write-Host "`n▸ $(T $es $en)" -ForegroundColor Cyan }
function Ok($es,$en){ Write-Host "✓ $(T $es $en)" -ForegroundColor Green }
function Warn($es,$en){ Write-Host "! $(T $es $en)" -ForegroundColor Yellow }
function Die($es,$en){ Write-Host "✗ $(T $es $en)" -ForegroundColor Red; exit 1 }
function Ask($es,$en){ if($Yes){return $true}; $r=Read-Host "$(T $es $en) [S/n]"; return ($r -notmatch '^[nN]') }
function ToGitBashPath([string]$p){ $p = $p -replace '\\','/'; if ($p -match '^([A-Za-z]):(.*)$') { return '/' + $Matches[1].ToLower() + $Matches[2] }; return $p }
function RandPw([int]$n=24){ -join ((48..57)+(65..90)+(97..122) | Get-Random -Count $n | ForEach-Object {[char]$_}) }
function RandSecret(){ RandPw 64 }
function GenFernet(){ $b=New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ([Convert]::ToBase64String($b)).Replace('+','-').Replace('/','_') }

Write-Host "`n   🐺  Wolf DevSecOps — $(T 'instalador' 'guided install')`n" -ForegroundColor Cyan

# 0b) perfil / profile
if (-not $InstallProfile -and -not $Yes) {
  Write-Host (T "`n  Perfil de instalación:" "`n  Install profile:")
  Write-Host (T '  1) lite      — panel + SonarQube + Trivy        (~2 GB RAM, arranque rápido)' '  1) lite      — panel + SonarQube + Trivy        (~2 GB RAM, quick start)')
  Write-Host (T '  2) standard  — + DefectDojo (gestión de hallazgos) (~4-5 GB RAM)' '  2) standard  — + DefectDojo (findings management)  (~4-5 GB RAM)')
  Write-Host (T '  3) full      — standard + Harbor (su instalador aparte)' '  3) full      — standard + Harbor (its own installer)')
  $pf = Read-Host '  [2]'
  switch ($pf) { '1' { $InstallProfile = 'lite' } '3' { $InstallProfile = 'full' } default { $InstallProfile = 'standard' } }
}
if (-not $InstallProfile) { $InstallProfile = 'standard' }

if ($InstallProfile -eq 'lite') {
  $Compose = Join-Path $Here 'docker-compose.lite.yml'
  $Seed    = Join-Path $RepoRoot 'config/projects.lite.yaml'
} else {
  $Compose = Join-Path $Here 'docker-compose.install.yml'
  $Seed    = Join-Path $RepoRoot 'config/projects.install.yaml'
}
Ok "Perfil: $InstallProfile" "Profile: $InstallProfile"

# 0c) objetivo a escanear (tu repo / tu imagen). Sin ellos: pregunta o no escanea; NUNCA demo salvo -Demo.
if (-not $Repo -and -not $Image -and -not $Demo -and -not $Yes) {
  Write-Host (T "`n  ¿Qué quieres analizar? (Enter = omitir)" "`n  What do you want to analyze? (Enter = skip)")
  $Repo  = Read-Host (T '  Ruta local de tu repo' '  Local repo path')
  $Image = Read-Host (T '  Imagen de contenedor (nombre[:tag])' '  Container image (name[:tag])')
  if ($Repo -or $Image) { $Org = Read-Host (T '  Organización (para agruparlo en el panel) [Local]' '  Organization (to group it in the panel) [Local]') }
  if (-not $Repo -and -not $Image) {
    $r = Read-Host (T '¿Correr el escaneo DEMO (python:3.9-slim)? [s/N]' 'Run the DEMO scan (python:3.9-slim)? [y/N]')
    if ($r -match '^[sSyY]') { $Demo = [switch]$true }
  }
}
if ($Repo) {
  if (-not (Test-Path -LiteralPath $Repo -PathType Container)) { Die "El repo no existe: $Repo" "Repo does not exist: $Repo" }
  $Repo = (Resolve-Path -LiteralPath $Repo).Path
}
if ($Image -and $Image -notmatch '^[A-Za-z0-9][A-Za-z0-9._/:@-]*$') { Die "Nombre de imagen inválido: $Image" "Invalid image name: $Image" }
$HasTarget = [bool]($Repo -or $Image)

# 1) preflight
Step 'Requisitos previos' 'Prerequisites'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die 'Falta Docker Desktop.' 'Docker Desktop is missing.' }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Die 'Falta Docker Compose v2 (docker compose).' 'Docker Compose v2 (docker compose) is missing.' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Die 'El demonio de Docker no responde. ¿Docker Desktop abierto?' 'Docker daemon not responding. Is Docker Desktop running?' }
Ok 'Docker + Compose OK' 'Docker + Compose OK'
Warn 'SonarQube pide ~2-3 GB de RAM libres en Docker.' 'SonarQube needs ~2-3 GB free RAM in Docker.'

# 2) deploy/.env
Step 'Secretos (deploy/.env)' 'Secrets (deploy/.env)'
$AdminPw = '?'
if (Test-Path $EnvFile) {
  Ok 'Ya existe deploy/.env — no se toca.' 'deploy/.env already exists — left untouched.'
} else {
  $AdminPw = RandPw 18
  @"
# Generado por install.ps1 — NO se comitea. / Generated by install.ps1 — not committed.
WOLF_JWT_SECRET=$(RandSecret)
WOLF_MFA_FERNET_KEY=$(GenFernet)
WOLF_ADMIN_USERNAME=admin
WOLF_ADMIN_PASSWORD=$AdminPw
WOLF_ADMIN_EMAIL=
POSTGRES_PASSWORD=$(RandPw 24)
SONAR_DB_PASSWORD=$(RandPw 24)
DD_DB_PASSWORD=$(RandPw 24)
WOLF_AI_ENABLED=false
WOLF_CORS_ORIGINS=http://localhost:8088
GITHUB_TOKEN=
DEFECTDOJO_TOKEN=
SONAR_TOKEN=
HARBOR_TOKEN=
TRIVY_TOKEN=
"@ | Set-Content -Path $EnvFile -Encoding ASCII
  Ok 'deploy/.env creado con secretos aleatorios.' 'deploy/.env created with random secrets.'
}

# 3) semilla de proyectos
Step 'Catálogo de proyectos' 'Projects catalog'
if (Test-Path $Projects) { Ok 'config/projects.yaml ya existe.' 'config/projects.yaml already exists.' }
elseif (Test-Path $Seed) { Copy-Item $Seed $Projects; Ok 'config/projects.yaml creado desde la plantilla.' 'config/projects.yaml seeded from template.' }
else { Warn 'Sin plantilla; el panel arranca vacío.' 'No template; panel starts empty.' }

# 4) up
Step 'Levantando la suite (build + up)' 'Bringing the suite up (build + up)'
if ($InstallProfile -eq 'lite') { Write-Host (T 'Perfil lite: panel + SonarQube + Trivy (sin DefectDojo).' 'lite profile: panel + SonarQube + Trivy (no DefectDojo).') }
Push-Location $RepoRoot
docker compose -f $Compose up -d --build
Pop-Location

# 4b) escaneo DEMO (opt-in con -Demo): idéntico al histórico, trivy image python:3.9-slim -> wolf-cloud.json
if ($Demo) {
  Step 'Escaneo demo (python:3.9-slim)' 'Demo scan (python:3.9-slim)'
  Push-Location $RepoRoot
  docker compose -f $Compose --profile demo run --rm trivy-scan
  if ($LASTEXITCODE -eq 0) { Ok 'Reporte demo publicado (wolf-cloud.json).' 'Demo report published (wolf-cloud.json).' }
  else { Warn 'El escaneo demo falló; reintenta con: docker compose -f deploy/<compose> --profile demo run --rm trivy-scan' 'Demo scan failed; retry with: docker compose -f deploy/<compose> --profile demo run --rm trivy-scan' }
  Pop-Location
}

# 5) salud
Step 'Esperando al panel' 'Waiting for the panel'
$url='http://localhost:8088'; $ok=$false
for ($i=0; $i -lt 90; $i++) {
  try { Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 3 *> $null; $ok=$true; break } catch { Start-Sleep 2; Write-Host -NoNewline '.' }
}
Write-Host ''
if ($ok) { Ok "Backend saludable. Frontend en $url" "Backend healthy. Frontend at $url" }
else { Warn 'El backend tardó demasiado; revisa: docker compose -f deploy/docker-compose.install.yml logs backend' 'Backend took too long; check the logs.' }

# 6) fuentes (provision-sources.sh es bash → WSL/Git Bash)
Step 'Conectar fuentes demo' 'Connect demo sources'
Warn "Para cablear las fuentes demo corre en WSL/Git Bash: WOLF_PROFILE=$InstallProfile bash deploy/provision-sources.sh" "To wire demo sources run in WSL/Git Bash: WOLF_PROFILE=$InstallProfile bash deploy/provision-sources.sh"

# 6a) escanear TU objetivo. scan-target.sh es bash: se usa Git Bash si existe; si no, se imprime el comando.
if ($HasTarget) {
  Step 'Escaneando tu proyecto (Trivy + Sonar)' 'Scanning your project (Trivy + Sonar)'
  $sa = @()
  if ($Repo)  { $sa += @('--repo', (ToGitBashPath $Repo)) }
  if ($Image) { $sa += @('--image', $Image) }
  if ($Key)   { $sa += @('--key', $Key) }
  if ($Name)  { $sa += @('--name', $Name) }
  if ($Org)   { $sa += @('--org', $Org) }
  $gitBash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
  if (Test-Path $gitBash) {
    $env:WOLF_COMPOSE_FILE = ToGitBashPath $Compose
    & $gitBash (Join-Path $Here 'scan-target.sh') @sa
    if ($LASTEXITCODE -eq 0) { Ok 'Tu score real ya está en el panel.' 'Your real score is now in the panel.' }
    else { Warn 'El escaneo terminó con avisos; reintenta en Git Bash/WSL: bash deploy/scan-target.sh ...' 'Scan finished with warnings; retry in Git Bash/WSL: bash deploy/scan-target.sh ...' }
  } else {
    Warn "Sin Git Bash: corre en WSL/Git Bash: WOLF_COMPOSE_FILE=deploy/$(Split-Path -Leaf $Compose) bash deploy/scan-target.sh $($sa -join ' ')" "No Git Bash: run in WSL/Git Bash: WOLF_COMPOSE_FILE=deploy/$(Split-Path -Leaf $Compose) bash deploy/scan-target.sh $($sa -join ' ')"
  }
} elseif (-not $Demo) {
  Warn 'No se escaneó nada (sin -Repo/-Image/-Demo). Luego: bash deploy/scan-target.sh --repo <ruta>' 'Nothing scanned (no -Repo/-Image/-Demo). Later: bash deploy/scan-target.sh --repo <path>'
}

# 6b) Harbor (solo full)
if ($InstallProfile -eq 'full') {
  Step 'Harbor (registry de imágenes)' 'Harbor (image registry)'
  Warn 'Instala Harbor con su instalador: bash deploy/harbor/setup-harbor.sh (WSL/Git Bash).' 'Install Harbor with its installer: bash deploy/harbor/setup-harbor.sh (WSL/Git Bash).'
}

# 7) resumen
Step 'Listo' 'Done'
Write-Host (T 'Abre el panel e inicia sesión:' 'Open the panel and sign in:')
Write-Host "   $url" -ForegroundColor Cyan
Write-Host "   $(T 'Usuario:' 'User:   ') admin"
if ($AdminPw -ne '?') { Write-Host "   $(T 'Clave:  ' 'Password:') $AdminPw" }
else { Write-Host "   $(T 'Clave:' 'Password:') $(T 'ver deploy/.env (WOLF_ADMIN_PASSWORD)' 'see deploy/.env (WOLF_ADMIN_PASSWORD)')" }
Write-Host ''
Write-Host (T 'Primer login: 1) cambia la contraseña, 2) enrola MFA (QR + TOTP).' 'First login: 1) change password, 2) enroll MFA (QR + TOTP).')
$cfRel = 'deploy/' + (Split-Path -Leaf $Compose)
Write-Host (T "Parar: docker compose -f $cfRel down" "Stop: docker compose -f $cfRel down")
if (Ask '¿Abrir el panel en el navegador?' 'Open the panel in the browser?') { Start-Process $url }
