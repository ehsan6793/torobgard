@echo off
chcp 65001 >nul
cd /d "%~dp0"
call .venv\Scripts\activate.bat
if "%~1"=="" (
  echo مسیر پوشهٔ فایل‌های CSV دیتاست ترب را به‌عنوان آرگومان بده، مثال:
  echo   2_build_db.bat "C:\path\to\torob-dataset"
  echo یا مسیر پوشه را همین‌جا بنویس و اینتر بزن:
  set /p CSVDIR="مسیر پوشهٔ CSV: "
) else (
  set CSVDIR=%~1
)
python scripts\build_db.py "%CSVDIR%" data\torob.db
echo.
echo دیتابیس ساخته شد. حالا 3_run.bat را اجرا کن.
pause
