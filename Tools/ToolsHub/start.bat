@echo off
echo ======================================
echo  ToolsHub - Tool Aggregator / MSA Bridge
echo  Web UI: http://127.0.0.1:8077
echo ======================================
echo.

call conda activate base
call conda activate agent

python hub_server.py

echo.
echo Server stopped. Press any key to close.
pause >nul
