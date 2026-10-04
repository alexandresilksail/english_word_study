# ==========================================================================
# Gunicorn 生产配置（gunicorn --config gunicorn.conf.py app:app）
# 2 vCPU / 2GB 内存的低配机器推荐：workers=2, threads=2
# ==========================================================================
bind = "0.0.0.0:8000"
workers = 2
worker_class = "gthread"
threads = 2
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 100
timeout = 60
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

preload_app = False
reload = False

# 前向 Nginx 时的信任代理头
forwarded_allow_ips = "127.0.0.1"
proxy_allow_from = "127.0.0.1"
