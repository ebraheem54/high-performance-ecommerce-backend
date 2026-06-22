# High-Performance E-Commerce Backend

A production-style e-commerce backend built with Django, Django REST Framework, PostgreSQL, Redis, Celery, Nginx, PgBouncer, Docker, JMeter, Prometheus, and Grafana.

This project is designed to demonstrate how a backend system can handle real performance and reliability problems under concurrent traffic: safe checkout, race-condition prevention, database connection control, asynchronous queues, chunk-based batch processing, distributed caching, distributed cache locks, transaction integrity, stress testing, bottleneck analysis, structured request logging, and load balancing across multiple application containers.

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
- [JMeter Stress Testing](#jmeter-stress-testing)
- [Monitoring With Prometheus and Grafana](#monitoring-with-prometheus-and-grafana)
- [Structured Runtime Logs](#structured-runtime-logs)
- [Requirement Evidence Commands](#requirement-evidence-commands)
- [API Endpoints](#api-endpoints)
- [Troubleshooting](#troubleshooting)

## Project Overview

The backend exposes REST APIs for users, products, carts, orders, checkout, administrative demo endpoints, and background processing. It is deployed as multiple Django/Gunicorn containers behind Nginx, with Redis for caching and task brokering, Celery for background jobs, PostgreSQL for persistent storage, and PgBouncer for database connection pooling.

The system is intentionally built to support load testing and university/report evidence. A new developer can run the full stack with Docker Compose, seed realistic e-commerce data, run focused Locust demos for earlier requirements, run the full-system JMeter stress test, and capture proof for each performance requirement.

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
| Load testing | Apache JMeter | Full-system stress testing for Requirement 9 |
| Monitoring | Prometheus + Grafana | Requirement 10 dashboards and bottleneck analysis |
| Exporters | cAdvisor, node-exporter, postgres-exporter, redis-exporter | Container, host, PostgreSQL, and Redis metrics |
| Inspection tools | pgAdmin | Optional database inspection |

## Architecture

```text
Client / JMeter / Postman / Browser
      |
      v
Nginx load balancer
      |
      +--> app1: Django + Gunicorn
      +--> app2: Django + Gunicorn
      +--> app3: Django + Gunicorn
      +--> app4: Django + Gunicorn
      +--> app5: Django + Gunicorn
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

The project runs five application containers: `ecommerce_app1`, `ecommerce_app2`, `ecommerce_app3`, `ecommerce_app4`, and `ecommerce_app5`. Each container runs the same Django application using Gunicorn:

```text
worker-class = gthread
workers      = 8
threads      = 3
```

This gives controlled parallel request handling while avoiding unbounded process creation.

### Load Balancing

Nginx is the public entry point on port `80`. It forwards traffic to `app1`, `app2`, `app3`, `app4`, and `app5` using the Least Connections algorithm. This is better than simple round-robin for mixed workloads because product reads, cart operations, login requests, and checkout requests do not all take the same amount of time.

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
- Product catalog listing, detail, top-selling, reviews, and rating summary endpoints.
- Redis-backed product caching using a Cache-Aside pattern.
- Redis distributed locking to prevent cache stampede on expensive cache rebuilds.
- Cart add/view/clear operations.
- Safe checkout and order creation.
- Stock race-condition protection using transactions and row-level locking.
- Optimistic stock helper functions for retry-based inventory updates.
- Asynchronous order and email processing with Celery.
- Dedicated Celery queues for general, email, and batch jobs.
- Scheduled daily sales aggregation with Celery Beat.
- Chunk-based batch processing for large order datasets.
- Nginx load balancing across five Django/Gunicorn containers.
- PgBouncer transaction pooling for PostgreSQL connection control.
- JMeter stress test plan for a complete purchase flow.
- Prometheus and Grafana monitoring stack for resource and bottleneck analysis.
- Structured logs grouped by subsystem under `logs/`.

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
- focused load tests that report response time and observed DB connections.

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

Redis is used as a distributed cache for read-heavy product endpoints. The project follows a Cache-Aside pattern:

1. The API checks Redis using a deterministic cache key.
2. If the data exists, the response is served from Redis.
3. If the data is missing, Django reads from PostgreSQL, serializes the response, stores it in Redis with a TTL, and returns it.

Cached endpoints include:

- `GET /api/products/`
- `GET /api/products/{id}/`
- `GET /api/products/top-selling/`
- `GET /api/products/{id}/rating-summary/`

Important design decision: public cache payloads do not store `stock`. Stock is sensitive and changes during reservations and checkout, so it is always read and modified inside database transactions.

Important code:

- `apps/products/cache_utils.py`
- `apps/products/views.py`
- `apps/products/serializers.py`

### Requirement 7: Concurrency Control

The project uses both database locking and Redis locking depending on the problem being solved.

Database-level locking is used for stock-sensitive operations:

- `POST /api/products/{id}/reserve/`
- `POST /api/orders/checkout/`
- `POST /api/orders/{id}/process-payment/`
- admin stock updates

These flows use `transaction.atomic()` and `select_for_update()` to serialize access to sensitive rows and prevent race conditions, double payment, and overselling.

Redis distributed locks are used for cache stampede protection. When many requests hit an expired expensive cache key at the same time, only one request rebuilds the cache while the others wait briefly and then read from Redis.

Important code:

- `apps/products/cache_utils.py`
- `apps/products/services.py`
- `apps/orders/services.py`

### Requirement 8: Transaction Integrity / ACID

Checkout and payment flows are protected with ACID transactions. The most important transaction is the checkout flow:

1. Read the user's cart.
2. Lock product rows in a consistent order.
3. Validate stock while the rows are locked.
4. Deduct stock.
5. Create the order.
6. Create order items.
7. Create the payment record.
8. Clear the cart.

If any step fails, the entire transaction is rolled back. This ensures that the system never creates a partial order, never deducts stock without an order, and never oversells stock under concurrent access.

Important code:

- `apps/orders/services.py`
- `apps/orders/views.py`
- `apps/products/services.py`
- `apps/cart/services.py`

### Requirement 9: Stress Testing

Requirement 9 is tested with Apache JMeter, not Locust. The JMeter plan runs a complete purchase flow with at least 100 concurrent users in the optimized AFTER state of the system.

JMeter test plan:

- `req9_jmeter/req9_full_system_stress_test.jmx`

The tested flow is:

```text
1.  GET    /api/users/me/
2.  GET    /api/products/
3.  GET    /api/products/{id}/
4.  GET    /api/products/{id}/reviews/
5.  GET    /api/products/{id}/rating-summary/
6.  DELETE /api/cart/clear/
7.  POST   /api/cart/add/
8.  GET    /api/cart/
9.  PATCH  /api/cart/{product_id}/quantity/
10. POST   /api/products/{id}/reserve/
11. POST   /api/orders/checkout/
12. GET    /api/orders/{id}/
13. POST   /api/orders/{id}/process-payment/
14. GET    /api/orders/
15. POST   /api/products/{id}/reviews/
16. GET    /api/products/{id}/rating-summary/
```

The goal is to prove that 100 users can complete a realistic purchase flow without system collapse, data corruption, negative stock, or unhandled server errors.

### Requirement 10: Benchmarking and Bottleneck Analysis

Requirement 10 is supported by:

- JMeter response-time reports.
- Prometheus metrics.
- Grafana dashboards.
- Structured request logs.

The monitoring stack is isolated from the main application stack:

- `req10_monitoring/docker-compose.monitoring.yml`
- `req10_monitoring/prometheus/prometheus.yml`
- `req10_monitoring/grafana/dashboards/req9-overview.json`

The primary bottleneck observed during the complete purchase flow is expected around:

```text
POST /api/orders/checkout/
```

This endpoint performs a database transaction and locks product rows to protect stock. When many users buy the same product concurrently, PostgreSQL intentionally serializes access to that product row. This increases p95/p99 latency but protects data integrity and prevents overselling.

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

### 2. Download Docker images

Application stack:

```bash
docker compose pull
```

Monitoring stack:

```bash
docker compose -f req10_monitoring/docker-compose.monitoring.yml pull
```

The main containers/images used by the project are:

```text
PostgreSQL
PgBouncer
Redis
Django/Gunicorn app containers
Celery workers
Celery Beat
Nginx
pgAdmin
Prometheus
Grafana
cAdvisor
node-exporter
postgres-exporter
redis-exporter
```

Python dependencies are installed from:

```text
requirements.txt
requirements.dev.txt
```

The monitoring stack is installed through Docker images, so no Python monitoring library is required inside Django for the current Req10 setup.

### 3. Start the full stack

```bash
docker compose up --build -d
```

This starts:

- PostgreSQL
- PgBouncer
- Redis
- migrations
- app1, app2, app3, app4, app5
- Celery workers
- Celery Beat
- Nginx
- pgAdmin
- optional Locust service for older focused tests

### 4. Check containers

```bash
docker ps
```

Expected important containers:

```text
ecommerce_nginx
ecommerce_app1
ecommerce_app2
ecommerce_app3
ecommerce_app4
ecommerce_app5
ecommerce_db
ecommerce_pgbouncer
ecommerce_redis
ecommerce_celery_worker
ecommerce_celery_email_worker
ecommerce_celery_batch_worker
ecommerce_celery_beat
```

### 5. Run migrations manually if needed

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

- 100 load-test users (`locust_1@test.com` through `locust_100@test.com`).
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
| pgAdmin | `http://localhost:5050` |
| Nginx health | `http://localhost/nginx-health` |
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3000` |

pgAdmin default credentials from `docker-compose.yml`:

```text
Email: admin@admin.com
Password: admin
```

Grafana default credentials:

```text
Username: admin
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
docker logs -f ecommerce_app4
docker logs -f ecommerce_app5
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
docker stats ecommerce_app1 ecommerce_app2 ecommerce_app3 ecommerce_app4 ecommerce_app5 ecommerce_pgbouncer ecommerce_db ecommerce_nginx ecommerce_redis
```

## JMeter Stress Testing

Requirement 9 uses Apache JMeter.

Test plan:

```text
req9_jmeter/req9_full_system_stress_test.jmx
```

The plan uses token-based authentication from:

```text
req9_jmeter/users.csv
```

After running `seed_ecommerce --clean`, regenerate the token CSV because user IDs and tokens may change:

```bash
docker compose exec -T app1 python manage.py shell -c "from django.contrib.auth import get_user_model; from rest_framework.authtoken.models import Token; U=get_user_model(); print('email,token'); [print(f'{u.email},{Token.objects.get_or_create(user=u)[0].key}') for u in U.objects.filter(email__startswith='locust_').order_by('id')[:100]]" > req9_jmeter/users.csv
```

Pick a high-stock product for the stress product:

```bash
docker compose exec -T app1 python manage.py shell -c "from apps.products.models import Product; print('\n'.join(f'{p.id} | {p.name} | stock={p.stock}' for p in Product.objects.filter(is_active=True, stock__gt=100).order_by('-stock')[:10]))"
```

Set the selected ID in JMeter as:

```text
stress_product_id=<product-id>
```

### Run From JMeter GUI

1. Open Apache JMeter.
2. Open `req9_jmeter/req9_full_system_stress_test.jmx`.
3. Set:

```text
host=localhost
port=80
protocol=http
tx_threads=100
stress_product_id=<high-stock-product-id>
```

4. Run the test and save Aggregate/Summary results under `req9_jmeter/results/`.

### Run From CLI

```bash
mkdir -p req9_jmeter/results

jmeter -n \
  -t req9_jmeter/req9_full_system_stress_test.jmx \
  -l req9_jmeter/results/req9-results.jtl \
  -e -o req9_jmeter/results/html-report \
  -Jhost=localhost \
  -Jport=80 \
  -Jprotocol=http \
  -Jtx_threads=100 \
  -Jstress_product_id=<high-stock-product-id>
```

Do not commit generated result files such as `aggregate.csv`, `summary.csv`, `.jtl`, or `html-report/`. They are local evidence files and can be screenshotted for the report.

## Monitoring With Prometheus and Grafana

Requirement 10 uses a separate monitoring compose file. It is intentionally isolated from the main application stack so monitoring can be started, stopped, or removed without changing the application containers.

Download monitoring images:

```bash
docker compose -f req10_monitoring/docker-compose.monitoring.yml pull
```

Start monitoring:

```bash
docker compose -f req10_monitoring/docker-compose.monitoring.yml up -d
```

If the application network name is different, pass it explicitly:

```bash
APP_NETWORK_NAME=parallelprogrammingproject_ecommerce-network \
docker compose -f req10_monitoring/docker-compose.monitoring.yml up -d
```

Open:

```text
Prometheus: http://localhost:9090
Grafana:    http://localhost:3000
```

Check Prometheus targets:

```text
http://localhost:9090/targets
```

Grafana is provisioned with a Prometheus datasource and a starter dashboard for:

- container CPU usage
- container memory working set
- Redis command rate
- PostgreSQL connections

Stop monitoring:

```bash
docker compose -f req10_monitoring/docker-compose.monitoring.yml down
```

## Structured Runtime Logs

The project automatically creates structured log files under:

```text
logs/
```

Runtime log groups:

```text
logs/cart/
logs/products/
logs/orders/
logs/payments/
logs/user_tracking/
```

Each group may contain:

```text
info.log
warnings.log
errors.log
```

The request tracking middleware records every API request with fields such as:

```text
request_id
method
endpoint
user_id
status_code
elapsed_ms
result
```

Example:

```text
request_id=abc123 method=POST endpoint=/api/orders/checkout/ user_id=2501 event=request.completed status_code=201 elapsed_ms=850 result=completed
```

Before each new JMeter run, clear old logs:

```bash
docker compose exec app1 python manage.py clear_logs
```

Generated log files are runtime evidence and should not normally be committed to Git. The code that creates them is committed; the local `.log` outputs are not.

## Legacy Locust Tests

Older focused requirement tests still exist under:

```text
locust_tests/
```

They are useful for Req6/Req7 before-after demonstrations, but the official full-system stress test for Req9 uses JMeter.

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
docker stats ecommerce_app1 ecommerce_app2 ecommerce_app3 ecommerce_app4 ecommerce_app5 ecommerce_pgbouncer ecommerce_db ecommerce_nginx ecommerce_redis
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
docker inspect -f '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ecommerce_app1 ecommerce_app2 ecommerce_app3 ecommerce_app4 ecommerce_app5
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
upstream=<app4-ip>:8000
upstream=<app5-ip>:8000
```

### Requirement 6: Distributed Caching

Run the Req6 cache evidence tests:

```bash
docker compose run --rm \
  locust -f locust_tests/req6/locust_req6.py \
  --host http://nginx:80 \
  --users 100 --spawn-rate 15 --headless --run-time 1m
```

Check Redis is alive:

```bash
docker exec ecommerce_redis redis-cli ping
```

Optional: inspect Redis keys:

```bash
docker exec ecommerce_redis redis-cli keys '*'
```

Important cache keys include product list, product detail, top-selling products, and rating summaries. Public cached product payloads intentionally exclude `stock`.

### Requirement 7: Concurrency Control

Run the Redis distributed-lock cache stampede test:

```bash
docker compose run --rm -p 8092:8089 \
  -e LOCUST_MODE=req7_after \
  -e REQ7_DELAY_MS=0 \
  locust -f locust_tests/req7/locust_req7_distributed_lock.py \
  --host http://nginx:80 -u 100 -r 15 --run-time 30s
```

Expected evidence:

- `DB Rebuilds` should be close to `1` per protected cache key.
- `Protected` should be `YES`.
- `Fallback DB Reads` should be `0`.

For database locking evidence, inspect safe transactional paths:

```text
POST /api/products/{id}/reserve/
POST /api/orders/checkout/
POST /api/orders/{id}/process-payment/
```

These paths use pessimistic locks to protect stock, orders, and payment state.

### Requirement 8: Transaction Integrity / ACID

Run the complete purchase flow with JMeter or Postman and verify that orders are not partial:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db \
  -c "SELECT COUNT(*) AS negative_stock FROM products_product WHERE stock < 0;"
```

Expected:

```text
negative_stock = 0
```

Verify that paid/created orders have order items:

```bash
docker exec ecommerce_db psql -U ecommerce_user -d ecommerce_db \
  -c "SELECT COUNT(*) AS orders_without_items FROM orders_order o LEFT JOIN orders_orderitem i ON i.order_id = o.id WHERE i.id IS NULL;"
```

Expected:

```text
orders_without_items = 0
```

The important guarantee is that checkout either fully succeeds or fully rolls back.

### Requirement 9: Full-System Stress Test

Run the official JMeter test plan:

```bash
jmeter -n \
  -t req9_jmeter/req9_full_system_stress_test.jmx \
  -l req9_jmeter/results/req9-results.jtl \
  -e -o req9_jmeter/results/html-report \
  -Jhost=localhost \
  -Jport=80 \
  -Jprotocol=http \
  -Jtx_threads=100 \
  -Jstress_product_id=<high-stock-product-id>
```

Expected evidence:

- at least 100 simulated users
- full purchase flow executed
- no server crash
- no unhandled 500 errors
- no negative stock
- successful order and payment logs

### Requirement 10: Benchmarking and Bottleneck Analysis

Start monitoring before running JMeter:

```bash
docker compose -f req10_monitoring/docker-compose.monitoring.yml up -d
```

Open Grafana:

```text
http://localhost:3000
```

Recommended screenshots for the report:

- JMeter Aggregate Report / Summary Report
- Grafana CPU panel
- Grafana Memory panel
- Grafana Redis command rate panel
- Grafana PostgreSQL connections panel
- `logs/user_tracking/info.log`
- `logs/user_tracking/warnings.log`
- `logs/orders/info.log`
- `logs/payments/info.log`

The main bottleneck to discuss is usually `POST /api/orders/checkout/`, because it performs a transaction and locks product rows. This can increase p95/p99 latency under high concurrency on the same product, while still preserving data correctness.

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

### JMeter result file already exists

If JMeter asks whether to append or overwrite a result file, choose overwrite for a clean run, or clear `req9_jmeter/results/` before starting a new test.

Do not set a result filename to a directory path such as `/home/ebraheem`; it must be a real file path such as:

```text
req9_jmeter/results/aggregate.csv
```

### Port 8089 is already allocated

This only applies to legacy Locust runs. An old Locust container may still be running.

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


- `docker-compose.yml`: app1-app5, PgBouncer, Redis, Celery workers, Celery Beat, Nginx.
- `nginx/nginx.conf`: Least Connections upstream config and upstream logging.
- Req6/Req7 focused Locust evidence if before-after cache/cache-lock proof is required.
- `req9_jmeter/req9_full_system_stress_test.jmx`: complete 100-user purchase flow.
- JMeter Aggregate/Summary screenshots for Req9.
- Grafana dashboard screenshots for Req10.
- Structured logs under `logs/user_tracking`, `logs/cart`, `logs/orders`, `logs/payments`, and `logs/products`.
- Nginx logs showing requests distributed to all five upstream app containers.
- Celery logs showing dedicated queues and batch chunk processing.
- PostgreSQL query output showing stock never becomes negative after safe checkout.
- PgBouncer/DB connection evidence showing controlled database connections.
- Bottleneck explanation for `POST /api/orders/checkout/` under concurrent purchase pressure.

## License

See [License.md](License.md).
