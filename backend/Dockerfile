FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/grpc-generated
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir .
COPY backend/alembic ./alembic
COPY backend/alembic-auth.ini ./
COPY core/proto /proto
RUN mkdir -p /app/grpc-generated && python -m grpc_tools.protoc -I/proto --python_out=/app/grpc-generated --grpc_python_out=/app/grpc-generated /proto/horizon/banking/v1/banking.proto && find /app/grpc-generated -type d -exec touch {}/__init__.py \;
EXPOSE 8000
# Normalize source modes before dropping privileges; keep code root-owned.
RUN chmod -R u=rwX,go=rX /app /proto && useradd --system --uid 10001 horizon
USER horizon
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
