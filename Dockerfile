# lanes in a container. Keep /data on a volume: the database and every file live there.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir /data && chown 10000:10000 /data
COPY app.py ./
COPY templates templates
COPY static static
ENV LANES_DATA=/data LANES_HOST=0.0.0.0 LANES_PORT=8080 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10000:10000
EXPOSE 8080
CMD ["python", "app.py"]
