from django.http import JsonResponse


def health_check(request):
    """
    Lightweight health endpoint used by GitHub Actions
    to wake/check the API Analyzer backend before CI submission.
    """

    if request.method != "GET":
        return JsonResponse(
            {
                "status": "error",
                "message": "Method not allowed",
            },
            status=405,
        )

    return JsonResponse(
        {
            "status": "ok",
            "service": "api-analyzer",
        },
        status=200,
    )