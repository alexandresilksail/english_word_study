#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from config import TestingConfig

app = create_app(TestingConfig)
c = app.test_client()

for url in ["/", "/courses", "/games", "/podcast/", "/ai-tutor"]:
    try:
        r = c.get(url)
    except Exception as e:
        print(f"{url}: ERROR {e}")
        continue
    html = r.get_data(as_text=True)
    feat = html.count('class="feat-img"')
    imgs = html.count("<img")
    svg = html.count("<svg")
    # show a couple of sample feat-img srcs
    import re
    srcs = re.findall(r'class="feat-img" src="([^"]+)"', html)[:4]
    print(f"{url:12s} -> {r.status_code} | <img>={imgs} feat-img={feat} <svg>={svg} | sample: {srcs}")
