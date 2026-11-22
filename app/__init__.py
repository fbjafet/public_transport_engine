from flask import Flask

from app.config import Config
from app.db import init_db
from routes.dashboard import dashboard_bp
from routes.disruptions import disruptions_bp


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_object(Config)

    init_db()

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(disruptions_bp)

    return app