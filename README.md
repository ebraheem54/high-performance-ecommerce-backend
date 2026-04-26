# High-Performance E-Commerce Backend Engine

## 🚀 Project Overview
This project is a high-performance e-commerce backend built with **Django** and **Django Rest Framework**. It is designed to handle thousands of concurrent requests by leveraging parallel programming concepts, asynchronous task queues, and distributed caching.

The project was developed for the **Parallel Programming Course (2026)** and focuses on solving common scalability issues such as race conditions, database bottlenecks, and long-running background tasks.

---

## 🛠 Tech Stack
- **Framework:** Django 5.x & Django Rest Framework
- **Database:** PostgreSQL 13 (ACID compliant)
- **Cache & Broker:** Redis 7
- **Task Queue:** Celery (for background processing)
- **Containerization:** Docker & Docker Compose
- **Stress Testing:** Locust

---

## 🏗 Key Features & Concurrency Control

### 1. Data Integrity & Locking
To prevent **Race Conditions** (e.g., two users buying the last item at the same time), the system implements:
- **Pessimistic Locking**: Uses `select_for_update()` during the checkout process to lock product rows until the transaction is complete.
- **ACID Transactions**: All order operations (stock update + order creation + logging) are wrapped in `transaction.atomic()`.

### 2. Asynchronous Processing
Non-critical operations are offloaded to **Celery Workers** to keep the API response times low:
- **Notifications**: Order confirmations are sent asynchronously.
- **Batch Processing**: Large inventory reports are processed in background chunks.

### 3. Distributed Caching
Frequently accessed data (like product lists) is cached in **Redis** to reduce the load on the PostgreSQL database.

---

## 📦 Installation & Setup

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### Quick Start
1. **Clone the repository**:
   ```bash
   git clone <your-repo-url>
   cd high-performance-ecommerce
   ```

2. **Build and start the containers**:
   ```bash
   docker-compose up --build
   ```
   This will start four services:
   - `app`: The Django web server (Port 8000)
   - `db`: The PostgreSQL database
   - `redis`: The cache and message broker
   - `worker`: The Celery background task processor

3. **Apply Migrations**:
   (The docker-compose is set to run migrations automatically, but you can run them manually if needed):
   ```bash
   docker-compose exec app python manage.py migrate
   ```

4. **Create a Superuser**:
   ```bash
   docker-compose exec app python manage.py createsuperuser
   ```

---

## 🧪 Stress Testing
The project includes a **Locust** configuration to simulate high load (100+ concurrent users).

1. **Install Locust locally** (or run via a temporary container):
   ```bash
   pip install locust
   ```
2. **Run the test**:
   ```bash
   locust -f locustfile.py --host http://localhost:8000
   ```
3. Open `http://localhost:8089` in your browser to start the simulation and monitor response times and bottlenecks.
 