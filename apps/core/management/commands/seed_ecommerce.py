"""
Professional E-Commerce Database Seeder
========================================
Designed for load testing all 6 non-functional requirements:

  Req 1 — Race Condition: 5 "hot" products with LOW stock (10 units each)
           → multiple users will fight over the last items
  Req 2 — Resource Management: 300 normal products, 500-2000 stock
  Req 3 — Async Queues: orders ready for invoice/email generation
  Req 4 — Batch Processing: 200+ CONFIRMED historical orders for daily report
  Req 5 — Load Distribution: 100 unique Locust users (each with own token)
  Req 6 — Distributed Caching: product catalog pre-populated for cache testing

Usage:
  python manage.py seed_ecommerce          # add data
  python manage.py seed_ecommerce --clean  # wipe & reseed
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from faker import Faker
from datetime import timedelta
import random

from apps.products.models import Product, InventoryLog
from apps.orders.models import Order, OrderItem, Payment
from apps.cart.models import CartItem

User = get_user_model()
fake = Faker()

# ── Constants ──────────────────────────────────────────────────────────────────
LOCUST_USER_COUNT  = 100   # 100 individual users, each with own token (Req 5)
REGULAR_USER_COUNT = 50    # Extra background users for realistic data
NORMAL_PRODUCT_COUNT = 300 # High-stock products for normal load (Req 2)
HOT_PRODUCT_COUNT    = 5   # LOW-stock products to trigger race conditions (Req 1)
HOT_STOCK            = 10  # Intentionally scarce → guarantees race condition
HISTORICAL_ORDER_COUNT = 200  # For Batch Processing daily report (Req 4)

LOCUST_PASSWORD = "LocustPass123!"
LOCUST_EMAIL_TEMPLATE = "locust_{i}@test.com"

PRODUCT_CATEGORIES = {
    "Electronics": [
        "iPhone 15 Pro", "Samsung Galaxy S24", "MacBook Pro M3", "Dell XPS 15",
        "Sony WH-1000XM5", "LG OLED TV", "iPad Pro", "Surface Pro 9",
        "Pixel 8 Pro", "AirPods Pro", "Canon EOS R5", "DJI Mini 4 Pro",
    ],
    "Gaming": [
        "PlayStation 5", "Xbox Series X", "Nintendo Switch OLED",
        "Razer DeathAdder", "Corsair K95 Keyboard", "BenQ Monitor 4K",
        "Logitech G Pro Mouse", "SteelSeries Arctis 7",
    ],
    "Fashion": [
        "Nike Air Max 270", "Adidas Ultraboost 23", "Levi's 501 Jeans",
        "North Face Jacket", "Puma Hoodie", "Converse Chuck Taylor",
        "Ray-Ban Aviator", "Casio G-Shock Watch",
    ],
    "Home & Kitchen": [
        "Dyson V15 Vacuum", "Instant Pot Duo", "Nespresso Vertuo",
        "KitchenAid Mixer", "Philips Air Fryer", "Braun Coffee Maker",
    ],
    "Accessories": [
        "Anker Power Bank", "USB-C Cable 3m", "HDMI Cable 4K",
        "Screen Protector", "Laptop Stand", "Webcam 4K",
        "USB Hub 7-Port", "Wireless Charger 15W",
    ],
}

HOT_PRODUCT_NAMES = [
    "Limited Edition PlayStation 5 Bundle",
    "Last-Chance iPhone 15 Pro 1TB",
    "Exclusive MacBook Pro M3 Max",
    "Flash Sale Sony Camera Kit",
    "Rare Nintendo Switch Bundle",
]


class Command(BaseCommand):
    help = "Seed professional e-commerce data for load testing all 6 requirements"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Wipe all existing data before seeding",
        )

    def handle(self, *args, **kwargs):
        clean = kwargs.get("clean", False)

        if clean:
            self._clean_database()

        self.stdout.write(self.style.SUCCESS("\n" + "═" * 65))
        self.stdout.write(self.style.SUCCESS("  E-COMMERCE SEEDER — Professional Load Test Setup"))
        self.stdout.write(self.style.SUCCESS("═" * 65))

        locust_users  = self._create_locust_users()
        regular_users = self._create_regular_users()
        all_users     = locust_users + regular_users

        hot_products    = self._create_hot_products()
        normal_products = self._create_normal_products()
        all_products    = hot_products + normal_products

        self._create_carts(regular_users, all_products)
        self._create_historical_orders(regular_users, all_products)
        self._create_locust_carts(locust_users, normal_products)

        self._print_summary(locust_users, regular_users, hot_products, normal_products)

    # ── Cleanup ────────────────────────────────────────────────────────────────
    def _clean_database(self):
        self.stdout.write(self.style.WARNING("\n[CLEAN] Removing existing data..."))
        CartItem.objects.all().delete()
        OrderItem.objects.all().delete()
        Payment.objects.all().delete()
        Order.objects.all().delete()
        InventoryLog.objects.all().delete()
        Product.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        self.stdout.write(self.style.SUCCESS("[CLEAN] ✓ Done\n"))

    # ── Users ──────────────────────────────────────────────────────────────────
    def _create_locust_users(self):
        """
        Create 100 individual Locust users, each with a unique email.
        Each will obtain its own auth token during the load test.
        This simulates 100 REAL different users (Requirement 5 — Load Distribution).
        """
        self.stdout.write("[1/6] Creating Locust users (100 unique accounts)...")
        users = []

        for i in range(1, LOCUST_USER_COUNT + 1):
            email = LOCUST_EMAIL_TEMPLATE.format(i=i)
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "name": f"Locust User {i}",
                    "is_active": True,
                },
            )
            if created:
                user.set_password(LOCUST_PASSWORD)
                user.wallet_balance = 500.00
                user.save()
            else:
                if user.wallet_balance < 100:
                    user.wallet_balance = 500.00
                    user.save(update_fields=["wallet_balance"])
            users.append(user)

        self.stdout.write(self.style.SUCCESS(
            f"  ✓ {len(users)} Locust users ready  "
            f"(locust_1@test.com … locust_{LOCUST_USER_COUNT}@test.com)"
        ))
        return users

    def _create_regular_users(self):
        """Background users for realistic historical data."""
        self.stdout.write("[2/6] Creating regular background users...")
        users = []
        for _ in range(REGULAR_USER_COUNT):
            user = User.objects.create_user(
                email=fake.unique.email(),
                password="Pass1234!",
                name=fake.name(),
            )
            users.append(user)
        self.stdout.write(self.style.SUCCESS(f"  ✓ {len(users)} background users created"))
        return users

    # ── Products ───────────────────────────────────────────────────────────────
    def _create_hot_products(self):
        """
        Requirement 1 — Race Condition Proof:
        5 products with stock=10. When 100 concurrent users all try to buy one,
        only 10 will succeed. The rest get 'Insufficient stock' — proving that
        pessimistic locking prevented overselling.
        """
        self.stdout.write("[3/6] Creating HOT products (low stock — Race Condition test)...")
        products = []

        for i, name in enumerate(HOT_PRODUCT_NAMES):
            product = Product.objects.create(
                name=name,
                description=(
                    f"[RACE CONDITION TEST PRODUCT #{i+1}] "
                    f"Limited stock of {HOT_STOCK} units. "
                    f"Used to prove pessimistic locking prevents overselling "
                    f"when {LOCUST_USER_COUNT} concurrent users attempt checkout."
                ),
                price=round(random.uniform(500, 3000), 2),
                stock=HOT_STOCK,
                is_active=True,
            )
            products.append(product)

        self.stdout.write(self.style.SUCCESS(
            f"  ✓ {len(products)} HOT products  ←  stock={HOT_STOCK} each  "
            f"(TOTAL: {len(products) * HOT_STOCK} units for {LOCUST_USER_COUNT} users)"
        ))
        self.stdout.write(self.style.WARNING(
            f"  ⚡ Only {len(products) * HOT_STOCK} users can buy these — "
            f"{LOCUST_USER_COUNT - len(products) * HOT_STOCK} will get 'Insufficient stock'"
        ))
        return products

    def _create_normal_products(self):
        """
        High-stock products so checkout tests don't exhaust stock prematurely.
        Used for Requirement 2 (resource management) and Requirement 6 (caching).
        """
        self.stdout.write("[4/6] Creating normal products (high stock)...")
        products = []

        all_names = [
            f"{name} — {variant}"
            for category, names in PRODUCT_CATEGORIES.items()
            for name in names
            for variant in [category]
        ]

        for i in range(NORMAL_PRODUCT_COUNT):
            base_name = all_names[i % len(all_names)]
            product = Product.objects.create(
                name=f"{base_name} v{i + 1}",
                description=fake.text(max_nb_chars=200),
                price=round(random.uniform(10, 2000), 2),
                stock=random.randint(500, 2000),
                is_active=True,
            )
            products.append(product)

        total_stock = sum(p.stock for p in products)
        self.stdout.write(self.style.SUCCESS(
            f"  ✓ {len(products)} products | Total stock: {total_stock:,} units"
        ))
        return products

    # ── Carts ──────────────────────────────────────────────────────────────────
    def _create_carts(self, users, products):
        """Pre-fill carts for background users to create realistic DB state."""
        self.stdout.write("[5/6] Creating carts for background users...")
        count = 0
        for user in users:
            picks = random.sample(products, k=random.randint(1, 4))
            CartItem.objects.bulk_create([
                CartItem(user=user, product=p, quantity=random.randint(1, 3))
                for p in picks
            ])
            count += len(picks)
        self.stdout.write(self.style.SUCCESS(f"  ✓ {count} cart items created"))

    def _create_locust_carts(self, locust_users, products):
        """
        Each Locust user starts with an empty cart.
        The test itself will add products → checkout → repeat.
        """
        pass  # Locust manages its own cart during the test

    # ── Historical Orders ──────────────────────────────────────────────────────
    def _create_historical_orders(self, users, products):
        """
        Requirement 4 — Batch Processing:
        Create 200+ CONFIRMED/DELIVERED orders from the past 7 days.
        The daily batch task (run_daily_sales_batch_task) will process these
        in chunks of 50 when triggered, demonstrating batch processing.
        """
        self.stdout.write("[6/6] Creating historical orders for Batch Processing test...")
        order_count = 0

        completed_statuses = [
            Order.Status.CONFIRMED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        ]

        for i in range(HISTORICAL_ORDER_COUNT):
            user = random.choice(users)

            # Spread orders over the past 7 days (for date-range testing)
            days_ago = random.randint(0, 6)
            created_time = timezone.now() - timedelta(
                days=days_ago,
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
            )

            order = Order(
                user=user,
                status=random.choice(completed_statuses),
                total_price=0,
            )
            order.save()

            # Manually set created_at (auto_now_add bypasses normal assignment)
            Order.objects.filter(pk=order.pk).update(created_at=created_time)

            total = 0
            picks = random.sample(products, k=random.randint(1, 4))

            items = []
            for product in picks:
                qty = random.randint(1, 3)
                unit_price = product.price
                items.append(OrderItem(
                    order=order,
                    product=product,
                    quantity=qty,
                    unit_price=unit_price,
                ))
                total += qty * unit_price

                InventoryLog.objects.create(
                    product=product,
                    quantity_change=-qty,
                    reason=InventoryLog.Reason.PURCHASE,
                    note=f"Historical seeder — order #{order.id}",
                )

            OrderItem.objects.bulk_create(items)
            order.total_price = total
            order.save(update_fields=["total_price"])

            Payment.objects.create(
                order=order,
                amount=total,
                status=Payment.Status.COMPLETED,
                method=random.choice(Payment.Method.values),
                transaction_id=fake.uuid4(),
            )

            order_count += 1

        total_revenue = sum(
            Order.objects.filter(
                status__in=completed_statuses
            ).values_list("total_price", flat=True)
        )

        self.stdout.write(self.style.SUCCESS(
            f"  ✓ {order_count} historical orders created | "
            f"Total revenue: {float(sum(Order.objects.values_list('total_price', flat=True))):,.2f}"
        ))
        self.stdout.write(self.style.WARNING(
            f"  → These will be processed by run_daily_sales_batch_task in chunks of 50"
        ))

    # ── Summary ────────────────────────────────────────────────────────────────
    def _print_summary(self, locust_users, regular_users, hot_products, normal_products):
        all_products   = hot_products + normal_products
        total_stock    = sum(p.stock for p in all_products)
        order_count    = Order.objects.count()

        self.stdout.write("\n" + self.style.SUCCESS("═" * 65))
        self.stdout.write(self.style.SUCCESS("  DATABASE SEEDED SUCCESSFULLY"))
        self.stdout.write(self.style.SUCCESS("═" * 65))
        self.stdout.write(f"  Locust Users:        {len(locust_users):>6}  (unique tokens)")
        self.stdout.write(f"  Background Users:    {len(regular_users):>6}")
        self.stdout.write(f"  HOT Products:        {len(hot_products):>6}  (stock={HOT_STOCK} each ← Race Condition)")
        self.stdout.write(f"  Normal Products:     {len(normal_products):>6}  (stock 500-2000)")
        self.stdout.write(f"  Total Stock:         {total_stock:>6,} units")
        self.stdout.write(f"  Historical Orders:   {order_count:>6}  (for Batch Processing)")
        self.stdout.write(self.style.SUCCESS("═" * 65))
        self.stdout.write(self.style.WARNING("\n  Locust Credentials (100 users):"))
        self.stdout.write(f"    Email:    locust_1@test.com … locust_{LOCUST_USER_COUNT}@test.com")
        self.stdout.write(f"    Password: {LOCUST_PASSWORD}")
        self.stdout.write(self.style.WARNING("\n  HOT Product IDs (Race Condition):"))
        for p in hot_products:
            self.stdout.write(f"    ID={p.id}  stock={p.stock}  — {p.name}")
        self.stdout.write(self.style.SUCCESS("\n  Ready for load testing! Run:"))
        self.stdout.write("    locust -f locustfile.py --host http://localhost:8000\n")
