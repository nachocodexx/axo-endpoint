# 
FROM python:3.10

# 
WORKDIR /app
RUN pip3 install poetry

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

COPY poetry.lock  .
COPY pyproject.toml  .
COPY axo_shared/ /app/axo_shared/
# path dependency: the endpoint's workers run Axo classes through it
COPY axo/pyproject.toml axo/README.md /app/axo/
COPY axo/axo/ /app/axo/axo/
# path dev-dependency (endpoint tests run against a real axo_storage)
COPY axo_storage/ /app/axo_storage/
RUN touch README.md

RUN poetry config virtualenvs.create false && \
    poetry lock && \
    poetry install --no-root

COPY ./axo_endpoint/ /app/axo_endpoint
RUN poetry install
# COPY ./activex.tar.gz .
# RUN poetry remove activex && poetry add /app/activex.tar.gz
# CMD ["sleep","infinity"]
ENTRYPOINT [ "poetry", "run", "python3", "-m", "axo_endpoint.main" ]