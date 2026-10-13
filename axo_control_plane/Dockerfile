# Built with build context = repo root (see platform.compose.yml) so
# axo_shared/ -- a sibling path dependency -- is reachable, mirroring the
# on-disk repo layout (axo_shared/ next to axo_control_plane/).
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app
COPY axo_shared/ /app/axo_shared/
COPY axo_storage/ /app/axo_storage/

WORKDIR /app/axo_control_plane
COPY axo_control_plane/pyproject.toml axo_control_plane/poetry.lock* ./
RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry install --without dev --no-root --no-interaction --no-ansi

COPY axo_control_plane/ ./
RUN poetry install --without dev --no-interaction --no-ansi \
    && pip uninstall -y poetry

EXPOSE 9000 9100

CMD ["python", "-m", "axo_control_plane"]
