# High-Performance E-Commerce Backend

A production-style e-commerce backend built with Django, Django REST Framework, PostgreSQL, Redis, Celery, Nginx, PgBouncer, Docker, and Locust.

This project is designed to demonstrate how a backend system can handle real performance and reliability problems under concurrent traffic: safe checkout, race-condition prevention, database connection control, asynchronous queues, chunk-based batch processing, distributed caching, and load balancing across multiple application containers.

## Table of Contents

- [Project Overview](#project-overview)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Main Features](#main-features)
- [Performance Requirements Covered](#performance-requirements-covered)
- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Quick Start](#quick-start)
- [Seed Test Data](#seed-test-data)
- [Useful URLs](#useful-urls)
- [Common Docker Commands](#common-docker-commands)
- [Load Testing With Locust](#load-testing-with-locust)
- [Requirement Evidence Commands](#requirement-evidence-commands)
- [API Endpoints](#api-endpoints)
- [Troubleshooting](#troubleshooting)

## Project Overview

The backend exposes REST APIs for users, products, carts, orders, checkout, administrative demo endpoints, and background processing. It is deployed as multiple Django/Gunicorn containers behind Nginx, with Redis for caching and task brokering, Celery for background jobs, PostgreSQL for persistent storage, and PgBouncer for database connection pooling.

The system is intentionally built to support load testing and university/report evidence. A new developer can run the full stack with Docker Compose, seed realistic e-commerce data, run Locust tests, and capture proof for each performance requirement.

## Technology Stack

| Area | Technology | Purpose |
| --- | --- | --- |
| Backend framework | Django | Main application framework |
| API framework | Django REST Framework | REST API endpoints and serializers |
| Database | PostgreSQL 16 | Relational data, transactions, stock, orders |
| DB pooling | PgBouncer | Controls direct PostgreSQL connections under load |
| Cache | Redis 7 | Product cache and shared cache layer |
| Message broker | Redis | Celery broker |
| Background jobs | Celery | Email, cache, cleanup, and batch processing tasks |
| Scheduler | Celery Beat | Scheduled jobs such as daily sales aggregation |
| Web server | Gunicorn | Runs Django WSGI app |
| Reverse proxy | Nginx | Load balancer and static file proxy |
| Containers | Docker / Docker Compose | Local deployment and orchestration |
| Load testing | Locust | Concurrent user simulation and requirement tests |
| Monitoring tools | Flower, pgAdmin | Celery and database inspection |

## Architecture

```text
Client / Locust
      |
      v
Nginx load balancer
      |
      +--> app1: Django + Gunicorn
      +--> app2: Django + Gunicorn
      +--> app3: Django + Gunicorn
               |
               v
            PgBouncer
               |
               v
          PostgreSQL

Django / Celery producers
      |
      v
Redis broker/cache
      |
      +--> celery_worker       queue=celery, concurrency=2
      +--> celery_email_worker queue=emails, concurrency=4
      +--> celery_batch_worker queue=batch,  concurrency=1
      +--> celery_beat         scheduled tasks
```

### Application Layer

The project runs three application containers: `ecommerce_app1`, `ecommerce_app2`, and `ecommerce_app3`. Each container runs the same Django application using Gunicorn:

```text
worker-class = gthread
workers      = 8
threads      = 3
```

This gives controlled parallel request handling while avoiding unbounded process creation.

### Load Balancing

Nginx is the public entry point on port `80`. It forwards traffic to `app1`, `app2`, and `app3` using the Least Connections algorithm. This is better than simple round-robin for mixed workloads because product reads, cart operations, login requests, and checkout requests do not all take the same amount of time.

### Database Connection Control

Django and Celery connect to PgBouncer at `pgbouncer:5432`. PgBouncer then maintains a smaller pool of backend connections to PostgreSQL. This prevents high Gunicorn/Celery concurrency from creating too many direct PostgreSQL connections.

### Background Processing

Celery tasks are separated by queue:

- `celery`: general tasks, cache invalidation, lock cleanup.
- `emails`: email-related tasks.
- `batch`: database-heavy batch jobs.

This prevents heavy batch jobs or email spikes from blocking unrelated background work.

## Main Features

- User authentication and token-based API access.
- Product catalog listing and detail endpoints.
- Redis-backed product caching.
- Cart add/view/clear operations.
- Safe checkout and order creation.
- Stock race-condition protection using transactions and row-level locking.
- Optimistic stock helper functions for retry-based inventory updates.
- Asynchronous order and email processing with Celery.
- Dedicated Celery queues for general, email, and batch jobs.
- Scheduled daily sales aggregation with Celery Beat.
- Chunk-based batch processing for large order datasets.
- Nginx load balancing across three Django/Gunicorn containers.
- PgBouncer transaction pooling for PostgreSQL connection control.
- Locust load tests for focused requirement validation.

## Performance Requirements Covered

### Requirement 1: Race Condition Safety

Checkout uses database transactions and row-level locking to prevent overselling. The safe checkout flow locks product rows during stock validation and deduction so concurrent users cannot make stock negative.

Important code areas:

- `apps/orders/views.py`
- `apps/orders/services.py`
- `apps/products/services.py`

### Requirement 2: Resource Management and Capacity Control

The stack controls resource usage through:

- Fixed Gunicorn workers and threads.
- PgBouncer connection pooling.
- PostgreSQL `max_connections` headroom.
- A dedicated capacity stress endpoint.
- Locust tests that report response time and observed DB connections.

### Requirement 3: Asynchronous Queues

Celery workers are separated into independent queues:

- `celery` for general tasks.
- `emails` for email tasks.
- `batch` for heavy batch tasks.

This improves isolation and prevents one task category from starving another.

### Requirement 4: Batch Processing

The optimized batch task processes daily sales in chunks instead of loading all orders into memory at once. This keeps memory usage controlled and makes the task safer for large datasets.

Important code:

- `apps/core/tasks.py`
- `apps/core/views.py`

### Requirement 5: Load Balancing

Nginx distributes requests across `app1`, `app2`, and `app3` using Least Connections. The Nginx access log includes the selected upstream backend, making load balancing easy to verify.

Important config:

- `docker-compose.yml`
- `nginx/nginx.conf`

### Requirement 6: Distributed Caching

Redis is used to cache frequently requested product data and reduce repeated PostgreSQL queries during product browsing load.

Important code:

- `apps/products/views.py`
- `config/settings.py`

## Prerequisites

Install:

- Docker
- Docker Compose v2

Check versions:

```bash
docker --version
docker compose version
```

## Environment Variables

The project uses `.env` for runtime configuration. Do not commit real secrets, passwords, SMTP credentials, or production keys. Keep the real `.env` file local, and use placeholders in documentation or in a safe `.env.example` file.

Example development template:

```env
DB_NAME=<database-name>
DB_USER=<database-user>
DB_PASSWORD=<database-password>
DB_HOST=pgbouncer
DB_PORT=5432
DB_ENGINE=django.db.backends.postgresql

DEBUG=true
SECRET_KEY=<development-secret-key>
ALLOWED_HOSTS=*

REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

CONN_MAX_AGE=60
```

For production, set `DEBUG=false`, use a strong `SECRET_KEY`, restrict `ALLOWED_HOSTS`, and store secrets in a secure secret manager or deployment environment variables.

## Quick Start

### 1. Clone the project

```bash
git clone <repo-url>
cd high-performance-ecommerce-backend
```

### 2. Start the full stack

```bash
docker compose up --build -d
```

This starts:

- PostgreSQL
- PgBouncer
- Redis
- migrations
- app1, app2, app3
- Celery workers
- Celery Beat
- Nginx
- Flower
- pgAdmin
- Locust service

### 3. Check containers

```bash
docker ps
```

Expected important containers:

```text
ecommerce_nginx
ecommerce_app1
ecommerce_app2
ecommerce_app3
ecommerce_db
ecommerce_pgbouncer
ecommerce_redis
ecommerce_celery_worker
ecommerce_celery_email_worker
ecommerce_celery_batch_worker
ecommerce_celery_beat
```

### 4. Run migrations manually if needed

The `migrate` service runs automatically, but this command is useful during development:

```bash
docker compose run --rm app1 python manage.py migrate
```

## Seed Test Data

The project includes a professional seeder for load testing.

Seed without deleting old data:

```bash
docker compose run --rm app1 python manage.py seed_ecommerce
```

Clean and reseed:

```bash
docker compose run --rm app1 python manage.py seed_ecommerce --clean
```

The seeder creates:

- 100 Locust test users.
- Regular users.
- Normal high-stock products.
- Hot low-stock products for race-condition tests.
- Historical orders for batch-processing tests.
- Carts and realistic product data.

Verify seed data:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db -c "SELECT COUNT(*) AS total_products FROM products_product;"
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db -c "SELECT COUNT(*) AS total_users FROM users_user;"
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db -c "SELECT COUNT(*) AS total_orders FROM orders_order;"
```

## Useful URLs

| Service | URL |
| --- | --- |
| API through Nginx | `http://localhost/` |
| Locust UI | `http://localhost:8089` or custom mapped port |
| Flower | `http://localhost:5555` |
| pgAdmin | `http://localhost:5050` |
| Nginx health | `http://localhost/nginx-health` |

pgAdmin default credentials from `docker-compose.yml`:

```text
Email: admin@admin.com
Password: admin
```

## Common Docker Commands

Start full stack:

```bash
docker compose up --build -d
```

Stop stack:

```bash
docker compose down
```

Stop stack and delete volumes/database data:

```bash
docker compose down -v
```

View running containers:

```bash
docker ps
```

View app logs:

```bash
docker logs -f ecommerce_app1
docker logs -f ecommerce_app2
docker logs -f ecommerce_app3
```

View Nginx logs:

```bash
docker logs -f ecommerce_nginx
```

View Celery logs:

```bash
docker logs -f ecommerce_celery_worker
docker logs -f ecommerce_celery_email_worker
docker logs -f ecommerce_celery_batch_worker
```

Open Django shell:

```bash
docker compose run --rm app1 python manage.py shell
```

Create superuser:

```bash
docker compose run --rm app1 python manage.py createsuperuser
```

Check database connection count:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db -c "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();"
```

Watch resource usage:

```bash
docker stats ecommerce_app1 ecommerce_app2 ecommerce_app3 ecommerce_pgbouncer ecommerce_db ecommerce_nginx ecommerce_redis
```

## Load Testing With Locust

The `locustfile.py` supports selectable test modes using `LOCUST_MODE`.

### Run Locust UI

```bash
docker compose run --rm -p 8090:8089 \
  -e LOCUST_MODE=normal \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80
```

Open:

```text
http://127.0.0.1:8090
```

In the UI:

```text
Number of users: 50
Ramp up: 10
Host: http://nginx:80
```

### Run Locust headless

```bash
docker compose run --rm \
  -e LOCUST_MODE=normal \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 50 --spawn-rate 10 --headless --run-time 1m
```

### Locust Modes

| Mode | Purpose |
| --- | --- |
| `normal` | Full normal e-commerce workload |
| `browsing` | Product browsing and cache-heavy reads |
| `race_before` | Unsafe race-condition demo endpoint |
| `req2` | Capacity/resource stress endpoint |
| `req3_sync` | Synchronous flow comparison |
| `req3_async` | Asynchronous flow comparison |
| `req4_before` | Naive batch processing demo |
| `req4_after` | Chunked batch processing demo |

## Requirement Evidence Commands

### Requirement 1: Race Condition Safety

Check low-stock products:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db \
  -c "SELECT id, name, stock FROM products_product WHERE stock <= 10 ORDER BY stock;"
```

Run normal safe checkout workload:

```bash
docker compose run --rm \
  -e LOCUST_MODE=normal \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 50 --spawn-rate 10 --headless --run-time 1m
```

Verify stock did not become negative:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db \
  -c "SELECT id, name, stock FROM products_product WHERE stock < 0;"
```

Expected result:

```text
0 rows
```

### Requirement 2: Resource Management and Capacity Control

Run capacity stress test:

```bash
docker compose run --rm \
  -e LOCUST_MODE=req2 \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 100 --spawn-rate 10 --headless --run-time 3m
```

Monitor database connections:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();"
```

Monitor containers:

```bash
docker stats ecommerce_app1 ecommerce_app2 ecommerce_app3 ecommerce_pgbouncer ecommerce_db ecommerce_nginx ecommerce_redis
```

### Requirement 3: Async Queues

Check active Celery queues:

```bash
docker compose exec celery_worker celery -A config inspect active_queues
docker compose exec celery_email_worker celery -A config inspect active_queues
docker compose exec celery_batch_worker celery -A config inspect active_queues
```

Expected queues:

```text
celery
emails
batch
```

Watch workers:

```bash
docker logs -f ecommerce_celery_worker
docker logs -f ecommerce_celery_email_worker
docker logs -f ecommerce_celery_batch_worker
```

### Requirement 4: Batch Processing

Before optimization: naive no-chunk task:

```bash
docker compose run --rm \
  -e LOCUST_MODE=req4_before \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 1 --spawn-rate 1 --headless --run-time 30s
```

Watch before logs:

```bash
docker logs -f ecommerce_celery_worker
```

Look for:

```text
[BATCH-NAIVE] Loaded ALL X order objects into memory at once
```

After optimization: chunked batch task:

```bash
docker compose run --rm \
  -e LOCUST_MODE=req4_after \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 1 --spawn-rate 1 --headless --run-time 30s
```

Watch after logs:

```bash
docker logs -f ecommerce_celery_batch_worker
```

Look for:

```text
[BATCH] Chunk 1/N processed
[BATCH] Daily sales ... COMPLETE
```

### Requirement 5: Load Balancing

Clear Nginx access log:

```bash
docker exec ecommerce_nginx sh -c '> /var/log/nginx/access.log'
```

Run normal workload:

```bash
docker compose run --rm \
  -e LOCUST_MODE=normal \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 50 --spawn-rate 10 --headless --run-time 1m
```

Map app container IPs:

```bash
docker inspect -f '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ecommerce_app1 ecommerce_app2 ecommerce_app3
```

Show Nginx upstream logs:

```bash
docker logs ecommerce_nginx --tail=100
```

Look for different upstreams:

```text
upstream=<app1-ip>:8000
upstream=<app2-ip>:8000
upstream=<app3-ip>:8000
```

### Requirement 6: Distributed Caching

Run browsing/cache-heavy workload:

```bash
docker compose run --rm \
  -e LOCUST_MODE=browsing \
  -e ADMIN_TOKEN="<admin-token>" \
  locust -f locustfile.py --host http://nginx:80 \
  --users 50 --spawn-rate 10 --headless --run-time 1m
```

Check Redis is alive:

```bash
docker exec ecommerce_redis redis-cli ping
```

Optional: inspect Redis keys:

```bash
docker exec ecommerce_redis redis-cli keys '*'
```

## API Endpoints

Common endpoint groups:

```text
/api/users/
/api/products/
/api/cart/
/api/orders/
/api/core/
```

Useful demo/admin endpoints:

```text
POST /api/core/capacity-stress/
POST /api/core/trigger-batch/
POST /api/core/trigger-batch-naive/
```

Login example:

```bash
curl -X POST http://localhost/api/users/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.com","password":"admin123"}'
```

Use the returned token for protected endpoints:

```bash
curl -X POST http://localhost/api/core/capacity-stress/ \
  -H "Authorization: Token <token>"
```

## Troubleshooting

### Port 8089 is already allocated

An old Locust container may still be running.

```bash
docker ps -a --filter network=high-performance-ecommerce-backend_ecommerce-network
docker stop <container-name-or-id>
docker rm <container-name-or-id>
```

Or use another host port:

```bash
docker compose run --rm -p 8090:8089 locust -f locustfile.py --host http://nginx:80
```

### Docker network is still in use

Find containers still attached:

```bash
docker ps -a --filter network=high-performance-ecommerce-backend_ecommerce-network
```

Stop and remove the leftover container, then:

```bash
docker compose down
```

### Protected endpoint returns 401

The token is missing or invalid. Login and pass:

```text
Authorization: Token <token>
```

### Login returns 400

Check that the seeded user exists and that the request body has the correct email/password.

### PgBouncer or database is not ready

Check health and logs:

```bash
docker ps
docker logs ecommerce_pgbouncer
docker logs ecommerce_db
```

### Reset everything

This removes database and Redis volumes:

```bash
docker compose down -v
docker compose up --build -d
docker compose run --rm app1 python manage.py seed_ecommerce --clean
```

## Report Evidence Checklist

For a university/report submission, capture:

- `docker-compose.yml`: app1/app2/app3, PgBouncer, Celery workers, Nginx.
- `nginx/nginx.conf`: Least Connections upstream config and upstream logging.
- Locust results for normal, req2, req4_before, and req4_after modes.
- Nginx logs showing requests distributed to all three upstream app containers.
- Celery logs showing dedicated queues and batch chunk processing.
- PostgreSQL query output showing stock never becomes negative after safe checkout.
- PgBouncer/DB connection evidence showing controlled database connections.

## License

See [License.md](License.md).
