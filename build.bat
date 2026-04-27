@echo off
REM Build a standalone Windows executable from the GUI.
REM Output: dist\revit-history-patcher.exe

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller || exit /b 1
)

echo Building...
pyinstaller --clean revit-history-patcher.spec || exit /b 1

echo.
echo Done. Output: dist\revit-history-patcher.exe
