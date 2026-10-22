@echo off
cd /d "%~dp0"
echo Setting up Torobgard environment...
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Done. Now run 2_build_db.bat
pause
