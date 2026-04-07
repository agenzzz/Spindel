@echo off
set PYTHON=C:\Users\Noel Jacobs\AppData\Local\Python\pythoncore-3.14-64\python.exe
set SPINDEL=C:\Users\Noel Jacobs\Documents\Spindel

echo Pruefe Python-Pakete...
"%PYTHON%" -c "import flask" 2>nul || "%PYTHON%" -m pip install flask -q
"%PYTHON%" -c "import nidaqmx" 2>nul || "%PYTHON%" -m pip install nidaqmx -q
echo Pakete OK.

echo Starte Spindel-Interface...
"%PYTHON%" "%SPINDEL%\spindel_web.py"
