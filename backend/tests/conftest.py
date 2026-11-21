import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AUTH_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("INTERNAL_GRPC_TOKEN", "test-internal-token-with-32-characters")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-with-32-characters")
os.environ.setdefault("API_KEY_PEPPER", "test-api-pepper-with-32-characters")
os.environ.setdefault("DISCORD_BOT_INTERNAL_TOKEN", "test-bot-token-with-32-characters")
