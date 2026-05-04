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
    help = "Seed full ecommerce database"

    def handle(self, *args, **kwargs):
        self.stdout.write("Seeding started...")

        # ---------------- USERS ----------------
        self.stdout.write("Creating users...")
        users = []

        for _ in range(50):
            user = User.objects.create_user(
                email=fake.unique.email(),
                password="12345678",
                name=fake.name(),
            )
            users.append(user)

        # ---------------- PRODUCTS ----------------
        self.stdout.write("Creating products...")
        products = []

        product_names = [
            "iPhone", "Samsung Galaxy", "MacBook", "Dell Laptop",
            "Nike Shoes", "Adidas Hoodie", "Sony Headphones",
            "LG TV", "Xbox", "PlayStation", "Canon Camera"
        ]

        for _ in range(200):
            product = Product.objects.create(
                name=f"{random.choice(product_names)} {fake.word().title()}",
                description=fake.text(max_nb_chars=200),
                price=round(random.uniform(10, 3000), 2),
                stock=random.randint(0, 100),
                is_active=True,
            )
            products.append(product)

        # ---------------- CART ----------------
        self.stdout.write("Creating cart items...")
        for user in users:
          selected_products = random.sample(products, k=random.randint(1, 8))

          CartItem.objects.bulk_create([
              CartItem(
                  user=user,
                  product=product,
                  quantity=random.randint(1, 3),
              )
              for product in selected_products
          ])

        # ---------------- ORDERS ----------------
        self.stdout.write("Creating orders...")

        for user in users:
            for _ in range(random.randint(1, 4)):

                order = Order.objects.create(
                    user=user,
                    status=random.choice(list(Order.Status.values)),
                )

                total = 0

                for _ in range(random.randint(1, 4)):
                    product = random.choice(products)

                    item = OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=random.randint(1, 3),
                        unit_price=product.price,
                    )

                    total += item.subtotal

                    # Inventory log
                    InventoryLog.objects.create(
                        product=product,
                        quantity_change=-item.quantity,
                        reason="PURCHASE",
                        note="Seeder order",
                    )

                order.total_price = total
                order.save()

                # ---------------- PAYMENT ----------------
                Payment.objects.create(
                    order=order,
                    amount=total,
                    status=random.choice(Payment.Status.values),
                    method=random.choice(Payment.Method.values),
                    transaction_id=fake.uuid4(),
                )

        self.stdout.write(self.style.SUCCESS(" Database seeded successfully!"))
