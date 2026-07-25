FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY snmp_agent.py .
COPY templates ./templates
RUN mkdir -p /app/incoming_reports

EXPOSE 10161/udp
EXPOSE 8000

CMD ["python", "snmp_agent.py"]
