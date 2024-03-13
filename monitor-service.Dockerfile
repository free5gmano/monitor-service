FROM python:3.8.10

WORKDIR /workdir

COPY requirements.txt .

COPY test.py .

RUN pip install -r requirements.txt

CMD ["python3", "test.py"]
