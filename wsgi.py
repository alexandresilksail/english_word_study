"""应用入口（WSGI / Gunicorn）。

生产启动方式：
    gunicorn -w 2 -b 0.0.0.0:8000 app:app
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    # 仅本地调试；生产请用 gunicorn
    app.run(host="0.0.0.0", port=8000, debug=False)
