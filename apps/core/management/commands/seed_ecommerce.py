"""
Improved seeding strategy for load testing.

Changes:
1. Higher stock (500-2000 per product) to prevent exhaustion during tests
2. Create dedicated test user (ee@example.com) for Locust
3. More products (300) for better distribution
4. Clean slate: delete existing data first
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from faker import Faker
import random

from apps.products.models import Product, InventoryLog
from apps.orders.models import Order, OrderItem, Payment
from apps.cart.models import CartItem

User = get_user_model()
fake = Faker()


class Command(BaseCommand):
    help = "Seed full ecommerce database with sufficient stock for load testing"

    def add_arguments(self, parser):
        parser.add_argument(
            '--clean',
            action='store_true',
            help='Delete all existing data before seeding',
        )

    def handle(self, *args, **kwargs):
        clean = kwargs.get('clean', False)

        if clean:
            self.stdout.write(self.style.WARNING("Cleaning existing data..."))
            CartItem.objects.all().delete()
            OrderItem.objects.all().delete()
            Payment.objects.all().delete()
            Order.objects.all().delete()
            InventoryLog.objects.all().delete()
            Product.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.SUCCESS("✓ Cleanup complete"))

        self.stdout.write("Seeding started...")

        # ═══════════════════════════════════════════════════════════════
        # USERS
        # ═══════════════════════════════════════════════════════════════
        self.stdout.write("Creating users...")
        users = []

        # Create dedicated test user for Locust
        test_user, created = User.objects.get_or_create(
            email="ee@example.com",
            defaults={
                "name": "Test User (Locust)",
                "is_staff": False,
                "is_active": True,
            }
        )
        if created:
            test_user.set_password("asdasdsdasd1221")
            test_user.save()
            self.stdout.write(self.style.SUCCESS(f"✓ Test user created: {test_user.email}"))
        else:
            self.stdout.write(f"✓ Test user exists: {test_user.email}")

        # Create 49 additional regular users
        for _ in range(49):
            user = User.objects.create_user(
                email=fake.unique.email(),
                password="12345678",
                name=fake.name(),
            )
            users.append(user)

        users.append(test_user)
        self.stdout.write(self.style.SUCCESS(f"✓ Total users: {len(users)}"))

        # ═══════════════════════════════════════════════════════════════
        # PRODUCTS — HIGH STOCK for load testing
        # ═══════════════════════════════════════════════════════════════
        self.stdout.write("Creating products with high stock...")
        products = []

        product_names = [
            "iPhone", "Samsung Galaxy", "MacBook", "Dell Laptop",
            "Nike Shoes", "Adidas Hoodie", "Sony Headphones",
            "LG TV", "Xbox", "PlayStation", "Canon Camera",
            "Keyboard", "Mouse", "Monitor", "Tablet",
            "Smartwatch", "Charger", "Power Bank", "USB Cable",
        ]

        # Create 300 products with stock 500-2000 each
        # Total capacity: ~375,000 units (enough for heavy testing)
        for i in range(300):
            product = Product.objects.create(
                name=f"{random.choice(product_names)} {fake.word().title()} {i}",
                description=fake.text(max_nb_chars=200),
                price=round(random.uniform(10, 3000), 2),
                stock=random.randint(500, 2000),  # ← HIGH stock
                is_active=True,
            )
            products.append(product)

        total_stock = sum(p.stock for p in products)
        self.stdout.write(self.style.SUCCESS(
            f"✓ Created {len(products)} products with total stock: {total_stock:,} units"
        ))

        # ═══════════════════════════════════════════════════════════════
        # CART — Only for non-test users
        # ═══════════════════════════════════════════════════════════════
        self.stdout.write("Creating cart items...")
        cart_count = 0
        for user in users:
            # Skip test user (Locust will manage its own cart)
            if user.email == "ee@example.com":
                continue

            selected_products = random.sample(products, k=random.randint(1, 5))

            CartItem.objects.bulk_create([
                CartItem(
                    user=user,
                    product=product,
                    quantity=random.randint(1, 2),
                )
                for product in selected_products
            ])
            cart_count += len(selected_products)

        self.stdout.write(self.style.SUCCESS(f"✓ Created {cart_count} cart items"))

        # ═══════════════════════════════════════════════════════════════
        # ORDERS — Historical data (won't affect test stock)
        # ═══════════════════════════════════════════════════════════════
        self.stdout.write("Creating historical orders...")
        order_count = 0

        for user in users:
            # Skip test user
            if user.email == "ee@example.com":
                continue

            for _ in range(random.randint(1, 3)):
                order = Order.objects.create(
                    user=user,
                    status=random.choice(list(Order.Status.values)),
                )

                total = 0

                for _ in range(random.randint(1, 3)):
                    product = random.choice(products)

                    item = OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=random.randint(1, 2),
                        unit_price=product.price,
                    )

                    total += item.subtotal

                    # Inventory log (historical — doesn't reduce current stock)
                    InventoryLog.objects.create(
                        product=product,
                        quantity_change=-item.quantity,
                        reason=InventoryLog.Reason.PURCHASE,
                        note="Historical seeder order",
                    )

                order.total_price = total
                order.save()

                # Payment
                Payment.objects.create(
                    order=order,
                    amount=total,
                    status=random.choice(Payment.Status.values),
                    method=random.choice(Payment.Method.values),
                    transaction_id=fake.uuid4(),
                )

                order_count += 1

        self.stdout.write(self.style.SUCCESS(f"✓ Created {order_count} historical orders"))

        # ═══════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("═" * 60))
        self.stdout.write(self.style.SUCCESS("DATABASE SEEDED SUCCESSFULLY!"))
        self.stdout.write(self.style.SUCCESS("═" * 60))
        self.stdout.write(f"Users:            {len(users)}")
        self.stdout.write(f"Products:         {len(products)}")
        self.stdout.write(f"Total Stock:      {total_stock:,} units")
        self.stdout.write(f"Cart Items:       {cart_count}")
        self.stdout.write(f"Historical Orders: {order_count}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Test credentials:"))
        self.stdout.write(f"  Email:    ee@example.com")
        self.stdout.write(f"  Password: asdasdsdasd1221")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Ready for load testing! 🚀"))
