FROM python
RUN pip install pycron

ADD DeFlock /app
WORKDIR /app

CMD ["python", "./main.py"]