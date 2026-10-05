@echo off
rem Lance l'interface de controle mensuel et ouvre le navigateur.
rem Fermer cette fenetre arrete le serveur.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m socialstats.server %*
if errorlevel 1 pause
