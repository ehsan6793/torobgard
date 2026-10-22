@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo نصب محیط پایتون برای ترب‌گرد...
python -m venv .venv
call .venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt
echo.
echo تمام شد. حالا 2_build_db.bat را اجرا کن.
pause
