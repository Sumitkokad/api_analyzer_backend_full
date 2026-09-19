try:
    from api_normalization import load_openapi_document
except ImportError:
    from .api_normalization import load_openapi_document


def load_api_spec(file_path):
    return load_openapi_document(file_path)
