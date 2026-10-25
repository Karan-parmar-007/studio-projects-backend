"""Maps Scraper: Google Maps company search, streamed. Callers choose only the term and places."""

PROJECT = "gmaps"

# Companies per user per UTC day, shared by the website and the API key.
DAILY_COMPANY_LIMIT = 50
# Runs at once across every user. Keeps the server's IP from being blocked by Google.
MAX_ACTIVE_RUNS = 2

DISCOVERY_WORKERS = 4
PER_ZIP_CAP = 20

# A run whose stream nobody starts reading within this time is stopped and refunded.
ATTACH_TIMEOUT_SECONDS = 15.0
# After the engine finishes, how long the stream gets to hand over the last rows.
CONSUMER_GRACE_SECONDS = 120.0

MAX_SEARCH_TERM = 200
MAX_PLACES = 50
MAX_PLACE_NAME = 100
