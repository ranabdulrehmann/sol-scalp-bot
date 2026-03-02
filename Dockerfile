FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ccxt pandas numpy

COPY bot.py .


CMD ["python", "-u", "bot.py"]
