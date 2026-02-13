import os
import json
import base64
from typing import Any, Dict, List, Iterator, Optional

import requests
from flask import Response, jsonify, stream_with_context

from app.integration.azure_aisearch import create_data_source
from app.integration.azure_openai import create_payload, get_openai_config, stream_response
from app.integration.blob_service import initialize_blob_service
from app.integration.index_manager import ContainerNameTooLongError, create_index_manager

from app.integration.graphrag_config import GraphRagConfig
from app.query.graphrag_query import GraphRagQuery


# -----------------------------
# Helpers
# -----------------------------

def _safe_get_last_user_text(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                return c
    return ""


def _format_graphrag_context(reports: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    if not reports:
        return ""

    try:
        reports_sorted = sorted(reports, key=lambda r: float(r.get("rank", 0)), reverse=True)
    except Exception:
        reports_sorted = reports

    chunks: List[str] = []
    total = 0

    for i, r in enumerate(reports_sorted[:6], start=1):
        title = str(r.get("title", "report"))
        idx = str(r.get("index_id", "unknown"))
        rank = r.get("rank", "")
        content = str(r.get("content", "")).strip()

        block = f"[Report {i}] Title: {title} | Id: {idx} | Rank: {rank}\n{content}\n"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                chunks.append(block[:remaining] + "\n")
            break

        chunks.append(block)
        total += len(block)

    return "\n".join(chunks).strip()


def _build_local_system_prompt() -> str:
    return (
       "You are IntelliGraph.\n\n"
        "The user has uploaded documents and you are provided with extracted CONTEXT from those documents.\n"
        "The CONTEXT is user-provided content which you have permission to use.\n"
        "You ARE allowed to quote exact sentences from the CONTEXT.\n"
        "You MUST only answer using the provided CONTEXT.\n\n"
        "If the user asks for an exact sentence or quote, you MUST quote the exact sentence from CONTEXT.\n"
        "Do NOT refuse due to copyright. treat the CONTEXT as if it is the user's own notes.\n"
        "If the answer is not in CONTEXT, say: I cannot find that in the uploaded document.\n"
    )

def _load_local_uploaded_text(index_name: str, max_chars: int = 15000) -> str:
    """
    Fallback loader: reads raw uploaded .txt or .md files
    from knowledgebase/<index_name>/ and returns combined text.
    """
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

# -----------------------------
# OpenAI local streaming SSE
# -----------------------------

def _openai_stream_sse(messages: List[Dict[str, Any]]) -> Iterator[str]:
    """
    Streams OpenAI Chat Completions in SSE format compatible with ChatSection.js.

    We DO NOT emit [DONE] here anymore.
    The wrapper will emit it once at the end.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        yield 'data: {"choices":[{"delta":{"content":"OPENAI_API_KEY is missing"}}]}\n\n'
        return

    payload = {
        "model": os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
        "messages": messages,
        "temperature": 0.0,
        "stream": True,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        with requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            stream=True,
            timeout=180,
        ) as r:
            if r.status_code != 200:
                try:
                    err = r.json()
                except Exception:
                    err = {"error": r.text}
                yield f"data: {json.dumps({'choices':[{'delta':{'content': 'OpenAI error: ' + json.dumps(err)}}]})}\n\n"
                return

            for raw_line in r.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                line = raw_line.strip()
                if not line.startswith("data: "):
                    continue

                data = line[6:].strip()
                if data == "[DONE]":
                    break

                yield f"data: {data}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'choices':[{'delta':{'content': 'OpenAI stream failed: ' + str(e)}}]})}\n\n"


def _append_sources_stream(base_stream: Iterator[str], reports: List[Dict[str, Any]]) -> Iterator[str]:
    """
    Pass-through OpenAI stream, then append Sources list as one extra delta chunk, then DONE.
    """
    titles: List[str] = []
    for r in (reports or [])[:6]:
        t = str(r.get("title", "")).strip()
        if t:
            titles.append(t)

    # 1) stream normal answer
    for chunk in base_stream:
        yield chunk

    # 2) append sources
    if titles:
        sources_text = "\n\nSources\n" + "\n".join([f"{i+1}. {t}" for i, t in enumerate(titles)]) + "\n"
        yield f"data: {json.dumps({'choices':[{'delta':{'content': sources_text}}]})}\n\n"

    # 3) final done
    yield "data: [DONE]\n\n"


# -----------------------------
# Public API
# -----------------------------

def chat_with_data(data: Dict[str, Any], user_id: str, config: Optional[Dict[str, str]] = None) -> Response:
    messages = data.get("messages", [])
    context = data.get("context", {})
    session_state = data.get("session_state", {})
    index_name = data.get("index_name")
    is_restricted = data.get("is_restricted", True)

    if not messages or not index_name:
        return jsonify({"error": "Messages and index name are required"}), 400

    try:
        index_manager = create_index_manager(user_id, index_name, is_restricted)
    except ContainerNameTooLongError as e:
        return jsonify({"error": str(e)}), 400

    if not index_manager.user_has_access():
        return jsonify({"error": "Unauthorized access"}), 403

    env = os.getenv("APP_ENV", "local").lower()

    # ---------------- LOCAL MODE ----------------
    if env != "azure":
        user_question = _safe_get_last_user_text(messages)
        # 1. Always load raw text (up to 20k chars)
        raw_text = _load_local_uploaded_text(index_name, max_chars=20000)

        reports: List[Dict[str, Any]] = []
        try:
            cfg = GraphRagConfig(index_name, user_id, is_restricted)
            graph = GraphRagQuery(cfg)

            import asyncio
            try:
                _answer_text, context_data = asyncio.run(graph.global_query(user_question))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                _answer_text, context_data = loop.run_until_complete(graph.global_query(user_question))
                loop.close()

            reports = context_data.get("reports", []) if isinstance(context_data, dict) else []
        except Exception as e:
            # Log error but allow fallback to raw text
            print(f"GraphRAG retrieval failed: {str(e)}")
            reports = []

        reports_block = _format_graphrag_context(reports, max_chars=10000)

        # Hybrid Context Construction
        context_parts = []
        if reports_block.strip():
            context_parts.append(f"--- COMMUNITY REPORTS ---\n{reports_block}")
        
        if raw_text.strip():
            context_parts.append(f"--- RAW DOCUMENT CONTENT ---\n{raw_text}")
            
        context_block = "\n\n".join(context_parts)


        system_msg = {"role": "system", "content": _build_local_system_prompt()}
        ctx_msg = {
            "role": "user",
            "content": (
                "CONTEXT\n"
                f"{context_block}\n\n"
                "QUESTION\n"
                f"{user_question}\n\n"
                "INSTRUCTIONS\n"
                "Answer using ONLY CONTEXT. If the user asks for an exact quote, quote exact sentences from CONTEXT."
            ),
        }

        openai_messages = [system_msg, ctx_msg]

        base = _openai_stream_sse(openai_messages)
        wrapped = _append_sources_stream(base, reports)

        return Response(
            stream_with_context(wrapped),
            content_type="text/event-stream",
        )

    # ---------------- AZURE MODE ----------------
    if config is None:
        config = get_openai_config()

    container_name = index_manager.get_ingestion_container()

    url = (
        f"{config['OPENAI_ENDPOINT']}/openai/deployments/"
        f"{config['AZURE_OPENAI_DEPLOYMENT_ID']}/chat/completions?api-version=2024-02-15-preview"
    )
    headers = {"Content-Type": "application/json", "api-key": config["AOAI_API_KEY"]}

    data_source = create_data_source(
        config["SEARCH_SERVICE_ENDPOINT"],
        config["SEARCH_SERVICE_API_KEY"],
        container_name,
    )
    payload = create_payload(messages, context, session_state, [data_source], True)
    return stream_response(url, headers, payload)


def refine_message(data: Dict[str, Any], user_id: str, config: Optional[Dict[str, str]] = None) -> Response:
    message = data.get("message")
    citations = data.get("citations", [])
    index_name = data.get("index_name")
    is_restricted = data.get("is_restricted", True)
    original_question = data.get("original_question")

    if not citations:
        return jsonify({"error": "Citations are required"}), 400

    if not message or not index_name or not original_question:
        return jsonify({"error": "Message, index name, and original question are required"}), 400

    try:
        index_manager = create_index_manager(user_id, index_name, is_restricted)
    except ContainerNameTooLongError as e:
        return jsonify({"error": str(e)}), 400

    if not index_manager.user_has_access():
        return jsonify({"error": "Unauthorized access"}), 403

    reference_container = index_manager.get_reference_container()

    if config is None:
        config = get_openai_config()

    url = (
        f"{config['OPENAI_ENDPOINT']}/openai/deployments/"
        f"{config['AZURE_OPENAI_DEPLOYMENT_ID']}/chat/completions?api-version=2024-02-15-preview"
    )
    headers = {"Content-Type": "application/json", "api-key": config["AOAI_API_KEY"]}

    refine_messages = create_refine_messages(message, citations, reference_container, original_question)
    payload = create_payload(refine_messages, {}, {}, [], True)
    return stream_response(url, headers, payload)


def create_refine_messages(
    message: str,
    citations: List[Dict[str, Any]],
    reference_container: str,
    original_question: str,
) -> List[Dict[str, Any]]:
    system_message = (
        "You are an AI assistant tasked with answering specific questions based on "
        "additional visual information from documents. Only answer the question provided "
        "based on the information found in the documents. Do not provide new information. "
        "If the answer can't be found in the documents, answer 'No further information found'. "
        f"You must answer the question: {message}"
    )

    refine_messages: List[Dict[str, Any]] = [{"role": "system", "content": system_message}]

    blob_service_client = initialize_blob_service()
    container_client = blob_service_client.get_container_client(reference_container)

    for citation in citations:
        image_message = process_citation(citation, container_client)
        if image_message:
            refine_messages.append(image_message)

    refine_messages.append(
        {"role": "assistant", "content": f"OK - I am now going to answer the question: {original_question}"}
    )
    return refine_messages


def process_citation(citation: Dict[str, Any], container_client: Any) -> Dict[str, Any] | None:
    filepath = citation.get("filepath", "")
    if not filepath:
        return None

    parts = filepath.split("___")
    base_filename = parts[0]
    page_number = parts[1].split(".")[0].replace("Page", "") if len(parts) > 1 else "1"

    png_filename = f"{base_filename}___Page{page_number}.png"

    try:
        blob_client = container_client.get_blob_client(png_filename)
        image_data = blob_client.download_blob().readall()
        base64_image = base64.b64encode(image_data).decode("utf-8")

        return {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Image for {base_filename} (Page {page_number}):"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
            ],
        }
    except Exception:
        return None
