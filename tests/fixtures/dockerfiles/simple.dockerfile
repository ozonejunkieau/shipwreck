FROM python:3.12-slim
RUN pip install flask
COPY . /app
CMD ["python", "app.py"]
