@echo off
if not defined CORTEX_PYTHON (
  >&2 echo cortex notes skill runtime error: cortex_python_required
  exit /b 70
)
"%CORTEX_PYTHON%" -I "%~dp0run_notes.py" %*
exit /b %ERRORLEVEL%
