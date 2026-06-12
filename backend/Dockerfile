# python backend (FastAPI). The previous node backend is preserved in
# Dockerfile.node and the *.js files, which are no longer used.
#
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5000"]
