# bookstore

bookstore software

## Development

Run development server:

```sh
uv run manage.py runserver
```

Load sample data (books and admin user):

```sh
uv run manage.py setupsampledata
```

This creates:
- 5 sample books (priced at $10.00 each)
- Admin user (username: `admin`, password: `admin`)

### Stripe Setup

1. Copy `.envrc.example` to `.envrc` and add your Stripe keys:
   ```bash
   export STRIPE_SECRET_KEY=sk_test_xxx
   export STRIPE_PUBLISHABLE_KEY=pk_test_xxx
   ```

2. Install Stripe CLI and login:
   ```bash
   stripe login
   ```

3. Start webhook forwarding:
   ```bash
   stripe listen --forward-to localhost:8000/stripe/webhook/
   ```

4. Copy the webhook signing secret and add to `.envrc`:
   ```bash
   export STRIPE_WEBHOOK_SECRET=whsec_xxx
   ```

### Email Setup

For development, set `LOCALDEV=1` to print emails to console instead of sending.

For production, configure Postmark credentials in `.envrc`.

Run linting:

```sh
uv run ruff check
uv run djade main/templates/**/*.html --check
```

Run formatting:

```sh
uv run ruff format
uv run djade main/templates/**/*.html
```

## Commit Message Style

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>: <description>

[optional body]
```

Types:
- `feat`: new feature
- `fix`: bug fix  
- `docs`: documentation changes
- `style`: formatting (no code changes)
- `refactor`: code refactoring
- `test`: adding tests
- `chore`: maintenance tasks

Examples:
- `feat: add Stripe checkout integration`
- `fix: correct book price display in cart`
- `docs: update deployment instructions`

## License

Copyright sirodoht

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, version 3.
