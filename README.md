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
- 5 sample books
- Admin user (username: `admin`, password: `admin`)

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

## License

Copyright sirodoht

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, version 3.
