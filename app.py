"""English Word Study —— 应用工厂。

生产环境由 Gunicorn 调用本模块的 create_app()，不使用 `flask run`。
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from flask import Flask, flash, redirect, render_template, request, url_for  # noqa: E402
from flask_login import LoginManager, current_user  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from config import load_config  # noqa: E402
from extensions import csrf, db  # noqa: E402

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录后再访问该页面"
login_manager.login_message_category = "info"
login_manager.session_protection = "strong"


def ensure_instance_dir(app: Flask) -> None:
    """确保 SQLite 所在目录存在（Docker 卷挂载后在宿主机 ./data）。"""
    url = app.config.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)


def create_app(config_object=None):
    cfg = config_object or load_config()

    # Flask 根路径固定到项目目录，模板与静态资源位于 ./app/ 下
    template_dir = os.path.join(BASE_DIR, "app", "templates")
    static_dir = os.path.join(BASE_DIR, "app", "static")
    app = Flask(__name__, instance_relative_config=False,
                template_folder=template_dir, static_folder=static_dir,
                static_url_path="/static")

    app.config.from_object(cfg)
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    # 双语（中文 + English）文案层：模板中可直接用 bi() / t() / bi_plain()
    from i18n import bi, bi_plain, t
    from media_service import audio_url, cover_url
    app.jinja_env.globals.update(bi=bi, t=t, bi_plain=bi_plain,
                                 audio_url=audio_url, cover_url=cover_url)

    # 生产环境必须有 SECRET_KEY
    if not app.config.get("SECRET_KEY"):
        if os.environ.get("FLASK_ENV", "").lower() == "production":
            raise RuntimeError("生产环境必须设置 SECRET_KEY 环境变量！")
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-key-change-me"

    app.config["SQLALCHEMY_DATABASE_URI"] = cfg.DATABASE_URL

    from models import TEST_MODE_LABELS  # noqa: F401

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # 为既有数据库补齐本轮新增列（幂等增量，不改动既有数据）
    from schema_compat import ensure_user_columns
    ensure_user_columns(app)

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        flash("请先登录后再继续 😊", "info")
        return redirect(url_for("auth.login", next=request.path))

    _register_blueprints(app)
    _register_security(app)
    _register_errors(app)
    _register_cli(app)
    _register_template_globals(app)
    _register_filters(app)

    ensure_instance_dir(app)

    with app.app_context():
        db.create_all()
        # V5：词库 CEFR 分级元数据幂等回填（空表才写）
        try:
            from path_service import ensure_word_meta
            ensure_word_meta()
        except Exception as exc:  # pragma: no cover
            logger.warning("word_meta 回填跳过：%s", exc)

    _setup_logging(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from auth import auth_bp
    from routes.admin import admin_bp
    from routes.api import api_bp
    from routes.games import games_bp
    from routes.learn import learn_bp
    from routes.main import main_bp
    from routes.podcast import podcast_bp
    from routes.quiz import quiz_bp
    from routes.words import words_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(words_bp)
    app.register_blueprint(quiz_bp)
    app.register_blueprint(learn_bp)
    app.register_blueprint(games_bp)
    app.register_blueprint(podcast_bp)
    app.register_blueprint(api_bp)      # REST API v1（未来多端共用）
    app.register_blueprint(admin_bp)


def _register_security(app: Flask) -> None:
    """安全响应头 + 反向代理适配。"""

    @app.after_request
    def set_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
        resp.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
        return resp


def _register_errors(app: Flask) -> None:
    @app.errorhandler(429)
    def too_many(err):
        return render_template("errors/429.html"), 429

    @app.errorhandler(403)
    def forbidden(err):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(err):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(err):
        return render_template("errors/500.html"), 500

    @app.errorhandler(Exception)
    def unhandled(err):
        if isinstance(err, HTTPException):
            code = err.code or 500
            if code == 404:
                return render_template("errors/404.html"), 404
            if code == 403:
                return render_template("errors/403.html"), 403
            return render_template("errors/500.html"), code
        app.logger.exception("未捕获异常: %s", err)
        if app.testing or app.debug:
            raise err
        return render_template("errors/500.html"), 500


def _register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db():
        """创建数据表（不存在才建）。"""
        with app.app_context():
            db.create_all()
        print("✔ 数据表已就绪")

    @app.cli.command("seed-words")
    def seed_words():
        """把 data/words.json 导入 words 表。"""
        from seeds.seed_words import seed_from_json
        with app.app_context():
            created, updated = seed_from_json(app.config["WORDS_JSON"])
        print(f"✔ 词库导入完成：新增 {created} / 更新 {updated}")

    @app.cli.command("create-admin")
    def create_admin():
        """创建管理员：EMAIL / PASSWORD 环境变量或交互输入。"""
        from models import User
        email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
        password = os.environ.get("ADMIN_PASSWORD") or ""
        username = os.environ.get("ADMIN_USERNAME") or "管理员"
        if not email or not password:
            print("✘ 请用 ADMIN_EMAIL 与 ADMIN_PASSWORD 环境变量指定管理员账号")
            return
        with app.app_context():
            user = User.query.filter_by(email=email).first()
            if user:
                user.is_admin = True
                # ADMIN_PASSWORD 必填，因此这里顺带重置密码（便于找回管理员密码）
                user.set_password(password)
                print(f"✔ 已将 {email} 设为管理员并重置密码")
            else:
                user = User(email=email, username=username, is_admin=True)
                user.set_password(password)
                db.session.add(user)
                print(f"✔ 已创建管理员 {email}")
            db.session.commit()

    @app.cli.command("bootstrap")
    def bootstrap():
        """一键初始化：建表 + 导词库 + 按 .env 建管理员。"""
        from seeds.seed_words import seed_from_json
        with app.app_context():
            db.create_all()
            created, updated = seed_from_json(app.config["WORDS_JSON"])
            print(f"✔ 词库：新增 {created} / 更新 {updated}")
            email = app.config.get("ADMIN_BOOTSTRAP_EMAIL")
            pwd = app.config.get("ADMIN_BOOTSTRAP_PASSWORD")
            if email and pwd:
                from models import User
                user = User.query.filter_by(email=email).first()
                if not user:
                    user = User(email=email, username="管理员", is_admin=True)
                    user.set_password(pwd)
                    db.session.add(user)
                    print(f"✔ 已创建管理员 {email}")
                else:
                    user.is_admin = True
                    print(f"✔ 已提升为管理员 {email}")
                db.session.commit()
        print("✔ 初始化完成")

    @app.cli.command("purge-codes")
    def purge_codes():
        """清理已过期 / 已使用的邮箱验证码（可放进定时任务）。"""
        from email_code import purge_expired
        with app.app_context():
            n = purge_expired()
        print(f"✔ 已清理 {n} 条过期验证码")


def _register_template_globals(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        from models import TEST_MODE_LABELS
        from services import dashboard_stats
        from flask import request, url_for
        from models import SKILLS
        stats = None
        gam = None
        skills = []
        ui_lang = "both"   # zh / en / both（双语自动）
        if current_user and current_user.is_authenticated:
            try:
                stats = dashboard_stats(current_user.id)
                from gamification import overview
                gam = overview(current_user.id)
            except Exception:
                stats = None
                gam = None
            # 顶部 Learn 下拉：六项技能（数据来自 models.SKILLS，单一来源）
            for s in SKILLS:
                item = dict(s)
                item["url"] = (url_for("words.learn") if s["key"] == "vocabulary"
                               else url_for("learn.skill", key=s["key"]))
                skills.append(item)
            if getattr(current_user, "preferred_lang", None) in ("zh", "en"):
                ui_lang = current_user.preferred_lang
        cookie_lang = (request.cookies.get("ui_lang") or "").strip()
        if cookie_lang in ("zh", "en", "both"):
            ui_lang = cookie_lang
        return dict(app_title=app.config.get("APP_TITLE_CN", "英语单词学习"),
                    app_title_en=app.config.get("APP_TITLE_EN", "English Learning Platform"),
                    app_version=app.config.get("APP_VERSION", "5.0.0"),
                    domain=app.config.get("DOMAIN", ""),
                    mode_labels=TEST_MODE_LABELS,
                    nav_stats=stats,
                    nav_gam=gam,
                    nav_skills=skills,
                    ui_lang=ui_lang)


def _register_filters(app: Flask) -> None:
    POS_LABELS = {"n": "名词", "v": "动词", "vt": "及物动词", "vi": "不及物动词",
                  "adj": "形容词", "adv": "副词", "prep": "介词", "pron": "代词",
                  "conj": "连词", "num": "数词", "art": "冠词", "int": "感叹词"}

    @app.template_filter("pos_text")
    def pos_text(p):
        return POS_LABELS.get((p or "").lower(), "")

    @app.template_filter("dt")
    def dt_filter(value, fmt="%Y-%m-%d %H:%M"):
        return value.strftime(fmt) if value else ""

    @app.template_filter("date_only")
    def date_only(value):
        return value.strftime("%Y-%m-%d") if value else ""


def _setup_logging(app: Flask) -> None:
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    if app.debug or app.testing:
        return
    handler = logging.FileHandler(os.path.join(BASE_DIR, "logs", "app.log"), encoding="utf-8")
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# WSGI 入口：Gunicorn 使用 `gunicorn -w 2 -b 0.0.0.0:8000 app:app`
# ---------------------------------------------------------------------------
app = create_app()


if __name__ == "__main__":
    # 仅本地调试用；生产请用 Gunicorn
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
