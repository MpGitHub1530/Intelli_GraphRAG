import os
import json
import time
import asyncio
import statistics
from typing import Any, Dict, List, Tuple, Optional

# We import inside functions so the script can still show helpful errors
# even if graphrag is missing or venv is not active.


def load_questions(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"benchmark file not found {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Allow legacy format list of strings but normalize it
    if isinstance(data, list) and all(isinstance(x, str) for x in data):
        normalized = []
        for i, q in enumerate(data, start=1):
            normalized.append({"id": f"q{i:02d}", "question": q, "expected_contains": []})
        data = normalized

    validate_questions(data)
    return data


def validate_questions(data: Any) -> None:
    if not isinstance(data, list):
        raise ValueError("benchmark_questions json must be a list")

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"entry {i} must be an object")
        if "question" not in item or not isinstance(item["question"], str) or not item["question"].strip():
            raise ValueError(f"entry {i} missing valid question")
        if "id" in item and not isinstance(item["id"], str):
            raise ValueError(f"entry {i} id must be a string")
        if "expected_contains" in item and not isinstance(item["expected_contains"], list):
            raise ValueError(f"entry {i} expected_contains must be a list")
        if "expected_contains" in item:
            for j, s in enumerate(item["expected_contains"]):
                if not isinstance(s, str):
                    raise ValueError(f"entry {i} expected_contains item {j} must be a string")


def resolve_index_name() -> str:
    # default to benchmark but you can override using env var
    return os.getenv("BENCHMARK_INDEX", "benchmark")


def resolve_user_id() -> str:
    # local mode does not truly use user id for auth in your setup
    return os.getenv("BENCHMARK_USER_ID", "local_user")


def resolve_is_restricted() -> bool:
    v = os.getenv("BENCHMARK_IS_RESTRICTED", "false").lower().strip()
    return v in ["1", "true", "yes"]


def ensure_output_ready(index_name: str) -> None:
    # GraphRagQuery local mode expects these parquet files here
    # output/<index_name>/create_final_nodes.parquet
    # output/<index_name>/create_final_community_reports.parquet
    base = os.path.abspath(os.path.join("output", index_name))
    nodes_path = os.path.join(base, "create_final_nodes.parquet")
    reports_path = os.path.join(base, "create_final_community_reports.parquet")

    missing = []
    if not os.path.exists(nodes_path):
        missing.append(nodes_path)
    if not os.path.exists(reports_path):
        missing.append(reports_path)

    if missing:
        msg = (
            "missing GraphRAG output files\n"
            + "\n".join(missing)
            + "\nrun indexing for this index then retry"
        )
        raise FileNotFoundError(msg)


async def run_graphrag_query(question: str, user_id: str, index_name: str, is_restricted: bool) -> Tuple[str, Dict[str, Any]]:
    # Import here so we can show clean errors if module not found
    from app.integration.graphrag_config import GraphRagConfig
    from app.query.graphrag_query import GraphRagQuery

    cfg = GraphRagConfig(index_name, user_id, is_restricted)
    graph = GraphRagQuery(cfg)
    # Use hybrid_query to test improved logic
    answer, context = await graph.hybrid_query(question)
    if context is None:
        context = {}
    return answer or "", context


def score_expected_contains(answer: str, expected: List[str]) -> Optional[bool]:
    # If no expected phrases provided then do not score automatically
    if not expected:
        return None
    a = (answer or "").lower()
    ok = True
    for phrase in expected:
        if phrase.lower() not in a:
            ok = False
            break
    return ok


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


async def main() -> None:
    questions_path = os.path.join("benchmarks", "benchmark_questions.json")
    out_path = os.path.join("benchmarks", "results.json")

    qs = load_questions(questions_path)

    index_name = resolve_index_name()
    user_id = resolve_user_id()
    is_restricted = resolve_is_restricted()

    ensure_output_ready(index_name)

    details: List[Dict[str, Any]] = []
    latencies: List[float] = []
    auto_scores: List[bool] = []
    retrieval_hits: List[bool] = []

    for item in qs:
        qid = item.get("id", "")
        question = item["question"]
        expected = item.get("expected_contains", [])

        print(f"Running {qid} {question}")

        t0 = time.perf_counter()
        answer, context = await run_graphrag_query(question, user_id, index_name, is_restricted)
        t1 = time.perf_counter()

        latency = round(t1 - t0, 3)
        latencies.append(latency)

        reports = []
        if isinstance(context, dict):
            reports = context.get("reports", [])
        hit = bool(reports) and len(reports) > 0
        retrieval_hits.append(hit)

        auto = score_expected_contains(answer, expected)
        if auto is not None:
            auto_scores.append(bool(auto))

        details.append(
            {
                "id": qid,
                "question": question,
                "latency_seconds": latency,
                "retrieval_reports_count": len(reports) if isinstance(reports, list) else 0,
                "auto_scored": auto is not None,
                "correct": auto,
                "expected_contains": expected,
                "answer_preview": (answer[:300] + " ...") if len(answer) > 300 else answer,
            }
        )

    avg_latency = round(statistics.mean(latencies), 3) if latencies else 0.0
    p95_latency = round(percentile(latencies, 0.95), 3)

    accuracy = None
    if auto_scores:
        accuracy = round(sum(1 for x in auto_scores if x) / len(auto_scores), 3)

    retrieval_coverage = round(sum(1 for x in retrieval_hits if x) / len(retrieval_hits), 3) if retrieval_hits else 0.0

    results = {
        "index_name": index_name,
        "count": len(qs),
        "accuracy": accuracy,
        "avg_latency_seconds": avg_latency,
        "p95_latency_seconds": p95_latency,
        "retrieval_coverage": retrieval_coverage,
        "details": details,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved results to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
