FROM python:3.12-slim
WORKDIR /work
COPY database/finance/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY database/finance/ ./
# COPY can preserve private host modes from git pull under umask 077.
# Keep root ownership; services need read/traverse access, not write access.
RUN chmod -R u=rwX,go=rX /work && useradd --system --uid 10001 migrations
USER migrations
CMD ["alembic","-c","alembic.ini","upgrade","head"]
