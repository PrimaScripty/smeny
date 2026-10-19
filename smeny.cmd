@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stahuji rozpis smen z Google tabulky...
python scripts\smeny.py build --standalone --out out\smeny-sport.html
if errorlevel 1 (
  echo.
  echo Prehled se nepodarilo vytvorit, chyba je vypsana vyse.
  pause
  exit /b 1
)
start "" "out\smeny-sport.html"
