import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "gthread"
workers = 1           # one process: the site keeps its option list in memory
threads = 8           # several visitors at once
timeout = 900         # downloads and conversions can take minutes
graceful_timeout = 30
accesslog = "-"
errorlog = "-"