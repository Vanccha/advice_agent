FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/service
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY common /app/common
COPY mcp_ticketing /app/service
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=25s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(\"http://127.0.0.1:8000/health\",timeout=2).status==200 else 1)"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
