"""
Settings for the production web server (gunicorn), used on Railway:
    gunicorn -c gunicorn.conf.py app:app
One worker keeps a single scheduler (freezing counts + hourly backups);
8 threads let it serve many phones at the same time.
"""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 1
threads = 8
timeout = 60
accesslog = "-"
errorlog = "-"


def post_worker_init(worker):
    from app import init_db, start_scheduler
    init_db()
    start_scheduler()