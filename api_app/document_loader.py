from langchain_community.document_loaders import TextLoader


def load_api_document(file_path):
    loader = TextLoader(
        file_path,
        encoding="utf-8"
    )

    return loader.load()