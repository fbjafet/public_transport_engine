import os

from app import create_app
from app.config import Config

app = create_app()

if __name__ == "__main__":
    # Development server only. Never enable the debugger outside development:
    # the Werkzeug debugger allows remote code execution.
    debug = Config.APP_ENV == "development" and os.getenv("FLASK_DEBUG") == "1"
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=debug,
        threaded=True,
    )
