from django.contrib import admin
from django.utils.html import format_html

from .models import Book, Order


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "author",
        "published_year",
        "isbn",
        "price",
        "is_available",
    ]
    search_fields = ["title", "author", "isbn"]
    list_filter = ["published_year", "author", "is_available"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "book_title",
        "customer_email",
        "created_at",
        "amount_paid",
        "fulfilled",
        "fulfillment_status",
        "shipping_country",
    ]
    list_filter = ["fulfilled", "created_at", "shipping_country", "fulfilled_at"]
    search_fields = [
        "customer_email",
        "book_title",
        "book_isbn",
        "stripe_session_id",
    ]
    list_editable = ["fulfilled"]
    readonly_fields = [
        "created_at",
        "fulfilled_at",
        "stripe_session_id",
        "amount_paid",
        "book_isbn",
        "book_price",
    ]

    fieldsets = [
        (
            "Order Information",
            {
                "fields": [
                    ("book_title", "book_author"),
                    ("book_isbn", "book_price"),
                    "customer_email",
                    "amount_paid",
                    ("created_at", "fulfilled", "fulfilled_at"),
                ]
            },
        ),
        ("Payment Details", {"fields": ["stripe_session_id"], "classes": ["collapse"]}),
        (
            "Shipping Address",
            {
                "fields": [
                    "shipping_name",
                    ("shipping_address_line1", "shipping_address_line2"),
                    ("shipping_city", "shipping_state"),
                    ("shipping_postal_code", "shipping_country"),
                ]
            },
        ),
    ]

    def fulfillment_status(self, obj):
        if obj.fulfilled:
            return format_html('<span style="color: green;">✓ Fulfilled</span>')
        return format_html('<span style="color: orange;">○ Pending</span>')

    fulfillment_status.short_description = "Status"
