import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/transit_ops",
    )
    READ_REPLICA_URL = os.getenv("READ_REPLICA_URL") or DATABASE_URL

    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_API_URL = os.getenv(
        "LLM_API_URL", "https://api.openai.com/v1/chat/completions"
    )
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")