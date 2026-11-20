@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Calage de plans : la page va s ouvrir dans le navigateur.
echo Laissez cette fenetre ouverte pendant l utilisation (la fermer arrete l appli).
py -m calage interface
if errorlevel 1 pause
