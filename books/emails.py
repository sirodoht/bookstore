"""Email utilities for the books app."""

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_purchase_confirmation(order):
    """Send confirmation email to customer."""
    purchase_date = order.created_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""

SHIPPING ADDRESS
---
Name: {order.shipping_name}
Address: {order.shipping_address_line1}
"""
        if order.shipping_address_line2:
            shipping_info += f"           {order.shipping_address_line2}\n"
        shipping_info += f"""City: {order.shipping_city}
State/Province: {order.shipping_state}
Postal Code: {order.shipping_postal_code}
Country: {order.shipping_country}"""

    isbn_info = (
        f"""ISBN: {order.book_isbn}
"""
        if order.book_isbn
        else ""
    )

    subject = f"[{settings.HOST}] Order Confirmation #{order.id} - {order.book_title}"
    body = f"""Thank you for your purchase!

ORDER #{order.id}
---
Order Date: {purchase_date}
Status: Pending (we ship within 2 business days)

BOOK DETAILS
---
Title: {order.book_title}
Author: {order.book_author}
{isbn_info}Price: £{order.amount_paid:.2f}
{shipping_info}

If you have any questions about your order just reply to this message.
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [order.customer_email],
        fail_silently=False,
    )


def send_admin_notification(order):
    """Send notification email to admin about new order."""
    if not settings.ADMINS:
        return

    purchase_date = order.created_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""
SHIPPING ADDRESS:
Name: {order.shipping_name}
Address: {order.shipping_address_line1}
"""
        if order.shipping_address_line2:
            shipping_info += f"         {order.shipping_address_line2}\n"
        shipping_info += f"""City: {order.shipping_city}
State: {order.shipping_state}
Postcode: {order.shipping_postal_code}
Country: {order.shipping_country}"""

    customer_display = (
        order.shipping_name if order.shipping_name else order.customer_email
    )
    shipping_location = ""
    if order.shipping_city and order.shipping_country:
        shipping_location = f" — {order.shipping_city}, {order.shipping_country}"

    isbn_info_admin = (
        f"""ISBN: {order.book_isbn}
"""
        if order.book_isbn
        else ""
    )

    subject = (
        f"[bookstore] {customer_display} bought {order.book_title}{shipping_location}"
    )
    body = f"""A new order has been placed!

ORDER #{order.id}
---
Order Date: {purchase_date}
Customer Email: {order.customer_email}
Stripe Session: {order.stripe_session_id}

BOOK DETAILS:
Title: {order.book_title}
Author: {order.book_author}
{isbn_info_admin}Price: £{order.amount_paid:.2f}
{shipping_info}
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        settings.ADMINS,
        fail_silently=False,
    )


def send_race_condition_refund_notification(
    book_title, book_author, customer_email, amount_total, refund_status
):
    """Send notification to customer when refunded due to race condition."""
    subject = f"[{settings.HOST}] Order Canceled - {book_title}"
    amount = Decimal(amount_total) / Decimal(100)

    if refund_status == "succeeded":
        refund_message = (
            f"You have been issued a full refund of £{amount:.2f}. "
            "The refund will appear on your payment method within 10 business days."
        )
    elif refund_status == "not attempted":
        refund_message = (
            "We were unable to process a refund automatically. Our team "
            f"has been notified and will manually issue a full refund of £{amount:.2f} "
            "to your payment method within 24 hours."
        )
    else:
        refund_message = (
            "We encountered an issue processing your refund automatically. Our team "
            f"has been notified and will manually issue a full refund of £{amount:.2f} "
            "to your payment method within 24 hours."
        )

    body = f"""We're sorry, but we were unable to complete your purchase.

BOOK DETAILS
---
Title: {book_title}
Author: {book_author}
Price: £{amount:.2f}

WHAT HAPPENED
---
Unfortunately, this book was sold to another customer just moments before your order was completed. We know this is disappointing, and we sincerely apologize for the inconvenience.

REFUND INFORMATION
---
{refund_message}

If you have any questions or need assistance, please contact us.

Thank you for your understanding
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [customer_email],
        fail_silently=False,
    )


def send_fulfillment_confirmation(order):
    """Send shipping confirmation to customer when order is fulfilled."""
    shipped_date = order.fulfilled_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""

SHIPPING ADDRESS
---
Name: {order.shipping_name}
Address: {order.shipping_address_line1}
"""
        if order.shipping_address_line2:
            shipping_info += f"           {order.shipping_address_line2}\n"
        shipping_info += f"""City: {order.shipping_city}
State/Province: {order.shipping_state}
Postal Code: {order.shipping_postal_code}
Country: {order.shipping_country}"""

    isbn_info = (
        f"""ISBN: {order.book_isbn}
"""
        if order.book_isbn
        else ""
    )

    subject = f"[{settings.HOST}] Order Shipped - {order.book_title}"
    body = f"""Your order has been shipped!

ORDER #{order.id}
---
Shipped Date: {shipped_date}
Status: Shipped

BOOK DETAILS
---
Title: {order.book_title}
Author: {order.book_author}
{isbn_info}Price: £{order.amount_paid:.2f}
{shipping_info}

Thank you for your purchase! If you have any questions about your order just reply to this message.
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [order.customer_email],
        fail_silently=False,
    )


def send_admin_fulfillment_notification(order):
    """Send notification to admin when order is marked as fulfilled."""
    if not settings.ADMINS:
        return

    shipped_date = order.fulfilled_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""
SHIPPING ADDRESS:
Name: {order.shipping_name}
Address: {order.shipping_address_line1}
"""
        if order.shipping_address_line2:
            shipping_info += f"         {order.shipping_address_line2}\n"
        shipping_info += f"""City: {order.shipping_city}
State: {order.shipping_state}
Postcode: {order.shipping_postal_code}
Country: {order.shipping_country}"""

    customer_display = (
        order.shipping_name if order.shipping_name else order.customer_email
    )

    isbn_info_admin = (
        f"""ISBN: {order.book_isbn}
"""
        if order.book_isbn
        else ""
    )

    subject = f"[bookstore] Order marked as fulfilled: #{order.id} - {order.book_title}"
    body = f"""Order #{order.id} has been marked as fulfilled.

FULFILLMENT DETAILS
---
Shipped Date: {shipped_date}
Customer: {customer_display}
Customer Email: {order.customer_email}

BOOK DETAILS:
Title: {order.book_title}
Author: {order.book_author}
{isbn_info_admin}Price: £{order.amount_paid:.2f}
{shipping_info}
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        settings.ADMINS,
        fail_silently=False,
    )
