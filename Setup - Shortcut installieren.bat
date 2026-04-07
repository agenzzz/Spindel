@echo off
:: Prüfen ob Admin-Rechte vorhanden
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administratorrechte erforderlich. Neustart mit erhoehten Rechten...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Shortcut auf Public Desktop — zeigt auf C:\Users\Public\Spindel
set "SRC=C:\Users\Public\Spindel\Spindel Starten.bat"
set "DST=C:\Users\Public\Desktop\Spindel Starten.bat"

copy /Y "%SRC%" "%DST%" >nul
if %errorlevel% equ 0 (
    echo.
    echo  Shortcut erfolgreich installiert:
    echo  %DST%
    echo.
    echo  "Spindel Starten" ist jetzt auf dem Desktop aller Nutzer sichtbar.
    echo  Code-Ordner: C:\Users\Public\Spindel
) else (
    echo.
    echo  FEHLER: Konnte Shortcut nicht kopieren.
)

echo.
pause
