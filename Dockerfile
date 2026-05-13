FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN uv pip install --system --no-cache-dir .

# Copy source and Streamlit config
COPY src/ src/
COPY .streamlit/ .streamlit/

# Copy default configs (used when bind mounts are missing)
COPY config.toml /app/defaults/config.toml
COPY gallery-dl.*.conf /app/defaults/

VOLUME /app/data
EXPOSE 8501

CMD ["streamlit", "run", "src/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
