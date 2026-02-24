"""라우트 Blueprint 등록"""
from flask import Flask


def register_blueprints(app: Flask):
    from .pages import pages_bp
    from .api_auth import auth_bp
    from .api_order import order_bp
    from .api_report import report_bp
    from .api_home import home_bp
    from .api_prediction import prediction_bp
    from .api_rules import rules_bp
    from .api_new_product import new_product_bp
    from .api_waste import waste_bp
    from .api_logs import logs_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(order_bp, url_prefix="/api/order")
    app.register_blueprint(report_bp, url_prefix="/api/report")
    app.register_blueprint(home_bp, url_prefix="/api/home")
    app.register_blueprint(prediction_bp, url_prefix="/api/prediction")
    app.register_blueprint(rules_bp, url_prefix="/api/rules")
    app.register_blueprint(new_product_bp, url_prefix="/api/new-product")
    app.register_blueprint(waste_bp, url_prefix="/api/waste")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
