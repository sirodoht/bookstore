"""Models for the books app."""

import io
import os
from uuid import uuid4

from django.db import models
from django.utils import timezone
from PIL import Image


def book_cover_path(instance, filename):
    """Generate unique path for book cover images."""
    ext = os.path.splitext(filename)[1].lower()
    return f"book_covers/{uuid4()}{ext}"


class Book(models.Model):
    """Book model with cover image processing."""

    title = models.CharField(max_length=200)
    author = models.CharField(max_length=200)
    isbn = models.CharField(max_length=13, unique=True, blank=True)
    description = models.TextField(blank=True)
    published_year = models.IntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)
    is_available = models.BooleanField(default=True)
    cover_image = models.ImageField(
        upload_to=book_cover_path,
        blank=True,
        null=True,
        help_text="Book cover image (will be cropped to 13:18 ratio)",
    )

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} by {self.author}"

    def save(self, *args, **kwargs):
        """Override save to process cover image."""
        if self.cover_image:
            self._process_cover_image()
        super().save(*args, **kwargs)

    def _process_cover_image(self):
        """Process cover image to 13:18 ratio with center cropping."""
        if not self.cover_image:
            return

        # Open the image
        img = Image.open(self.cover_image)

        # Convert to RGB if necessary (handles PNG with transparency)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Target ratio: 13:18 (width:height)
        target_ratio = 13 / 18

        # Get current dimensions
        width, height = img.size
        current_ratio = width / height

        # Calculate crop dimensions for center cropping
        if current_ratio > target_ratio:
            # Image is too wide, crop width
            new_width = int(height * target_ratio)
            left = (width - new_width) // 2
            right = left + new_width
            top = 0
            bottom = height
        else:
            # Image is too tall, crop height
            new_height = int(width / target_ratio)
            top = (height - new_height) // 2
            bottom = top + new_height
            left = 0
            right = width

        # Crop the image
        img = img.crop((left, top, right, bottom))

        # Resize to standard dimensions (390x540 for 13:18 at 30px per unit)
        img = img.resize((390, 540), Image.Resampling.LANCZOS)

        # Save back to the field
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85)
        output.seek(0)

        # Update the field
        self.cover_image.file = output
        self.cover_image.name = self.cover_image.name


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
