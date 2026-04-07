@echo off
:: Prüfen ob Admin-Rechte vorhanden
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administratorrechte erforderlich. Neustart mit erhoehten Rechten...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Ziel: Public Desktop (erscheint bei ALLEN Nutzern)
set "SRC=C:\Users\Noel Jacobs\Documents\Spindel\Spindel Starten.bat"
set "DST=C:\Users\Public\Desktop\Spindel Starten.bat"

copy /Y "%SRC%" "%DST%" >nul
if %errorlevel% equ 0 (
    echo.
    echo  Shortcut erfolgreich installiert:
    echo  %DST%
    echo.
    echo  "Spindel Starten" ist jetzt auf dem Desktop aller Nutzer sichtbar.
) else (
    echo.
    echo  FEHLER: Konnte Shortcut nicht kopieren.
    echo  Quelle:  %SRC%
    echo  Ziel:    %DST%
)

echo.
pause
