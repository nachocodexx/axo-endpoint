# Built with build context = repo root (see docker-compose.yml's axo-storage
# service) so axo_shared/ -- a sibling path dependency -- is reachable,
# mirroring the on-disk repo layout (axo_shared/ next to axo_storage/).
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app
COPY axo_shared/ /app/axo_shared/

WORKDIR /app/axo_storage
COPY axo_storage/pyproject.toml axo_storage/poetry.lock* ./
RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry install --without dev --no-root --no-interaction --no-ansi

COPY axo_storage/ ./
RUN poetry install --without dev --no-interaction --no-ansi \
    && pip uninstall -y poetry

EXPOSE 7000

CMD ["python", "-m", "axo_storage"]
