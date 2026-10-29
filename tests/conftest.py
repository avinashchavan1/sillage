import os

# Fixed key for the whole test session (auth.api_key() is cached on first use)
os.environ.setdefault("SILLAGE_API_KEY", "test-key")
API_HEADERS = {"Authorization": "Bearer test-key"}
