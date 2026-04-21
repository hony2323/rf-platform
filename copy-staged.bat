@echo off
setlocal enabledelayedexpansion

for /f "delims=" %%i in ('git rev-parse --show-toplevel') do set REPO_ROOT=%%i
set DEST=%REPO_ROOT%\temp staged

if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%"

for /f "delims=" %%f in ('git diff --name-only --cached') do (
    set FILE=%%f
    set SRC=%REPO_ROOT%\!FILE:/=\!
    if exist "!SRC!" (
        for %%d in ("!SRC!") do set FILE_DIR=%%~dpd
        set DEST_DIR=%DEST%\!FILE:/=\!
        for %%d in ("!DEST_DIR!") do set DEST_PARENT=%%~dpd
        if not exist "!DEST_PARENT!" mkdir "!DEST_PARENT!"
        copy "!SRC!" "!DEST_PARENT!" >nul
        echo Copied: %%f
    ) else (
        echo Skipped ^(deleted^): %%f
    )
)

echo.
echo Done. Files are in: %DEST%
endlocal
