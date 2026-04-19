"""Models for the books app."""

import io
import os
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageOps


def book_cover_path(instance, filename):
    """Generate unique path for book cover images."""
    ext = os.path.splitext(filename)[1].lower()
    return f"book_covers/{uuid4()}{ext}"


class Tag(models.Model):
    """Tag for categorizing books."""

    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """Auto-generate slug from name."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Book(models.Model):
    """Book model with cover image processing."""

    title = models.CharField(max_length=200)
    author = models.CharField(max_length=200)
    isbn = models.CharField(max_length=13, blank=True, null=True)
    description = models.TextField(blank=True)
    review = models.TextField(blank=True, help_text="Public review of the book")
    published_year = models.IntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=4.00)
    is_available = models.BooleanField(default=True)
    cover_image = models.ImageField(
        upload_to=book_cover_path,
        blank=True,
        null=True,
        help_text="Book cover image (will be cropped to 13:18 ratio)",
    )
    amazon_link = models.URLField(
        blank=True,
        null=True,
        help_text="Amazon affiliate link for external purchase",
    )
    worldofbooks_link = models.URLField(
        blank=True,
        null=True,
        help_text="World of Books affiliate link for external purchase",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="books")

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} by {self.author}"

    def save(self, *args, **kwargs):
        """Override save to process cover image."""
        if self.cover_image and not self.cover_image._committed:
            self._process_cover_image()
        super().save(*args, **kwargs)

    def _process_cover_image(self):
        """Process cover image to 13:18 ratio with center cropping."""
        if not self.cover_image:
            return

        # Open the image and apply EXIF orientation
        img = Image.open(self.cover_image)
        img = ImageOps.exif_transpose(img)

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

        # Resize to standard dimensions (780x1080 for 13:18 at 60px per unit)
        img = img.resize((780, 1080), Image.Resampling.LANCZOS)

        # Save processed image to buffer with optimization
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        output.seek(0)

        # Replace the uploaded file in-memory so Django writes only the final image.
        original_stem = os.path.splitext(os.path.basename(self.cover_image.name))[0]
        processed_name = f"{original_stem or 'cover'}.jpg"
        self.cover_image = ContentFile(output.read(), name=processed_name)


class Order(models.Model):
    # Book snapshot (copied at time of purchase)
    book_title = models.CharField(max_length=200)
    book_author = models.CharField(max_length=200)
    book_isbn = models.CharField(max_length=13, blank=True)
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
    shipping_country = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "Fulfilled" if self.fulfilled else "Pending"
        return f"Order #{self.id} - {self.book_title} ({status})"

    def save(self, *args, **kwargs):
        # Track if this is a new fulfillment
        is_newly_fulfilled = False
        if self.pk:
            try:
                old_order = Order.objects.get(pk=self.pk)
                is_newly_fulfilled = self.fulfilled and not old_order.fulfilled
            except Order.DoesNotExist:
                pass
        else:
            is_newly_fulfilled = self.fulfilled

        # Auto-update fulfilled_at when marked as fulfilled
        if self.fulfilled and not self.fulfilled_at:
            self.fulfilled_at = timezone.now()

        super().save(*args, **kwargs)

        # Send fulfillment emails when order is newly marked as fulfilled
        if is_newly_fulfilled:
            from .emails import (
                send_admin_fulfillment_notification,
                send_fulfillment_confirmation,
            )

            send_fulfillment_confirmation(self)
            send_admin_fulfillment_notification(self)
