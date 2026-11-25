FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY roles.py auth.py notify.py main.py monitor.py ./

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
