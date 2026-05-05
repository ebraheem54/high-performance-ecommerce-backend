import random
from locust import HttpUser, task, between


class EcommerceUser(HttpUser):
    wait_time = between(1, 2)

    def on_start(self):
        # منتجات تجريبية (يفضل لاحقًا تجيبها من API)
        self.products = list(range(1, 201))
        self.token = None

        # تسجيل الدخول
        response = self.client.post(
            "/api/users/login/",
            json={
                "email": "ee@example.com",
                "password": "asdasdsdasd1221"
            }
        )

        print("LOGIN STATUS:", response.status_code)

        if response.status_code != 200:
            return

        try:
            data = response.json()
            self.token = data.get("token")
        except Exception as e:
            print("LOGIN JSON ERROR:", e)
            self.token = None

    @task(3)
    def create_order(self):
        if not self.token:
            return

        headers = {
            "Authorization": f"Token {self.token}"
        }

        # simulate cart (ليس منتج واحد فقط)
        cart_items = [
            {
                "product_id": random.choice(self.products),
                "quantity": random.randint(1, 3)
            }
            for _ in range(random.randint(1, 5))
        ]

        response = self.client.post(
            "/api/orders/checkout/",
            json={
                "items": cart_items
            },
            headers=headers
        )

        if response.status_code >= 400:
            print("ORDER FAILED:", response.status_code, response.text)

    @task(1)
    def view_orders(self):
        if not self.token:
            return

        headers = {
            "Authorization": f"Token {self.token}"
        }

        self.client.get("/api/orders/", headers=headers)
