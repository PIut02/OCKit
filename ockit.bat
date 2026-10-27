@echo off
rem ockit launcher (Windows) - find a usable Python 3 interpreter
rem NOTE: keep this file ASCII-only; cmd.exe mis-parses non-ASCII in batch files.
setlocal
set "OCKIT=%~dp0ockit.py"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%OCKIT%" %*
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%OCKIT%" %*
    exit /b %errorlevel%
)

where python3 >nul 2>nul
if %errorlevel%==0 (
    python3 "%OCKIT%" %*
    exit /b %errorlevel%
)

echo [ockit] Python 3 not found. Install it once (no further config needed):
echo     winget install Python.Python.3.12
echo Or install Python 3.12 from the Microsoft Store.
exit /b 1
