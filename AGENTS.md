# Agent Guidelines for Bookstore

## Project Overview

Django bookstore application with Stripe payment integration. Sells books with Stripe Checkout, sends email confirmations, and tracks orders.

## Build/Lint/Test Commands

```bash
# Development server
uv run manage.py runserver

# Linting (MUST run before committing)
uv run ruff check
uv run djade books/templates/**/*.html --check

# Formatting
uv run ruff format
uv run djade books/templates/**/*.html

# Django management
uv run manage.py <command>

# Database migrations
uv run manage.py makemigrations
uv run manage.py migrate

# Load sample data
uv run manage.py setupsampledata
```

**Note**: There are no tests yet. When adding tests, use Django's test framework:
```bash
# Run all tests
uv run manage.py test

# Run single test
uv run manage.py test books.tests.TestClassName.test_method_name
```

## Code Style Guidelines

### Python

- **Python version**: 3.13+
- **Quotes**: Use double quotes for strings (`"string"`)
- **Line length**: No strict limit (E501 ignored in ruff config)
- **Docstrings**: Use triple double quotes with one-line description for classes/functions
- **Type hints**: Not currently used; add only if explicitly requested

### Imports

- Group order: stdlib, Django, third-party, local
- Within each group: alphabetical order
- Absolute imports for Django modules (`from django.conf import settings`)
- Relative imports within app (`from .models import Book`)

Example:
```python
import os

from django.conf import settings
from django.shortcuts import get_object_or_404

import stripe

from .models import Book, Order
```

### Naming Conventions

- **Variables/functions**: `snake_case` (e.g., `book_list`, `get_context_data`)
- **Classes**: `PascalCase` (e.g., `BookListView`, `Order`)
- **Constants**: `UPPER_CASE` (e.g., `STRIPE_API_KEY`)
- **Files**: `snake_case.py`
- **Templates**: `snake_case.html`
- **URL names**: `kebab-case` (e.g., `book-list`, `checkout-success`)

### Django Patterns

- Use class-based views (CBVs) by default
- Use `get_object_or_404()` for object retrieval
- Use reverse URL lookups: `{% url 'books:book-buy' book.pk %}`
- Define `app_name` in URL configs for namespacing
- Models: Define `Meta.ordering`, implement `__str__()`

### Error Handling

- Catch specific exceptions, not bare `except:`
- Return appropriate HTTP status codes in views
- Use `fail_silently=False` for critical email sends

### Templates (Django HTML)

- Use Djade formatter for HTML templates
- 4-space indentation in templates
- Use `{% url %}` tag for all internal links

## Commit Message Style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>

[optional body]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Examples:
- `feat: add Stripe checkout integration`
- `fix: correct book price display in cart`

## Git Workflow

Never push to the remote repository unless the user explicitly asks you to do so.

## Project Structure

```
bookstore/          # Django project settings
books/              # Main app (models, views, templates)
books/templates/    # Django HTML templates
deploy/             # Deployment scripts
manage.py           # Django management
pyproject.toml      # Dependencies and tool config
```

## Environment Setup

Copy `.envrc.example` to `.envrc` and configure:
- `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` - Required for payments
- `STRIPE_WEBHOOK_SECRET` - Required for webhooks
- `LOCALDEV=1` - For development (emails print to console)
- `SECRET_KEY` - Production only
- `HOST` - Production domain

## Key Dependencies

- Django 6.0+
- Stripe Python SDK
- Ruff (linting)
- Djade (HTML formatting)
