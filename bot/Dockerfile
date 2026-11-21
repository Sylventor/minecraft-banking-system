FROM python:3.12-slim
WORKDIR /app
COPY bot/pyproject.toml ./
COPY bot/app ./app
RUN pip install --no-cache-dir .
RUN chmod -R u=rwX,go=rX /app && useradd --system --uid 10001 horizon-bot
USER horizon-bot
CMD ["python","-m","app.main"]
