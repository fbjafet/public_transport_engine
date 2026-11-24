"""Production server settings:  gunicorn -c gunicorn.conf.py run:app

ONE worker process with many threads. The live feed keeps its subscribers in
process memory, so several worker processes would each hold only some of the
clients and events would silently miss people. Each open live-feed connection
occupies a thread, so the thread count must exceed SSE_MAX_CLIENTS.
"""
import os

bind = f"{os.getenv('HOST', '127.0.0.1')}:{os.getenv('PORT', '8000')}"
workers = 1
worker_class = "gthread"
threads = int(os.getenv("SSE_MAX_CLIENTS", "100")) + 25
timeout = 60
graceful_timeout = 20
keepalive = 5
accesslog = "-"
errorlog = "-"
