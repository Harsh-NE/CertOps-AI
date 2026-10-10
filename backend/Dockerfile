FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY . .
# Train the (synthetic-data) risk model into the image so scores come from the model, not the fallback rule.
RUN python -m app.ml.train --bootstrap 50

EXPOSE 8000
# Apply migrations, then serve. Single worker: the in-process scheduler must not run twice.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
