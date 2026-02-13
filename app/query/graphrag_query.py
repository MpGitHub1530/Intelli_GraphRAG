import os
import time
from io import BytesIO
from typing import Tuple, Dict, Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import tiktoken

import logging
from app.integration.graphrag_config import GraphRagConfig

logger = logging.getLogger(__name__)

from graphrag.query.indexer_adapters import read_indexer_reports
from graphrag.query.structured_search.global_search.search import GlobalSearch
from graphrag.query.structured_search.global_search.community_context import GlobalCommunityContext
from graphrag.query.llm.oai.chat_openai import ChatOpenAI
from graphrag.query.llm.oai.typing import OpenaiApiType

# Azure only import guarded
try:
    from azure.storage.blob import BlobServiceClient
except Exception:
    BlobServiceClient = None


class GraphRagQuery:
    def __init__(self, config: GraphRagConfig):
        self.config = config
        self.env = os.getenv("APP_ENV", "local").lower()

    # ---------------- Data loading ----------------

    @staticmethod
    def _read_parquet_local(file_path: str) -> pd.DataFrame:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Parquet file not found: {file_path}")
        return pq.read_table(file_path).to_pandas()

    def _read_parquet_from_blob(self, abfs_path: str) -> pd.DataFrame:
        if BlobServiceClient is None:
            raise RuntimeError("Azure Blob libraries are not available but Azure mode was used")

        cfg = self.config.get_config()
        conn = cfg.get("storage", {}).get("connection_string")
        if not conn:
            raise RuntimeError("Azure storage connection_string missing in config")

        blob_service_client = BlobServiceClient.from_connection_string(conn)

        # abfs://<container>/<blob_path>
        path = abfs_path.replace("abfs://", "", 1)
        parts = path.split("/", 1)
        if len(parts) < 2:
            raise ValueError(f"Invalid abfs path: {abfs_path}")

        container_name = parts[0]
        blob_name = parts[1]

        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

        stream = BytesIO()
        stream.write(blob_client.download_blob().readall())
        stream.seek(0)

        return pq.read_table(stream).to_pandas()

    def _load_local_uploaded_text(self, index_name: str, max_chars: int = 20000) -> str:
        base = os.path.abspath(os.path.join("knowledgebase", index_name))
        if not os.path.isdir(base):
            return ""

        texts = []
        for filename in os.listdir(base):
            if filename.lower().endswith((".txt", ".md")):
                file_path = os.path.join(base, filename)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        if content.strip():
                            texts.append(f"FILE: {filename}\n{content}\n")
                except Exception:
                    continue

        combined = "\n".join(texts).strip()
        return combined[:max_chars] if combined else ""

    def _format_context(self, reports: list, max_chars: int = 10000) -> str:
        if not reports:
            return ""
        
        try:
            reports_sorted = sorted(reports, key=lambda r: float(r.get("rank", 0)), reverse=True)
        except Exception:
            reports_sorted = reports

        chunks = []
        total = 0
        for i, r in enumerate(reports_sorted[:6], start=1):
            title = str(r.get("title", "report"))
            idx = str(r.get("index_id", "unknown"))
            rank = r.get("rank", "")
            content = str(r.get("content", "")).strip()

            block = f"[Report {i}] Title: {title} | Id: {idx} | Rank: {rank}\n{content}\n"
            if total + len(block) > max_chars:
                break
            chunks.append(block)
            total += len(block)
        
        return "\n".join(chunks).strip()

    def _get_reports(
        self,
        entity_table_path: str,
        community_report_table_path: str,
        community_level: int,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        if self.env != "azure":
            entity_df = self._read_parquet_local(entity_table_path)
            report_df = self._read_parquet_local(community_report_table_path)
            return report_df, entity_df

        report_df = self._read_parquet_from_blob(community_report_table_path)
        entity_df = self._read_parquet_from_blob(entity_table_path)
        return report_df, entity_df

    def get_reports(self, entity_table_path: str, community_report_table_path: str, community_level: int):
        return self._get_reports(entity_table_path, community_report_table_path, community_level)


    # ---------------- Query ----------------

    async def global_query(self, query: str) -> Tuple[str, Dict[str, Any]]:
        """
        Returns:
          answer_text: str
          context_data: dict (serializable) includes "reports" list for citations
        """

        COMMUNITY_LEVEL = 1

        # Resolve parquet locations
        if self.env != "azure":
            base = os.path.abspath(os.path.join("output", self.config.index_name))
            entity_table_path = os.path.join(base, "create_final_nodes.parquet")
            community_report_table_path = os.path.join(base, "create_final_community_reports.parquet")
        else:
            ENTITY_TABLE = "output/create_final_nodes.parquet"
            COMMUNITY_REPORT_TABLE = "output/create_final_community_reports.parquet"
            entity_table_path = f"abfs://{self.config.prefix}-{self.config.index_name}-grdata/{ENTITY_TABLE}"
            community_report_table_path = f"abfs://{self.config.prefix}-{self.config.index_name}-grdata/{COMMUNITY_REPORT_TABLE}"

        t0 = time.time()
        report_df, entity_df = self._get_reports(entity_table_path, community_report_table_path, COMMUNITY_LEVEL)
        print(f"GraphRAG report load time: {time.time() - t0:.2f}s")
        logger.info(f"Loaded {len(report_df)} community reports from parquet.")

        # Pack title so we can unpack later into index_name, index_id, title
        if "community" in report_df.columns and "title" in report_df.columns:
            report_df["title"] = [
                f"{self.config.index_name}<sep>{i}<sep>{t}"
                for i, t in zip(report_df["community"], report_df["title"])
            ]

        cfg = self.config.get_config()

        # Build LLM
        if self.env != "azure":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY missing for local GraphRAG query")

            model_name = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

            llm = ChatOpenAI(
                api_base="https://api.openai.com/v1",
                model=model_name,
                api_type=OpenaiApiType.OpenAI,
                api_key=api_key,
                max_retries=10,
            )
            token_encoder = tiktoken.encoding_for_model(model_name)

        else:
            llm_cfg = cfg.get("llm", {})
            if not llm_cfg:
                raise RuntimeError("Azure llm config missing")

            model_name = llm_cfg.get("model")
            if not model_name:
                raise RuntimeError("Azure llm model missing")

            llm = ChatOpenAI(
                api_base=llm_cfg["api_base"],
                model=model_name,
                api_type=OpenaiApiType.AzureOpenAI,
                deployment_name=llm_cfg.get("deployment_name"),
                api_version=llm_cfg.get("api_version"),
                api_key=llm_cfg.get("api_key"),
                max_retries=10,
            )
            token_encoder = tiktoken.encoding_for_model(model_name)

        context_builder = GlobalCommunityContext(
            community_reports=read_indexer_reports(report_df, entity_df, COMMUNITY_LEVEL),
            token_encoder=token_encoder,
        )

        global_search = GlobalSearch(
            llm=llm,
            context_builder=context_builder,
            token_encoder=token_encoder,
            max_data_tokens=80000,
            map_llm_params={"max_tokens": 2000, "temperature": 0.0},
            reduce_llm_params={"max_tokens": 3000, "temperature": 0.0},
            context_builder_params={
                "use_community_summary": False,
                "shuffle_data": True,
                "include_community_rank": True,
                "min_community_rank": 0,
                "max_tokens": 80000,
                "context_name": "Reports",
            },
            concurrent_coroutines=10,
        )

        t1 = time.time()
        result = await global_search.asearch(query=query)
        print(f"GraphRAG asearch time: {time.time() - t1:.2f}s")
        
        # Log result stats
        if result.context_data and "reports" in result.context_data:
            rep_count = len(result.context_data["reports"])
            logger.info(f"GlobalSearch returned {rep_count} reports for query: {query}")
        else:
            logger.warning(f"GlobalSearch returned NO context data for query: {query}")

        # Normalize reports to list[dict]
        processed_reports = []
        reports_val = result.context_data.get("reports")

        if isinstance(reports_val, pd.DataFrame):
            for _, row in reports_val.iterrows():
                title_raw = str(row.get("title", ""))
                parts = title_raw.split("<sep>")
                processed_reports.append({
                    "index_name": parts[0] if len(parts) > 0 else self.config.index_name,
                    "index_id": parts[1] if len(parts) > 1 else "unknown",
                    "title": parts[2] if len(parts) > 2 else title_raw,
                    "content": row.get("content", ""),
                    "rank": float(row.get("rank", 0)),
                })

        elif isinstance(reports_val, list):
            for entry in reports_val:
                title_raw = str(entry.get("title", ""))
                parts = title_raw.split("<sep>")
                processed_reports.append({
                    "index_name": parts[0] if len(parts) > 0 else self.config.index_name,
                    "index_id": parts[1] if len(parts) > 1 else "unknown",
                    "title": parts[2] if len(parts) > 2 else title_raw,
                    "content": entry.get("content", ""),
                    "rank": float(entry.get("rank", 0)),
                })

        result.context_data["reports"] = processed_reports

        # Make context_data serializable
        serializable_context: Dict[str, Any] = {}
        for key, value in result.context_data.items():
            if isinstance(value, pd.DataFrame):
                serializable_context[key] = value.to_dict(orient="records")
            elif isinstance(value, np.ndarray):
                serializable_context[key] = value.tolist()
            else:
                serializable_context[key] = value

        return result.response, serializable_context

    async def hybrid_query(self, query: str) -> Tuple[str, Dict[str, Any]]:
        # 1. Get GraphRAG Reports (Global Search)
        # We ignore the initial answer from global_search as we will regenerate it with hybrid context
        _, context_data = await self.global_query(query)
        reports = context_data.get("reports", []) if isinstance(context_data, dict) else []

        # 2. Get Raw Text
        raw_text = ""
        if self.env != "azure":
             raw_text = self._load_local_uploaded_text(self.config.index_name)
        
        # 3. Build Hybrid Context
        reports_block = self._format_context(reports)
        
        context_parts = []
        if reports_block.strip():
            context_parts.append(f"--- COMMUNITY REPORTS ---\n{reports_block}")
        
        if raw_text.strip():
            context_parts.append(f"--- RAW DOCUMENT CONTENT ---\n{raw_text}")
            
        combined_context = "\n\n".join(context_parts)
        
        if not combined_context.strip():
            return "I cannot find that in the uploaded document.", context_data

        # 4. Generate Answer
        system_prompt = (
            "You are IntelliGraph.\n"
            "The user has uploaded documents and you are provided with extracted CONTEXT from those documents.\n"
            "The CONTEXT is user-provided content which you have permission to use.\n"
            "You ARE allowed to quote exact sentences from the CONTEXT.\n"
            "You MUST only answer using the provided CONTEXT.\n\n"
            "If the user asks for an exact sentence or quote, you MUST quote the exact sentence from CONTEXT.\n"
            "Do NOT refuse due to copyright. treat the CONTEXT as if it is the user's own notes.\n"
            "If the answer is not in CONTEXT, say: I cannot find that in the uploaded document.\n"
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"CONTEXT\n{combined_context}\n\nQUESTION\n{query}"}
        ]
        
        # We need a reference to 'llm'. In global_query it matches local var. 
        # We should store self.llm in __init__? 
        # But global_query re-inits it every time. 
        # Let's just re-init or refactor global_query to store it.
        # Refactoring global_query is cleaner but risky. 
        # I'll just copy the LLM init logic for now to be safe and self-contained.
        
        # ... actually, I can just call global_query and grab the 'llm' object if I refactor global_query slightly.
        # But I can't easily change global_query signature without breaking other things.
        # I will COPY the LLM init logic here. It is small.
        
        if self.env != "azure":
            api_key = os.getenv("OPENAI_API_KEY")
            model_name = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
            llm = ChatOpenAI(
                api_base="https://api.openai.com/v1",
                model=model_name,
                api_type=OpenaiApiType.OpenAI,
                api_key=api_key,
                max_retries=10,
            )
        else:
             # simplistic azure fallback or error if azure not supported in hybrid yet
             # For now let's focus on local as per user request context
             cfg = self.config.get_config()
             llm_cfg = cfg.get("llm", {})
             llm = ChatOpenAI(
                api_base=llm_cfg["api_base"],
                model=llm_cfg.get("model"),
                api_type=OpenaiApiType.AzureOpenAI,
                deployment_name=llm_cfg.get("deployment_name"),
                api_version=llm_cfg.get("api_version"),
                api_key=llm_cfg.get("api_key"),
                max_retries=10,
            )

        response = await llm.agenerate(messages=messages)
        return response, context_data
