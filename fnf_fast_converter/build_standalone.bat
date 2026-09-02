@echo off
setlocal EnableDelayedExpansion
title FNF Fast Converter - Build Standalone Distribution

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ====================================================================
echo          FNF Fast Converter - Standalone Packaging Pipeline
echo ====================================================================
echo.

:: 1. Search for Python >= 3.9
set "PYTHON_EXE="

:: Check 'python' in PATH
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON_EXE (
            "%%I" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
            if !ERRORLEVEL! EQU 0 (
                set "PYTHON_EXE=%%I"
            )
        )
    )
)

:: Check 'py' launcher
if not defined PYTHON_EXE (
    where py >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            set "PYTHON_EXE=py -3"
        )
    )
)

:: Check standard local appdata installation locations
if not defined PYTHON_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PYTHON_EXE (
            if exist "%%D\python.exe" (
                "%%D\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
                if !ERRORLEVEL! EQU 0 (
                    set "PYTHON_EXE=%%D\python.exe"
                )
            )
        )
    )
)

:: Check standard C:\Python3* locations
if not defined PYTHON_EXE (
    for /d %%D in ("C:\Python3*") do (
        if not defined PYTHON_EXE (
            if exist "%%D\python.exe" (
                "%%D\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
                if !ERRORLEVEL! EQU 0 (
                    set "PYTHON_EXE=%%D\python.exe"
                )
            )
        )
    )
)

:: Check Program Files locations
if not defined PYTHON_EXE (
    for /d %%D in ("%ProgramFiles%\Python3*") do (
        if not defined PYTHON_EXE (
            if exist "%%D\python.exe" (
                "%%D\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
                if !ERRORLEVEL! EQU 0 (
                    set "PYTHON_EXE=%%D\python.exe"
                )
            )
        )
    )
)

if not defined PYTHON_EXE (
    echo [ERROR] Python 3.9 or newer was not found on your system!
    echo.
    echo Please install Python 3.9+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add python.exe to PATH" during installation.
    echo.
    echo ====================================================================
    pause
    exit /b 1
)

echo [OK] Using Python: %PYTHON_EXE%
echo.

:: 2. Check and install PyInstaller and build dependencies
echo [INFO] Checking packaging dependencies...
%PYTHON_EXE% -c "import PyInstaller, customtkinter, soundfile, numpy, PIL, windnd" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Installing required build dependencies...
    %PYTHON_EXE% -m pip install pyinstaller customtkinter soundfile numpy pillow windnd
    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] Failed to install build dependencies.
        echo Please check your internet connection or install manually with:
        echo   %PYTHON_EXE% -m pip install pyinstaller customtkinter soundfile numpy pillow windnd
        echo.
        pause
        exit /b 1
    )
    echo [OK] Build dependencies installed successfully.
    echo.
) else (
    echo [OK] All build dependencies are installed.
    echo.
)

:: 3. Execute build_standalone.py
echo ====================================================================
echo Starting PyInstaller Standalone Packaging Process...
echo ====================================================================
echo.

if exist "%SCRIPT_DIR%build_standalone.py" (
    %PYTHON_EXE% "%SCRIPT_DIR%build_standalone.py" %*
) else if exist "%SCRIPT_DIR%..\build_standalone.py" (
    %PYTHON_EXE% "%SCRIPT_DIR%..\build_standalone.py" %*
) else (
    echo [ERROR] Cannot find build_standalone.py!
    pause
    exit /b 1
)

set "BUILD_EXIT_CODE=%ERRORLEVEL%"
if %BUILD_EXIT_CODE% NEQ 0 (
    echo.
    echo ====================================================================
    echo [ERROR] Build failed with exit code %BUILD_EXIT_CODE%.
    echo ====================================================================
    pause
    exit /b %BUILD_EXIT_CODE%
)

echo.
echo ====================================================================
echo Standalone distribution build finished successfully!
echo Output folder: %SCRIPT_DIR%dist\FNF_Fast_Converter\
echo ====================================================================
echo.
exit /b 0
