FROM python:3.8.10

WORKDIR /workdir

COPY requirements.txt .

COPY monitor-service.py .

RUN pip install -r requirements.txt

CMD ["python3", "monitor-service.py"]
