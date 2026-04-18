# Sarthi AI Backend

This Django backend supports user auth, relative medicine management, and automatic reminder calls through Vapi.

## Features

- JWT based authentication
- Relative and medicine management
- Medicine schedules with Celery task execution
- Automatic Vapi voice call trigger at medicine time
- Dockerized runtime for AWS deployment workflows

## Environment Variables

Create a `.env` file at project root (copy from `.env.example`).

Required Vapi keys:

- `VAPI_API_KEY`
- `VAPI_ASSISTANT_ID`
- `VAPI_PHONE_NUMBER_ID` (or use Twilio fields below)
- `VAPI_TWILIO_PHONE_NUMBER`
- `VAPI_TWILIO_ACCOUNT_SID`
- `VAPI_WEBHOOK_URL`
- `VAPI_WEBHOOK_SECRET`
- `VAPI_RETRY_DELAY_MINUTES`

Webhook path:

- `POST /api/vapi/webhook`
- Auth: `Authorization: Bearer <VAPI_WEBHOOK_SECRET>`
- Compact payload supported: `{ "id": "med_123", "taken": true }`
- Retry behavior: 3 patient attempts; if still not taken, 4th call escalates to the user.

Celery/Redis:

- `REDIS_URL` (recommended single setting)
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`

Use your cloud Redis URL here, for example Upstash or ElastiCache with TLS.

App settings:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `DATABASE_URL`

## Local Run (without Docker)

1. Install dependencies:
   - `pip install .`
2. Start Redis (default broker):
   - `redis-server`
3. Run migrations:
   - `python manage.py migrate`
4. Start Django:
   - `python manage.py runserver`
5. Start Celery worker in another terminal:
   - `celery -A config worker --loglevel=info`

## Docker Run

1. Create `.env` from `.env.example`.
2. Start services:
   - `docker compose up --build`

Services started:

- `web` (Django via gunicorn)
- `celery-worker`

The compose file now expects managed cloud database and Redis endpoints from `.env`.

## AWS Deployment Notes

- Build and push image to ECR.
- Run web and celery worker as separate ECS services (same image, different commands).
- Use a managed PostgreSQL database and ElastiCache or Upstash Redis for broker/backend.
- Inject all secrets through ECS task environment variables or AWS Secrets Manager.

## How Call Scheduling Works

- On creation of a `MedicineSchedule`, backend computes `next_run_at`.
- Celery enqueues a task for that ETA.
- At runtime task calls Vapi and records `last_called_at`.
- If schedule is recurring (`daily` or `weekly`), next run is enqueued automatically.
