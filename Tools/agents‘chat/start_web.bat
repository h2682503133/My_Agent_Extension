@echo off
chcp 65001 >nul
echo ======================================
echo 双智能体对话调度器 - 手机比例Web前端
echo 注意：必须先启动 gateway-backend-service 网关(127.0.0.1:8080)
echo 启动后用手机/浏览器访问：
echo   http://127.0.0.1:8090        (本机)
echo   http://本机局域网IP:8090     (同一WiFi下的手机)
echo ======================================
echo.

:: 激活conda基础环境，再进入agent虚拟环境
call conda activate base
call conda activate agent

:: 运行程序
python web_server.py

echo.
echo 程序退出，按任意键关闭窗口
pause >nul
