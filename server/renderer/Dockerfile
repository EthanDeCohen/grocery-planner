FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir playwright==1.47.0 fastapi==0.115.0 uvicorn==0.30.6
WORKDIR /app
COPY app.py .
EXPOSE 8081
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
