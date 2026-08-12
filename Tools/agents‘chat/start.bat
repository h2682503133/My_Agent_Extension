@echo off
chcp 65001 >nul
echo ======================================
echo 双智能体对话GUI调度器 (conda环境: agent)
echo 注意：必须先启动Flask网关，再运行本脚本
echo 模拟用户：agent_a 、 agent_b
echo ======================================
echo.

:: 激活conda基础环境，再进入agent虚拟环境
call conda activate base
call conda activate agent

:: 运行程序
python main.py

echo.
echo 程序退出，按任意键关闭窗口
pause >nul