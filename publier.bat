@echo off
rem Calcule les resultats et les publie dans Supabase pour l'interface en ligne.
rem Les cles se lisent dans le fichier .env (voir .env.example).
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m socialstats.publish %*
pause
