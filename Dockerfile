FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir cryptography pysnmp watchdog fastapi uvicorn python-multipart

COPY snmp_agent.py .
RUN mkdir -p /app/incoming_reports

EXPOSE 10161/udp
EXPOSE 8000

CMD ["python", "snmp_agent.py"]
