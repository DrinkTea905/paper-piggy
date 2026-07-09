@echo off
setlocal
set "HERE=%~dp0"
set "PYW="
if exist "%HERE%python\pythonw.exe" set "PYW=%HERE%python\pythonw.exe"
if not defined PYW if exist "%HERE%.venv\Scripts\pythonw.exe" set "PYW=%HERE%.venv\Scripts\pythonw.exe"
if not defined PYW for %%P in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:P"
if not defined PYW (
  echo [PaperPiggy] 未找到 Python 解释器 pythonw.exe。
  echo 请在本目录放置 python\ 子目录或 .venv\，或先安装 Python 后重试。
  echo 详情见 README.md。
  pause
  exit /b 1
)
start "" "%PYW%" "%HERE%launcher.py"
endlocal
