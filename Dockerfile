FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

# Keep the virtual environment outside /app.
# This is useful because /app will be bind-mounted during development.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependency files first for Docker layer caching
COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-install-project

# Now copy the application
COPY . .

RUN uv sync --locked

CMD ["uv", "run", "--no-sync", "uvicorn", "gamerec.main:app", "--host", "0.0.0.0", "--port", "8000"]