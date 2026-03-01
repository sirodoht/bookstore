"""OpenAI module for analyzing book cover images."""

import base64
import io
import json
import logging

import openai
from django.conf import settings
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def _resize_image_for_analysis(image_data, max_size=1024):
    """Resize image to reduce token usage while preserving quality for analysis.

    Args:
        image_data: Raw bytes of the image file
        max_size: Maximum dimension (width or height) for the resized image

    Returns:
        bytes: Resized image data as JPEG
    """
    try:
        img = Image.open(io.BytesIO(image_data))
        img = ImageOps.exif_transpose(img)

        # Convert to RGB if necessary
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Resize if larger than max_size
        width, height = img.size
        if width > max_size or height > max_size:
            ratio = min(max_size / width, max_size / height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save to buffer with optimization
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        output.seek(0)

        resized_data = output.read()
        logger.debug(
            "_resize_image_for_analysis: Resized from %d to %d bytes",
            len(image_data),
            len(resized_data),
        )
        return resized_data
    except Exception as e:
        logger.error(
            "_resize_image_for_analysis: Failed to resize: %s", e, exc_info=True
        )
        # Return original if resize fails
        return image_data


def analyze_cover_image(image_data):
    """Analyze a book cover image using OpenAI and extract book details.

    Args:
        image_data: Raw bytes of the image file

    Returns:
        dict: Extracted book details with keys:
            - title (str): Book title
            - author (str): Author name
            - description (str): Book description/blurb
            - published_year (str): Publication year
            - success (bool): Whether analysis was successful
            - error (str, optional): Error message if failed
    """
    if not settings.OPENAI_API_KEY:
        logger.error("analyze_cover_image: OPENAI_API_KEY not configured")
        return {
            "title": "",
            "author": "",
            "description": "",
            "published_year": "",
            "success": False,
            "error": "OpenAI not configured",
        }

    # Resize image to reduce token usage
    resized_data = _resize_image_for_analysis(image_data)

    try:
        base64_image = base64.b64encode(resized_data).decode("utf-8")
        logger.debug("analyze_cover_image: Image encoded (%d bytes)", len(resized_data))
    except Exception as e:
        logger.error(
            "analyze_cover_image: Failed to encode image: %s", e, exc_info=True
        )
        return {
            "title": "",
            "author": "",
            "description": "",
            "published_year": "",
            "success": False,
            "error": "Failed to process image",
        }

    prompt = """Analyze this book cover image and provide the following information:

1. Title: The main title of the book
2. Author: The author's name
3. Description: A one-sentence blurb or description of what the book is about
4. Published Year: The publication year

Return ONLY a JSON object with these exact keys:
- title (string, empty if not known)
- author (string, empty if not known)
- description (string, empty if not known)
- published_year (string, empty if not known)

If any field cannot be determined, use an empty string as the value.
Do not include markdown formatting, just the raw JSON."""

    try:
        logger.info("analyze_cover_image: Sending request to OpenAI")

        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            max_completion_tokens=500,
        )

        content = response.choices[0].message.content
        logger.debug("analyze_cover_image: OpenAI response: %s", content)

        try:
            data = json.loads(content)

            required_keys = ["title", "author", "description", "published_year"]
            for key in required_keys:
                if key not in data:
                    logger.warning(
                        "analyze_cover_image: Missing key '%s' in response", key
                    )
                    data[key] = ""

            data["success"] = True

            logger.info(
                "analyze_cover_image: Success - title: '%s', author: '%s'",
                data.get("title", "")[:50],
                data.get("author", "")[:50],
            )

            return data

        except json.JSONDecodeError as e:
            logger.error(
                "analyze_cover_image: Failed to parse JSON: %s\nContent: %s",
                e,
                content,
            )
            return {
                "title": "",
                "author": "",
                "description": "",
                "published_year": "",
                "success": False,
                "error": "Failed to parse AI response",
            }

    except openai.APIError as e:
        logger.error("analyze_cover_image: OpenAI API error: %s", e, exc_info=True)
        return {
            "title": "",
            "author": "",
            "description": "",
            "published_year": "",
            "success": False,
            "error": "AI service error",
        }
    except openai.RateLimitError as e:
        logger.error("analyze_cover_image: Rate limit exceeded: %s", e, exc_info=True)
        return {
            "title": "",
            "author": "",
            "description": "",
            "published_year": "",
            "success": False,
            "error": "AI service rate limited",
        }
    except Exception as e:
        logger.exception("analyze_cover_image: Unexpected error: %s", e)
        return {
            "title": "",
            "author": "",
            "description": "",
            "published_year": "",
            "success": False,
            "error": "Analysis failed",
        }
