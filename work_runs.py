import os, re, sys
sys.path.insert(0, os.getcwd())
from config import TestingConfig
from app import create_app
from extensions import db
from models import User

app = create_app(TestingConfig)
app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    db.create_all()
    from seeds.seed_words import seed_from_json
    seed_from_json(app.config["WORDS_JSON"])
    c = app.test_client()
    e = "scanner@demo.com"
    if not User.query.filter_by(email=e).first():
        c.post("/register", data={"email": e, "username": "S",
                                  "password": "Scan123456", "confirm": "Scan123456"})
    c.post("/login", data={"email": e, "password": "Scan123456"})
    body = c.get("/learn", headers={"Cookie": "ui_lang=en"}).get_data(as_text=True)
    # both regex styles
    a = re.compile(r"[\u4e00-\u9fff]")
    b = re.compile(r"[一-鿿]")
    print("literal-range count:", len(b.findall(body)))
    print("escaped-range count:", len(a.findall(body)))
    print('contains "语言学习平台":', "语言学习平台" in body)
    print('contains "AI Language":', "AI Language" in body)
    import re as _re
    m = _re.search(r"<title[^>]*>(.*?)</title>", body, _re.S)
    print("TITLE:", m.group(1) if m else "(none)")
    for mm in _re.findall(r'<meta[^>]*content="([^"]*语言[^"]*)"', body):
        print("META:", mm)
    # show a snippet around first meta
    idx = body.find("description")
    print("SNIPPET:", body[idx-40:idx+120] if idx >= 0 else "no description meta")
