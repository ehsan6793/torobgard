@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
if "%~1"=="" (
  echo Give the folder path of the Torob dataset CSV files, for example:
  echo   2_build_db.bat "C:\path\to\torob-dataset"
  echo Or type the folder path here and press Enter:
  set /p CSVDIR="CSV folder path: "
) else (
  set CSVDIR=%~1
)
python scripts\build_db.py "%CSVDIR%" data\torob.db
echo.
echo Database built. Now run 3_run.bat
pause
