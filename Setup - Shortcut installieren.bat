@echo off
:: Prueft Admin-Rechte
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administratorrechte erforderlich. Neustart mit erhoehten Rechten...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Erstellt einen .lnk Shortcut auf das Public Desktop (zeigt auf Spindel.exe)
set "EXE=C:\Users\Public\Spindel\Spindel.exe"
set "LNK=C:\Users\Public\Desktop\Spindel Starten.lnk"

powershell -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='C:\Users\Public\Spindel'; $s.IconLocation='%%SystemRoot%%\System32\imageres.dll,77'; $s.Description='HSD Spindel Web-Interface'; $s.Save()"

if exist "%LNK%" (
    echo.
    echo  Shortcut erfolgreich installiert:
    echo  %LNK%
    echo.
    echo  "Spindel Starten" ist jetzt auf dem Desktop aller Nutzer sichtbar.
    echo  EXE-Pfad: %EXE%
    echo  Kein Python erforderlich.
) else (
    echo.
    echo  FEHLER: Konnte Shortcut nicht erstellen.
)

echo.
pause
