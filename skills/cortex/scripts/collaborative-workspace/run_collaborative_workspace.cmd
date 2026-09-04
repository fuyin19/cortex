@echo off
if not defined CORTEX_PYTHON (
  >&2 echo cortex collaborative workspace runtime error: cortex_python_required
  exit /b 70
)
"%CORTEX_PYTHON%" -I -B "%~dp0run_collaborative_workspace.py" %*
exit /b %ERRORLEVEL%
