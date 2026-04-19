"""Amazon book scraping utilities."""

import re

import requests
from bs4 import BeautifulSoup


def follow_redirect(url):
    """Follow amzn.to short link to get real Amazon URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
    }

    try:
        # Use GET instead of HEAD to better simulate browser behavior
        response = requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=30,
        )
        response.raise_for_status()
        return response.url
    except requests.RequestException as e:
        return {"error": f"Failed to follow redirect: {str(e)}"}


def extract_asin(url):
    """Extract ASIN from Amazon URL (/dp/ASIN or /gp/product/ASIN)."""
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def isbn10_to_isbn13(isbn10):
    """Convert ISBN-10 to ISBN-13."""
    if not isbn10 or len(isbn10) != 10:
        return None

    if not isbn10[:-1].isdigit():
        return None

    # Add "978" prefix and remove the ISBN-10 check digit
    isbn12 = "978" + isbn10[:-1]

    # Calculate ISBN-13 check digit
    total = sum(int(digit) * (1 if i % 2 == 0 else 3) for i, digit in enumerate(isbn12))
    check_digit = (10 - (total % 10)) % 10

    return isbn12 + str(check_digit)


def lookup_open_library(isbn):
    """Look up book data from Open Library API by ISBN."""
    if not isbn:
        return None

    # Clean ISBN (remove hyphens, spaces)
    clean_isbn = re.sub(r"[^0-9X]", "", isbn, flags=re.I).upper()

    try:
        response = requests.get(
            f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_isbn}&format=json&jscmd=data",
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        key = f"ISBN:{clean_isbn}"
        if key not in data:
            return None

        book_data = data[key]
        result = {
            "title": book_data.get("title"),
            "author": None,
            "year": None,
            "cover_url": None,
            "isbn": clean_isbn,
        }

        # Extract author
        authors = book_data.get("authors", [])
        if authors:
            result["author"] = authors[0].get("name", "Unknown Author")

        # Extract year
        publish_date = book_data.get("publish_date", "")
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", publish_date)
        if year_match:
            result["year"] = int(year_match.group(1))

        # Extract cover
        cover = book_data.get("cover", {})
        if cover:
            # Prefer large cover
            result["cover_url"] = (
                cover.get("large") or cover.get("medium") or cover.get("small")
            )

        return result

    except requests.RequestException:
        return None


def scrape_book_data(url):
    """Scrape book metadata from Amazon product page or fallback to Open Library."""
    # More realistic browser headers to avoid 503
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    # First, try to extract ASIN/ISBN from URL
    asin = extract_asin(url)
    isbn_from_url = None
    if asin and re.match(r"^[0-9X]{10}$", asin, re.I):
        isbn_from_url = asin

    try:
        response = requests.get(url, headers=headers, timeout=30)

        # Handle common Amazon blocking responses
        if response.status_code == 503:
            # Try fallback to Open Library
            if isbn_from_url:
                openlib_data = lookup_open_library(isbn_from_url)
                if openlib_data:
                    return openlib_data
            return {
                "error": "Amazon is temporarily blocking requests and no fallback data available."
            }

        if response.status_code == 404:
            return {"error": "Product page not found (404)"}

        response.raise_for_status()
    except requests.RequestException as e:
        # Try fallback to Open Library
        if isbn_from_url:
            openlib_data = lookup_open_library(isbn_from_url)
            if openlib_data:
                return openlib_data
        return {"error": f"Failed to fetch page: {str(e)}"}

    soup = BeautifulSoup(response.text, "html.parser")

    data = {}

    # Check if we hit a CAPTCHA/bot challenge page
    captcha_indicators = [
        "validateCaptcha",
        "continue shopping",
        "type the characters",
        "captcha",
        "robot check",
        "a-captcha",
    ]
    page_text = response.text.lower()
    is_captcha = any(indicator in page_text for indicator in captcha_indicators)

    # Check for Amazon error pages
    is_error_page = soup.select_one(".a-error-page") or "dogs of amazon" in page_text

    # If we hit CAPTCHA or error page, try Open Library fallback
    if is_captcha or is_error_page:
        if isbn_from_url:
            openlib_data = lookup_open_library(isbn_from_url)
            if openlib_data:
                return openlib_data

        if is_captcha:
            return {
                "error": "Amazon is requiring CAPTCHA verification. Trying Open Library fallback..."
            }
        return {"error": "Amazon is temporarily blocking requests."}

    # Extract title - try multiple selectors
    title = None
    title_selectors = [
        "#productTitle",
        "[data-testid='product-title']",
        "h1.a-size-large",
        "h1.a-size-medium",
        "#title",
        "#ebooksProductTitle",
        "span#productTitle",
        "h1",
    ]

    for selector in title_selectors:
        title_elem = soup.select_one(selector)
        if title_elem:
            title = title_elem.get_text(strip=True)
            if title and len(title) > 3:  # Ensure it's a real title
                break

    # Fallback: look for title in meta tags
    if not title:
        meta_title = soup.find("meta", property="og:title") or soup.find(
            "meta", attrs={"name": "title"}
        )
        if meta_title:
            title = meta_title.get("content", "").strip()
            # Remove " | Amazon.co.uk" or similar suffixes
            title = re.sub(r"\s*\|\s*Amazon\..*$", "", title)

    if not title:
        # Try Open Library fallback
        if isbn_from_url:
            openlib_data = lookup_open_library(isbn_from_url)
            if openlib_data:
                return openlib_data
        return {"error": "Could not find book title"}

    data["title"] = title

    # Extract author - try multiple selectors
    author = None
    author_selectors = [
        ".author a",
        ".contributorName",
        "[data-testid='author-name']",
        "#bylineInfo .a-link-normal",
        "#bylineInfo span",
        ".a-section#bylineInfo a",
        "[data-testid='byline-info']",
        "#bookAuthor",
        "#author",
    ]

    for selector in author_selectors:
        author_elem = soup.select_one(selector)
        if author_elem:
            author = author_elem.get_text(strip=True)
            # Clean up author text (remove "by" prefix if present)
            author = re.sub(r"^by\s+", "", author, flags=re.I)
            if author and len(author) > 1:
                break

    if not author:
        # Try meta tag
        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author:
            author = meta_author.get("content", "").strip()

    if not author:
        # Try to find in feature bullets or description
        author_elem = soup.find(
            string=re.compile(r"by\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)", re.I)
        )
        if author_elem:
            match = re.search(
                r"by\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)", author_elem, re.I
            )
            if match:
                author = match.group(1)

    if not author:
        author = "Unknown Author"

    data["author"] = author

    # Extract ISBN from product details
    isbn = None
    details = soup.select(
        "#productDetailsTable td, #detailBullets td, .a-unordered-list.a-nostyle li, "
        "#productDetails_techSpec_section_1 td, #productDetails_detailBullets_sections1 td, "
        "[data-testid='product-details'] tr, .prodDetTable td"
    )

    for detail in details:
        text = detail.get_text(strip=True)
        # Look for ISBN-10
        isbn_match = re.search(r"ISBN-10[:\s]+([0-9X]{10})", text, re.I)
        if isbn_match:
            isbn = isbn_match.group(1)
            break
        # Alternative format
        isbn_match = re.search(r"ISBN-?10[^0-9]*([0-9X]{10})", text, re.I)
        if isbn_match:
            isbn = isbn_match.group(1)
            break
        # Look for ASIN which is often the ISBN for books
        asin_match = re.search(r"ASIN[:\s]+([A-Z0-9]{10})", text, re.I)
        if asin_match:
            potential_isbn = asin_match.group(1)
            # Check if it looks like an ISBN (mostly digits)
            if re.match(r"^[0-9X]{10}$", potential_isbn, re.I):
                isbn = potential_isbn
                break

    # Fallback: try to extract ASIN from URL if no ISBN found
    if not isbn:
        asin = extract_asin(url)
        if asin and re.match(r"^[0-9X]{10}$", asin, re.I):
            isbn = asin

    data["isbn"] = isbn

    # Extract publication year
    year = None
    for detail in details:
        text = detail.get_text(strip=True)
        # Look for publication date patterns
        year_match = re.search(r"(?:Publication Date|Date)[:\s]+.*?(\d{4})", text, re.I)
        if year_match:
            year = int(year_match.group(1))
            break
        # Alternative: look for any 4-digit year in product details
        if not year:
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
            if year_match:
                year = int(year_match.group(1))
                break

    data["year"] = year

    # Extract cover image URL
    cover_url = None
    for selector in ["#landingImage", "#imgBlkFront", "#ebooksImgBlkFront"]:
        img_elem = soup.select_one(selector)
        if img_elem:
            # Try data-old-hires first (high-res), then src
            cover_url = img_elem.get("data-old-hires") or img_elem.get("src")
            if cover_url and not cover_url.startswith("data:"):
                break

    # Fallback: look for any large book image
    if not cover_url:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt", "")
            if (
                ("book" in alt.lower() or "cover" in alt.lower())
                and "amazon" in src
                and ("_SL" in src or "images-na" in src)
            ):
                cover_url = src
                break

    data["cover_url"] = cover_url

    return data


def download_image(url):
    """Download cover image from Amazon CDN."""
    if not url:
        return None

    # Upgrade to higher resolution if possible
    # Amazon images often have _SLXXX_ format, try to get larger
    if "_SL" in url:
        url = re.sub(r"_SL\d+_", "_SL1000_", url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.amazon.com/",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None
