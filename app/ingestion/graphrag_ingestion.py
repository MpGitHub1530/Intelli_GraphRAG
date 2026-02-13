import logging
import time
import os

from graphrag.config import create_graphrag_config
from graphrag.index import create_pipeline_config
from graphrag.index.run import run_pipeline_with_config
from graphrag.index.progress import PrintProgressReporter

from app.integration.graphrag_config import GraphRagConfig

logger = logging.getLogger(__name__)


class GraphRagIngestion:
    def __init__(self, config: GraphRagConfig):
        self.config = config
        self.env = os.getenv("APP_ENV", "local")

    async def process(self):
        start_time = time.time()

        # Load config (local or azure decided inside GraphRagConfig)
        config_dict = self.config.get_config()

        logger.info(
            f"Starting GraphRAG ingestion | "
            f"index={self.config.index_name} | "
            f"env={self.env}"
        )

        parameters = create_graphrag_config(config_dict, ".")
        pipeline_config = create_pipeline_config(parameters, True)

        error_count = 0

        async for workflow_result in run_pipeline_with_config(
            config_or_path=pipeline_config,
            progress_reporter=PrintProgressReporter("Running GraphRAG pipeline"),
        ):
            if workflow_result.errors:
                error_count += len(workflow_result.errors)
                logger.error(
                    f"GraphRAG errors for index={self.config.index_name}: "
                    f"{workflow_result.errors}"
                )

        duration = round(time.time() - start_time, 2)

        if error_count == 0:
            logger.info(
                f"GraphRAG ingestion completed successfully | "
                f"index={self.config.index_name} | "
                f"env={self.env} | "
                f"duration={duration}s"
            )
        else:
            logger.warning(
                f"GraphRAG ingestion completed with errors | "
                f"index={self.config.index_name} | "
                f"errors={error_count} | "
                f"duration={duration}s"
            )

       
        metrics = {
            "index": self.config.index_name,
            "environment": self.env,
            "duration_seconds": duration,
            "errors": error_count,
            "files_processed": len([f for f in os.listdir(config_dict["input"]["base_dir"]) if os.path.isfile(os.path.join(config_dict["input"]["base_dir"], f))]),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        os.makedirs("metrics", exist_ok=True)
        metrics_path = f"metrics/{self.config.index_name}_ingestion_{int(time.time())}.json"
        with open(metrics_path, "w") as f:
            import json
            json.dump(metrics, f, indent=2)

        logger.info(f"Ingestion metrics saved to {metrics_path}")
