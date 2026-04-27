@echo off
REM Build a standalone Windows executable from the GUI.
REM Output: dist\rvt-history-patcher.exe

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller || exit /b 1
)

echo Building...
pyinstaller --clean rvt-history-patcher.spec || exit /b 1

echo.
echo Done. Output: dist\rvt-history-patcher.exe
