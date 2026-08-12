@echo off
chcp 65001 >nul
echo ======================================
echo LLBot 群聊监控
echo 首次启动请按提示输入 llbot.exe / QQ.exe 路径
echo ======================================
echo.

:: 激活conda环境（与 Tools/agents'chat 一致）
call conda activate base
call conda activate agent

python main.py

echo.
echo 程序退出，按任意键关闭窗口
pause >nul
