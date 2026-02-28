from django.db import models
from django.utils import timezone


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=200)
    isbn = models.CharField(max_length=13, unique=True)
    description = models.TextField(blank=True)
    published_year = models.IntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)
    is_available = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} by {self.author}"


class Order(models.Model):
    # Book snapshot (copied at time of purchase)
    book_title = models.CharField(max_length=200)
    book_author = models.CharField(max_length=200)
    book_isbn = models.CharField(max_length=13)
    book_price = models.DecimalField(max_digits=10, decimal_places=2)

    # Order details
    stripe_session_id = models.CharField(max_length=255, unique=True)
    customer_email = models.EmailField()
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    # Fulfillment tracking
    fulfilled = models.BooleanField(default=False)
    fulfilled_at = models.DateTimeField(null=True, blank=True)

    # Shipping details
    shipping_name = models.CharField(max_length=255, blank=True)
    shipping_address_line1 = models.CharField(max_length=255, blank=True)
    shipping_address_line2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100, blank=True)
    shipping_state = models.CharField(max_length=100, blank=True)
    shipping_postal_code = models.CharField(max_length=20, blank=True)
    shipping_country = models.CharField(max_length=2, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "Fulfilled" if self.fulfilled else "Pending"
        return f"Order #{self.id} - {self.book_title} ({status})"

    def save(self, *args, **kwargs):
        # Auto-update fulfilled_at when marked as fulfilled
        if self.fulfilled and not self.fulfilled_at:
            self.fulfilled_at = timezone.now()
        super().save(*args, **kwargs)
