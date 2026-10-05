# ==========================================================================
# 第一阶段：构建 —— 安装依赖到独立目录，减小最终镜像体积
# ==========================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install -r requirements.txt

# ==========================================================================
# 第二阶段：运行 —— 只带运行所需内容，服务器无需安装 Python
# ==========================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PYTHONPATH=/app

WORKDIR /app

# 复制第一阶段安装的依赖
COPY --from=builder /install /usr/local

COPY requirements.txt .
# 用通配复制根目录模块：新增 i18n.py / mailer.py / schema_compat.py / email_code.py 等
# 不必再改 Dockerfile，避免"本地能跑、镜像里 ImportError" 的问题
COPY *.py ./
COPY routes/ ./routes/
COPY utils/ ./utils/
COPY seeds/ ./seeds/
COPY app/templates/ ./app/templates/
COPY app/static/ ./app/static/
COPY app/data/ ./app/data/

# 数据库与日志目录（宿主机 ./data、./logs 会挂载到这里）
RUN mkdir -p /app/instance /app/logs \
    && useradd --system --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# 生产环境用 Gunicorn 启动（2 worker × 2 线程，2C2G 机器友好）
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
