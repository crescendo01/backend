# Optional: only needed if you deploy this as a Render "Docker" service
# instead of a native "Python" service (render.yaml uses native Python,
# which builds faster — see README.md "Deploying to Render"). Both produce
# the same running service; pick whichever you're more comfortable with.

FROM python:3.12-slim

# Faster, quieter pip; unbuffered logs so they show up in Render's log
# stream immediately instead of waiting for an internal buffer to flush.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Render injects $PORT at runtime; default to 8000 for `docker run` locally.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
