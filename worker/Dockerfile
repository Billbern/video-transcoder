# Worker re-uses the backend image; docker-compose runs the Celery command instead of uvicorn.
FROM backend-base

CMD ["celery", "-A", "app.workers.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
