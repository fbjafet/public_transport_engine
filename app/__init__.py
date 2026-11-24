import logging

from flask import Flask, jsonify

from app.config import Config, validate_config
from app.timeutil import make_localtime_filter


def create_app(config_overrides: dict | None = None) -> Flask:
    # Imports are local so that lightweight modules (config, security helpers)
    # can be imported and unit-tested without the database stack.
    from app.cli import register_cli
    from app.db import init_db
    from app.errors import register_error_handlers
    from app.security import init_security
    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.disruptions import disruptions_bp
    from services.sse_service import broker

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    validate_config(app.config)

    if not app.config["TESTING"] and not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    app.jinja_env.filters["localtime"] = make_localtime_filter(app.config["APP_TIMEZONE"])

    if app.config["TRUSTED_PROXY_COUNT"] > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix

        n = app.config["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    if app.config["AUTO_CREATE_SCHEMA"]:
        init_db()

    broker.configure(
        max_clients=app.config["SSE_MAX_CLIENTS"],
        max_per_owner=app.config["SSE_MAX_PER_USER"],
    )

    init_security(app)
    register_error_handlers(app)
    register_cli(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(disruptions_bp)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app
