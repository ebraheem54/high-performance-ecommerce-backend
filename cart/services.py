"""
Business logic for cart app.
"""

from cart.models import CartItem


def get_cart(user):
    """Return all cart items for a user with product details."""
    return CartItem.objects.select_related("product").filter(user=user)


def add_to_cart(user, product_id: int, quantity: int = 1) -> CartItem:
    """
    Add a product to the cart or increase quantity if already present.
    """
    from products.models import Product
    product = Product.objects.get(id=product_id, is_active=True)

    if product.stock < quantity:
        raise ValueError(f"Only {product.stock} units available.")

    item, created = CartItem.objects.get_or_create(
        user=user,
        product=product,
        defaults={"quantity": quantity},
    )
    if not created:
        item.quantity += quantity
        item.save(update_fields=["quantity", "updated_at"])

    return item


def remove_from_cart(user, product_id: int) -> bool:
    """Remove a product from the cart. Returns True if deleted."""
    deleted, _ = CartItem.objects.filter(user=user, product_id=product_id).delete()
    return deleted > 0


def clear_cart(user) -> int:
    """Remove all items from the user's cart. Returns count deleted."""
    deleted, _ = CartItem.objects.filter(user=user).delete()
    return deleted


def update_cart_item_quantity(user, product_id: int, quantity: int) -> CartItem:
    """Set exact quantity for a cart item."""
    if quantity <= 0:
        remove_from_cart(user, product_id)
        return None
    item = CartItem.objects.get(user=user, product_id=product_id)
    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    return item
