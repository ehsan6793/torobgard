FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY scripts/entrypoint.sh ./entrypoint.sh
# the dataset is shipped compressed (110 MB) and unpacked on first boot
COPY data/torob.db.gz ./data/torob.db.gz
RUN chmod +x entrypoint.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health')" || exit 1
CMD ["./entrypoint.sh"]
