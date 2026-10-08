"""English Word Study —— 应用工厂。

生产环境由 Gunicorn 调用本模块的 create_app()，不使用 `flask run`。
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta

import click
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
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("english_app")
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
    # V5.1：数据字段本地化助手（pick 系列）—— 注册为 Jinja 全局，
    # 确保被 `{% import ... %}` 引入的 macro 内部也能用到
    # （否则 macro 中 pick 为 undefined）。
    from localization import (pick, pick_pair, title_of, desc_of, meaning_of,
                              example_of, lang_label, translate, t_ui,
                              pos_label, is_english, flash_l)
    app.jinja_env.globals.update(bi=bi, t=t, bi_plain=bi_plain,
                                 audio_url=audio_url, cover_url=cover_url,
                                 pick=pick, pick_pair=pick_pair,
                                 title_of=title_of, desc_of=desc_of,
                                 meaning_of=meaning_of, example_of=example_of,
                                 lang_label=lang_label, translate=translate,
                                 t_ui=t_ui, pos_label=pos_label,
                                 is_english=is_english)

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
    from schema_compat import ensure_content_columns, ensure_user_columns
    ensure_user_columns(app)
    ensure_content_columns(app)

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        # 语言感知：English 版面只显示英文提示，绝不出现中文（V5 硬约束）
        flash_l("请先登录后再继续 😊", "Please log in to continue 😊", "info")
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
        # V5：English / Cantonese 课程体系幂等播种
        try:
            from seed_courses import seed_courses
            seed_courses()
        except Exception as exc:  # pragma: no cover
            logger.warning("课程播种跳过：%s", exc)
        # V5.1：给空壳课时补内容 + 回填英文释义（English 版面零中文的数据前提）
        try:
            from seed_courses import fill_missing_content
            fill_missing_content()
        except Exception as exc:  # pragma: no cover
            logger.warning("课时内容补齐跳过：%s", exc)
        try:
            from translation_service import backfill_english
            backfill_english(app)
        except Exception as exc:  # pragma: no cover
            logger.warning("英文释义回填跳过：%s", exc)
        # V5.2：等级主数据 + 商业化商品（§17 / §19），均为幂等播种
        try:
            from payment_service import ensure_levels, ensure_products
            ensure_levels()
            ensure_products()
        except Exception as exc:  # pragma: no cover
            logger.warning("levels/products 播种跳过：%s", exc)

    # V5.1：English 版面在服务端剥离中文节点（不只是 CSS 隐藏）
    @app.after_request
    def _strip_other_language(resp):
        try:
            from localization import resolve_lang, strip_other_language
            lang = resolve_lang()
            if (lang == "en"
                    and (resp.content_type or "").startswith("text/html")):
                data = resp.get_data(as_text=True)
                cleaned = strip_other_language(data, lang)
                if cleaned != data:
                    resp.set_data(cleaned)
        except Exception:  # pragma: no cover - 剥离失败绝不能影响正常响应
            pass
        return resp

    _setup_logging(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from auth import auth_bp
    from routes.admin import admin_bp
    from routes.ai_tutor import ai_tutor_bp
    from routes.assessment import assessment_bp
    from routes.api import api_bp
    from routes.speaking import speaking_bp
    from routes.games import games_bp
    from routes.learn import learn_bp
    from routes.main import main_bp
    from routes.podcast import podcast_bp
    from routes.practice import practice_bp
    from routes.quiz import quiz_bp
    from routes.settings import settings_bp
    from routes.words import words_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(words_bp)
    app.register_blueprint(quiz_bp)
    app.register_blueprint(learn_bp)
    app.register_blueprint(games_bp)
    app.register_blueprint(podcast_bp)
    app.register_blueprint(practice_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)      # REST API v1（未来多端共用）
    app.register_blueprint(ai_tutor_bp)  # V5.7 AI Tutor JSON API
    app.register_blueprint(speaking_bp)  # V5.8 Speaking / Listening API
    app.register_blueprint(assessment_bp)  # V6.0.1 AI Assessment
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

    @app.cli.command("recompute-mastery")
    @click.option("--user-id", type=int, default=None,
                  help="只重算指定用户；不传则全量重算")
    def recompute_mastery(user_id):
        """按当前统一 0-4 规则重算掌握度 level。

        用途：掌握度规则演进后（如 V5.5 初步规则 → V5.6 统一规则），
        老数据仍是按旧规则算出的 level，需要一次性刷平。

        **幂等**，可反复执行；只在 level 真的变化时才写库，
        因此第二次执行应该是「变更 0 行」。
        """
        from mastery_service import recompute
        with app.app_context():
            n = recompute(user_id)
        scope = f"用户 {user_id}" if user_id is not None else "全量"
        print(f"✔ 掌握度重算完成（{scope}）：更新 {n} 行")

    @app.cli.command("purge-usage")
    @click.option("--days", type=int, default=90,
                  help="保留最近 N 天的用量记录，默认 90")
    def purge_usage(days):
        """清理过期的每日用量计数行（usage_counters）。

        计数表是「每用户 × 每功能 × 每天」一行，长期运行会持续膨胀，
        而 90 天前的行已无任何查询价值 —— 建议挂月度定时任务清理。
        """
        from entitlements import purge_old_usage
        with app.app_context():
            n = purge_old_usage(days)
        print(f"✔ 已清理 {n} 条 {days} 天前的用量记录")


def _register_template_globals(app: Flask) -> None:
    try:
        from localization import UI_LANGS as _UI_LANGS
    except Exception:  # pragma: no cover
        _UI_LANGS = ("zh", "en", "yue")

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
                item["url"] = (url_for("words.daily") if s["key"] == "vocabulary"
                               else url_for("learn.skill", key=s["key"]))
                skills.append(item)
            if getattr(current_user, "preferred_lang", None) in _UI_LANGS:
                ui_lang = current_user.preferred_lang
        cookie_lang = (request.cookies.get("ui_lang") or "").strip()
        if cookie_lang in ("zh", "en", "yue", "both"):
            ui_lang = cookie_lang

        # V5.1：数据字段本地化助手 —— 「English 版面零中文」的模板入口
        import localization as _loc
        title_cn = app.config.get("APP_TITLE_CN", "AI 语言学习平台")
        title_en = app.config.get("APP_TITLE_EN", "AI Language Learning Platform")
        # English 版面下站点名也必须是英文（<title>、footer 都用这个变量）
        app_title = title_en if ui_lang == "en" else title_cn
        return dict(app_title=app_title,
                    app_title_en=title_en,
                    app_version=app.config.get("APP_VERSION", "5.1.0"),
                    domain=app.config.get("DOMAIN", ""),
                    mode_labels=TEST_MODE_LABELS,
                    nav_stats=stats,
                    nav_gam=gam,
                    nav_skills=skills,
                    ui_lang=ui_lang,
                    ui_langs=_loc.UI_LANGS,
                    lang_flags=_loc.LANG_FLAGS,
                    lang_switch_url=url_for("main.set_lang"),
                    pick=_loc.pick,
                    pick_pair=_loc.pick_pair,
                    title_of=_loc.title_of,
                    desc_of=_loc.desc_of,
                    meaning_of=_loc.meaning_of,
                    example_of=_loc.example_of,
                    lang_label=_loc.lang_label,
                    t_flash=_loc.t_flash)


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
