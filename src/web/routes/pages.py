"""페이지 라우팅"""
from flask import Blueprint, render_template, session, redirect, url_for

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("pages.login_page"))
    return render_template("index.html")


@pages_bp.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("pages.index"))
    return render_template("login.html")
