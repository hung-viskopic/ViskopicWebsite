FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY migrations ./migrations
COPY viskopic ./viskopic
# Frozen consistency model (Δ). Hash-checked at startup; see vendor/consistency_v13/VENDORED.md.
COPY vendor ./vendor
RUN useradd --system --uid 10001 viskopic
USER viskopic
EXPOSE 8000
