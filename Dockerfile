FROM python
RUN pip install schedule

ADD DeFlock /app
WORKDIR /app

CMD ["python", "./main.py"]