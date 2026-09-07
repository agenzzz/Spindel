@echo off
:: HSD Spindel — Public-Desktop-Shortcut Installer
:: Legt einen .lnk auf C:\Users\Public\Desktop, der Spindel.exe startet.
:: Sichtbar fuer ALLE Nutzer dieses PCs. Kein Python noetig.

:: Admin-Rechte pruefen
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administratorrechte erforderlich. Neustart mit erhoehten Rechten...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

set "EXE=C:\Users\Public\Spindel\Spindel.exe"
set "LNK=C:\Users\Public\Desktop\Spindel Starten.lnk"

:: EXE muss existieren
if not exist "%EXE%" (
    echo.
    echo  FEHLER: %EXE% wurde nicht gefunden.
    echo  Bitte zuerst die aktuelle Spindel.exe nach C:\Users\Public\Spindel\ kopieren.
    echo.
    pause
    exit /b 1
)

:: Alten Shortcut loeschen wenn vorhanden
if exist "%LNK%" del /F "%LNK%"

:: Neuen .lnk erzeugen (zeigt auf EXE, mit Icon)
powershell -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='C:\Users\Public\Spindel'; $s.IconLocation='%%SystemRoot%%\System32\imageres.dll,77'; $s.Description='HSD Spindel Web-Interface'; $s.Save()"

if exist "%LNK%" (
    echo.
    echo  ============================================
    echo   Setup abgeschlossen
    echo  ============================================
    echo   Shortcut:  %LNK%
    echo   Target:    %EXE%
    echo   Sichtbar fuer alle Nutzer auf dem Desktop.
    echo.
    echo   Version im UI pruefen nach Start:
    echo   Aktuell v2 ist die neueste Version.
    echo  ============================================
) else (
    echo.
    echo  FEHLER: Konnte Shortcut nicht erstellen.
)

echo.
pause
