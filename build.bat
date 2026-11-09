@echo off
rem 重新构建 上班激励计时器.exe（需要先 pip install pyinstaller pillow）
cd /d %~dp0

if not exist assets\icon.ico python make_icon.py

python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name MCTimer --icon assets\icon.ico mctimer.py || goto :err

copy /y dist\MCTimer.exe "上班激励计时器.exe" >nul
echo.
echo 构建完成: 上班激励计时器.exe
pause
exit /b 0

:err
echo 构建失败
pause
exit /b 1
