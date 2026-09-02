@echo off
setlocal EnableDelayedExpansion
title FNF Fast Converter - High-Speed Rock Band CON to Clone Hero Converter

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ====================================================================
echo               FNF Fast Converter - One-Click Launcher
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

:: 2. Setup PYTHONPATH
if exist "%SCRIPT_DIR%fnf_fast_converter" (
    set "PYTHONPATH=%SCRIPT_DIR%;%SCRIPT_DIR%fnf_fast_converter;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%SCRIPT_DIR%..;%SCRIPT_DIR%;%PYTHONPATH%"
)

:: 3. Check and install dependencies
echo [INFO] Checking required dependencies...
%PYTHON_EXE% -c "import customtkinter, windnd, soundfile, numpy, PIL" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Missing dependencies detected. Installing required packages...
    if exist "%SCRIPT_DIR%requirements.txt" (
        %PYTHON_EXE% -m pip install -r "%SCRIPT_DIR%requirements.txt"
    ) else if exist "%SCRIPT_DIR%..\requirements.txt" (
        %PYTHON_EXE% -m pip install -r "%SCRIPT_DIR%..\requirements.txt"
    ) else (
        %PYTHON_EXE% -m pip install customtkinter windnd soundfile numpy pillow
    )
    
    :: Verify dependencies were installed
    %PYTHON_EXE% -c "import customtkinter, windnd, soundfile, numpy, PIL" >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] Failed to install required dependencies.
        echo Please check your internet connection or install manually with:
        echo   %PYTHON_EXE% -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed successfully.
    echo.
) else (
    echo [OK] All dependencies are installed.
    echo.
)

:: 4. Launch GUI Application
echo ====================================================================
echo Starting FNF Fast Converter GUI...
echo ====================================================================
echo.

if exist "%SCRIPT_DIR%run_gui.py" (
    %PYTHON_EXE% "%SCRIPT_DIR%run_gui.py" %*
) else if exist "%SCRIPT_DIR%src\gui.py" (
    %PYTHON_EXE% "%SCRIPT_DIR%src\gui.py" %*
) else (
    %PYTHON_EXE% -m fnf_fast_converter.src.gui %*
)

set "APP_EXIT_CODE=%ERRORLEVEL%"
if %APP_EXIT_CODE% NEQ 0 (
    echo.
    echo ====================================================================
    echo [ERROR] Application terminated with error code %APP_EXIT_CODE%.
    echo Check the error messages above for details.
    echo ====================================================================
    pause
    exit /b %APP_EXIT_CODE%
)

exit /b 0
