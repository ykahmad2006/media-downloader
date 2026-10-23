FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# JavaScript runtime that yt-dlp uses for YouTube (delete this line if the build can't pull it)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

RUN useradd -m app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER app

ENV PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
# start-period gives the update step below time to finish before health checks count
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:%s/healthz'%os.environ['PORT'])"

# Refresh yt-dlp and gallery-dl on every start: the sites change often
CMD ["sh", "-c", "pip install --user -q -U yt-dlp gallery-dl; exec gunicorn -c gunicorn.conf.py app:app"]