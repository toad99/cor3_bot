@echo off
rem Lanceur pour le Planificateur de taches Windows (cor3_bot)
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
python cor3_bot.py %*
exit /b %ERRORLEVEL%
