FROM python:3.11-slim

WORKDIR /app

# Ingestion (ingestion/*.py) needs python3.14 locally and has its own
# requirements file (ingestion/requirements.txt) — none of that runs in
# this container. Only app.py's dependency closure is installed here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0", "--server.headless=true"]
