# -*- coding: utf-8 -*-
"""TV-режим (Kodi/браузер на ТВ, CRT и LCD): маршрут /tv.

Страница автономна (не наследует base.html): крупные шрифты, safe-зона
под overscan, навигация пультом (D-pad), масштаб ±. Данные — существующие
/api/dashboard + /api/weather-status (без дублирования агрегатов).
"""
from flask import render_template


def register_routes(app, ctx):
    login_required = ctx.login_required
    page_data = ctx.page_data

    @app.route("/tv")
    @login_required
    def tv_page():
        return render_template("tv.html", **page_data())
