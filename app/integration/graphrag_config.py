import os
from dotenv import load_dotenv

load_dotenv()

class GraphRagConfig:
    def __init__(self, index_name, user_id=None, is_restricted=False):
        self.index_name = index_name
        self.user_id = user_id
        self.is_restricted = is_restricted
        self.env = os.getenv("APP_ENV", "local").lower()

    def get_config(self):
        if self.env == "azure":
            return self._azure_config()
        return self._local_config()

    # ---------------- LOCAL MODE ----------------
    def _local_config(self):
        base_dir = os.path.join(os.path.abspath("knowledgebase"), self.index_name)
        output_dir = os.path.join(os.path.abspath("output"), self.index_name)
        cache_dir = os.path.join(os.path.abspath("cache"), self.index_name)

        os.makedirs(base_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)

        return {
            "input": {
                "type": "file",
                "base_dir": base_dir,
                "file_type": "text",
                "file_pattern": r".*\.(txt|md|pdf)$",
            },
            "storage": {
                "type": "file",
                "base_dir": output_dir,
            },
            "cache": {
                "type": "file",
                "base_dir": cache_dir,
            },
            "llm": {
                "type": "openai_chat",
                "api_key": os.getenv("OPENAI_API_KEY"),
                "api_base": os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1"),
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "model_supports_json": True,
            },
            "embeddings": {
                "async_mode": "threaded",
                "llm": {
                    "type": "openai_embedding",
                    "api_key": os.getenv("OPENAI_API_KEY"),
                    "api_base": os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1"),
                    "model": os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small"),
                },
                "vector_store": {
                    "type": "lancedb",
                    "collection_name": self.index_name,
                    "overwrite": True,
                    "title_column": "name_description",
                     "id_column": "id",
                },
            },
            "parallelization": {"stagger": 0.25, "num_threads": 8},
            "async_mode": "threaded",
            "entity_extraction": {
                "prompt": "app/ingestion/prompts/entity-extraction-prompt.txt"
            },
            "community_reports": {
                "prompt": "app/ingestion/prompts/community-report-prompt.txt"
            },
            "summarize_descriptions": {
                "prompt": "app/ingestion/prompts/summarize-descriptions-prompt.txt"
            },
            "claim_extraction": {"enabled": True},
            "snapshots": {"graphml": True},
        }

    # ---------------- AZURE MODE ----------------
    def _azure_config(self):
        prefix = "open" if not self.is_restricted else self.user_id
        collection_name = f"{prefix}-{self.index_name}-graphrag"

        storage_account = os.getenv("STORAGE_ACCOUNT_NAME")
        storage_key = os.getenv("STORAGE_ACCOUNT_KEY")

        blob_url = f"https://{storage_account}.blob.core.windows.net"
        connection_string = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={storage_account};"
            f"AccountKey={storage_key};"
            f"EndpointSuffix=core.windows.net"
        )

        base_config = {
            "storage_account_blob_url": blob_url,
            "connection_string": connection_string,
        }

        return {
            "input": {
                **base_config,
                "container_name": f"{prefix}-{self.index_name}-ingestion",
                "type": "blob",
                "file_type": "text",
                "file_pattern": r".*\.md$",
                "base_dir": ".",
            },
            "storage": {
                **base_config,
                "container_name": f"{prefix}-{self.index_name}-grdata",
                "type": "blob",
                "base_dir": "output",
            },
            "cache": {
                **base_config,
                "container_name": f"{prefix}-{self.index_name}-grcache",
                "type": "blob",
                "base_dir": "cache",
            },
            "llm": {
                "type": "azure_openai_chat",
                "api_base": os.getenv("AOAI_ENDPOINT"),
                "api_key": os.getenv("AOAI_API_KEY"),
                "deployment_name": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
                "model_supports_json": True,
            },
            "embeddings": {
                "async_mode": "threaded",
                "llm": {
                    "type": "azure_openai_embedding",
                    "api_base": os.getenv("AOAI_ENDPOINT"),
                    "api_key": os.getenv("AOAI_API_KEY"),
                    "deployment_name": os.getenv("ADA_DEPLOYMENT_NAME"),
                },
                "vector_store": {
                    "type": "azure_ai_search",
                    "collection_name": collection_name,
                    "api_key": os.getenv("SEARCH_SERVICE_API_KEY"),
                    "url": os.getenv("SEARCH_SERVICE_ENDPOINT"),
                },
            },
            "parallelization": {"stagger": 0.25, "num_threads": 10},
            "async_mode": "threaded",
            "entity_extraction": {
                "prompt": "app/ingestion/prompts/entity-extraction-prompt.txt"
            },
            "community_reports": {
                "prompt": "app/ingestion/prompts/community-report-prompt.txt"
            },
            "summarize_descriptions": {
                "prompt": "app/ingestion/prompts/summarize-descriptions-prompt.txt"
            },
            "claim_extraction": {"enabled": True},
            "snapshots": {"graphml": True},
        }
