"""라우트 Blueprint 등록"""
from flask import Flask


def register_blueprints(app: Flask):
    from .pages import pages_bp
    from .api_auth import auth_bp
    from .api_order import order_bp
    from .api_report import report_bp
    from .api_home import home_bp
    from .api_prediction import prediction_bp
    from .api_new_product import new_product_bp
    from .api_waste import waste_bp
    from .api_logs import logs_bp
    from .api_health import health_bp
    from .api_inventory import inventory_bp
    from .api_receiving import receiving_bp
    from .api_settings import settings_bp
    from .api_category import category_bp
    from .api_food_monitor import food_monitor_bp
    from .api_dessert_decision import dessert_decision_bp
    from .api_beverage_decision import beverage_decision_bp
    from .api_category_decision import category_decision_bp
    from .api_integrity import integrity_bp
    from .api_association import association_bp
    from .onboarding import onboarding_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(order_bp, url_prefix="/api/order")
    app.register_blueprint(report_bp, url_prefix="/api/report")
    app.register_blueprint(home_bp, url_prefix="/api/home")
    app.register_blueprint(prediction_bp, url_prefix="/api/prediction")
    app.register_blueprint(new_product_bp, url_prefix="/api/new-product")
    app.register_blueprint(waste_bp, url_prefix="/api/waste")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
    app.register_blueprint(health_bp, url_prefix="/api/health")
    app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
    app.register_blueprint(receiving_bp, url_prefix="/api/receiving")
    app.register_blueprint(settings_bp, url_prefix="/api/settings")
    app.register_blueprint(category_bp, url_prefix="/api/categories")
    app.register_blueprint(food_monitor_bp, url_prefix="/api/food-monitor")
    app.register_blueprint(dessert_decision_bp, url_prefix="/api/dessert-decision")
    app.register_blueprint(beverage_decision_bp, url_prefix="/api/beverage-decision")
    app.register_blueprint(category_decision_bp, url_prefix="/api/category-decision")
    app.register_blueprint(integrity_bp, url_prefix="/api/integrity")
    app.register_blueprint(association_bp, url_prefix="/api/association")
    app.register_blueprint(onboarding_bp)
