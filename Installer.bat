@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Installation de Calage de plans ===
where py >nul 2>nul
if errorlevel 1 (
  echo Python est introuvable. Installez-le depuis https://www.python.org/downloads/ puis relancez ce fichier.
  pause
  exit /b 1
)
py -m pip install --upgrade pip
py -m pip install -e .
if errorlevel 1 (
  echo L installation a echoue : voir les messages ci-dessus.
  pause
  exit /b 1
)
echo.
echo Installation terminee. Lancez Calage.bat pour ouvrir l appli.
pause
