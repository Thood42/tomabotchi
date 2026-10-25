# The "Web browser" home: Microsoft's Playwright image ships the browsers but not the Python
# package, so add the matching one. Built by tomabotchi (docker_ops.ensure_home).
FROM mcr.microsoft.com/playwright/python:v1.48.0
RUN pip install --no-cache-dir --break-system-packages playwright==1.48.0
