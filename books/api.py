"""API endpoints for the books app."""

import logging

from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse

from . import openai as openai_module

logger = logging.getLogger(__name__)


@user_passes_test(lambda u: u.is_staff)
def analyze_cover(request):
    """Analyze book cover image using OpenAI and extract book details."""
    if request.method != "POST":
        logger.warning(
            "analyze_cover: Received non-POST request from user %s", request.user
        )
        return JsonResponse({"error": "Only POST requests allowed"}, status=405)

    if "cover_image" not in request.FILES:
        logger.warning(
            "analyze_cover: No cover_image in request from user %s", request.user
        )
        return JsonResponse({"error": "No image provided"}, status=400)

    cover_image = request.FILES["cover_image"]

    logger.info(
        "analyze_cover: Processing image '%s' for user %s",
        cover_image.name,
        request.user,
    )

    try:
        image_data = cover_image.read()
    except Exception as e:
        logger.error("analyze_cover: Failed to read image: %s", e, exc_info=True)
        return JsonResponse({"error": "Failed to process image"}, status=500)

    analysis = openai_module.analyze_cover_image(image_data)

    if not analysis["success"]:
        error_msg = analysis.get("error", "Analysis failed")
        status_code = 500
        if "rate limited" in error_msg.lower():
            status_code = 429
        elif "not configured" in error_msg.lower():
            status_code = 500
        return JsonResponse({"error": error_msg}, status=status_code)

    return JsonResponse(
        {
            "title": analysis.get("title", ""),
            "author": analysis.get("author", ""),
            "description": analysis.get("description", ""),
            "published_year": analysis.get("published_year", ""),
        }
    )
