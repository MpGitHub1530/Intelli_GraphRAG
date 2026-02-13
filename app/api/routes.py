from fileinput import filename
from typing import Tuple
from flask import Flask, request, jsonify, Response, send_file, current_app
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
import os
import asyncio
from io import BytesIO
import logging
import threading
import nest_asyncio
nest_asyncio.apply()
from azure.core.exceptions import ResourceNotFoundError

from app.ingestion.graphrag_ingestion import GraphRagIngestion
from app.integration.graphrag_config import GraphRagConfig
from app.integration.identity import get_user_id, easyauth_enabled
from app.query.chat_service import chat_with_data, refine_message


# Azure-only imports (kept)
from app.integration.blob_service import (
    upload_file_to_lz, create_index_containers, list_files_in_container,
    delete_file_from_blob, list_indexes, delete_index, initialize_blob_service
)
from app.integration.index_manager import (
    create_index_manager, ContainerNameTooLongError, IndexConfig
)
from app.ingestion.indexing_queue import queue_indexing_job
from app.ingestion.ingestion_job import check_job_status, delete_ingestion_index

logger = logging.getLogger(__name__)


def are_operations_restricted():
    return os.getenv("RESTRICT_OPERATIONS", "false").lower() == "true"


class RouteConfigurator:
    def __init__(self, app: Flask, socketio: SocketIO):
        self.app = app
        self.socketio = socketio
        self.env = os.getenv("APP_ENV", "local").lower()
        self.operations_restricted = are_operations_restricted()

        # Azure
        self.blob_service = initialize_blob_service() if self.env == "azure" else None

        # Local
        self.local_root = os.path.abspath("knowledgebase")
        os.makedirs(self.local_root, exist_ok=True)
        self._local_index_status = {}

    # ---------------- PUBLIC ----------------

    def configure_routes(self) -> Flask:
        self.app.route("/config", methods=["GET"])(self._get_config)
        self.app.route("/indexes", methods=["GET"])(self._get_indexes)
        self.app.route("/indexes", methods=["POST"])(self._create_index)
        self.app.route("/indexes/<index_name>", methods=["DELETE"])(self._remove_index)

        self.app.route("/indexes/<index_name>/upload", methods=["POST"])(self._upload_file)
        self.app.route("/indexes/<index_name>/files", methods=["GET"])(self._list_files)
        self.app.route("/indexes/<index_name>/index", methods=["POST"])(self._index_files)
        self.app.route("/indexes/<index_name>/index/status", methods=["GET"])(self._check_index_status)

        self.app.route("/chat", methods=["POST"])(self._chat)

        self.app.route("/refine", methods=["POST"])(self._refine)

        return self.app

        return self.app

    # ---------------- CONFIG ----------------

    def _get_config(self):
        return jsonify({
            "operationsRestricted": self.operations_restricted,
            "easyAuthEnabled": easyauth_enabled(request),
            "environment": self.env
        }), 200

    # ---------------- INDEXES ----------------

    def _get_indexes(self):
        user_id = get_user_id(request)

        if self.env != "azure":
            indexes = [d for d in os.listdir(self.local_root)
                       if os.path.isdir(os.path.join(self.local_root, d))]
            return jsonify({"indexes": sorted(indexes)}), 200

        return jsonify({"indexes": list_indexes(user_id)}), 200

    def _create_index(self):
        if self.operations_restricted:
            return jsonify({"error": "Operation not allowed"}), 403

        data = request.get_json()
        index_name = data.get("name")
        if not index_name:
            return jsonify({"error": "Index name required"}), 400

        if self.env != "azure":
            os.makedirs(os.path.join(self.local_root, index_name), exist_ok=True)
            return jsonify({"message": "Index created", "index": index_name}), 201

        user_id = get_user_id(request)
        try:
            create_index_manager(user_id, index_name, True)
            containers = create_index_containers(user_id, index_name, True)
            return jsonify({"message": "Index created", "containers": containers}), 201
        except ContainerNameTooLongError as e:
            return jsonify({"error": str(e)}), 400

    def _remove_index(self, index_name: str):
        if self.operations_restricted:
            return jsonify({"error": "Operation not allowed"}), 403

        if self.env != "azure":
            path = os.path.join(self.local_root, index_name)
            if not os.path.isdir(path):
                return jsonify({"error": "Index not found"}), 404
            for root, dirs, files in os.walk(path, topdown=False):
                for f in files:
                    os.remove(os.path.join(root, f))
                for d in dirs:
                    os.rmdir(os.path.join(root, d))
            os.rmdir(path)
            self._local_index_status.pop(index_name, None)
            return jsonify({"message": "Index deleted"}), 200

        user_id = get_user_id(request)
        delete_index(user_id, index_name, True)
        delete_ingestion_index(index_name)
        return jsonify({"message": "Index deleted"}), 200

    # ---------------- FILES ----------------

    def _upload_file(self, index_name: str):
        if self.operations_restricted:
            return jsonify({"error": "Operation not allowed"}), 403

        if "file" not in request.files:
            return jsonify({"error": "No file"}), 400

        file = request.files["file"]
        filename = secure_filename(file.filename)
        data = file.read()

        pages = 0
        is_pdf = filename.lower().endswith(".pdf")

        if is_pdf:
            try:
                pages = get_pdf_page_count(BytesIO(data))
            except Exception:
                logger.exception("PDF page count failed")
                pages = 0

        if self.env != "azure":
            path = os.path.join(self.local_root, index_name)
            if not os.path.isdir(path):
                return jsonify({"error": "Index not found"}), 404

            with open(os.path.join(path, filename), "wb") as f:
                f.write(data)

            return jsonify({"message": "File uploaded", "pages": pages}), 201

        user_id = get_user_id(request)
        buf = BytesIO(data)
        upload_file_to_lz(buf, filename, user_id, index_name, True, self.blob_service)
        return jsonify({"message": "File queued", "pages": pages}), 202


    def _list_files(self, index_name: str):
        if self.env != "azure":
            path = os.path.join(self.local_root, index_name)
            if not os.path.isdir(path):
                return jsonify({"error": "Index not found"}), 404
            files = [{"filename": f} for f in os.listdir(path)
                     if os.path.isfile(os.path.join(path, f))]
            return jsonify({"files": files}), 200

        user_id = get_user_id(request)
        index_manager = create_index_manager(user_id, index_name, True)
        files = list_files_in_container(index_manager.get_reference_container(), self.blob_service)
        return jsonify({"files": files}), 200

    # ---------------- INGESTION ----------------

    def _index_files(self, index_name: str):
        user_id = get_user_id(request)

        if self.env != "azure":
            if index_name in self._local_index_status and self._local_index_status[index_name]["status"] == "in_progress":
                return jsonify({"error": "Indexing already in progress"}), 400

            self._local_index_status[index_name] = {"status": "in_progress", "progress": 0}

            
            # Check if there are files
            path = os.path.join(self.local_root, index_name)
            has_files = any(f.lower().endswith(('.txt', '.md', '.pdf')) for f in os.listdir(path))
            if not has_files:
                self._local_index_status.pop(index_name)
                return jsonify({"error": "No text files found in knowledgebase."}), 400

            def run_indexing():
                try:
                    logger.info(f"Starting background indexing for {index_name}")
                    cfg = GraphRagConfig(index_name, user_id, True)
                    asyncio.run(GraphRagIngestion(cfg).process())
                    self._local_index_status[index_name] = {"status": "completed", "progress": 100}
                    logger.info(f"Background indexing completed for {index_name}")
                except Exception as e:
                    logger.exception(f"Local indexing failed for {index_name}: {str(e)}")
                    self._local_index_status[index_name] = {"status": "failed", "error": str(e)}

            threading.Thread(target=run_indexing, daemon=True).start()
            return jsonify({"status": "initiated", "message": "Indexing started in background"}), 202

        index_manager = create_index_manager(user_id, index_name, True)
        queue_indexing_job(index_manager.get_ingestion_container(), user_id, index_name, True)
        return jsonify({"status": "initiated"}), 202

    def _check_index_status(self, index_name: str):
        if self.env != "azure":
            return jsonify(self._local_index_status.get(
                index_name, {"status": "not_started"}
            )), 200

        user_id = get_user_id(request)
        index_manager = create_index_manager(user_id, index_name, True)
        return jsonify(check_job_status(index_manager.get_ingestion_container())), 200

    # ---------------- QUERY ----------------

    def _chat(self):
        return chat_with_data(request.json, get_user_id(request))


    
    def _refine(self):
        return refine_message(request.json, get_user_id(request))


def configure_routes(app: Flask, socketio: SocketIO, **kwargs) -> Flask:
    return RouteConfigurator(app, socketio).configure_routes()
