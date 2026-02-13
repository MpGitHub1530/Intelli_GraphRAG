import requests
import json
import os
import time

def run_evaluation(index_name, questions, is_restricted=False):
    print(f"Starting evaluation for index: {index_name}")
    results = []
    
    for q in questions:
        print(f"Querying: {q}")
        start_time = time.time()
        try:
            # Using the /ask endpoint as it returns a structured answer per question
            response = requests.post("http://localhost:5000/ask", json={
                "questions": [q],
                "indexName": index_name,
                "isRestricted": is_restricted,
                "useGraphRag": True
            })
            response.raise_for_status()
            data = response.json()
            
            answer = data.get("answers", [{}])[0].get("answer", "No answer found")
            duration = time.time() - start_time
            
            results.append({
                "question": q,
                "answer": answer,
                "duration_seconds": round(duration, 2),
                "status": "success"
            })
        except Exception as e:
            print(f"Error evaluating '{q}': {e}")
            results.append({
                "question": q,
                "error": str(e),
                "status": "failed"
            })

    # Save results
    os.makedirs("evaluation_results", exist_ok=True)
    output_path = f"evaluation_results/{index_name}_eval_{int(time.time())}.json"
    with open(output_path, "w") as f:
        json.dump({
            "index_name": index_name,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "results": results
        }, f, indent=2)
    
    print(f"Evaluation results saved to {output_path}")

if __name__ == "__main__":
    # Example questions
    test_questions = [
        "What are the main topics discussed in these documents?",
        "Summarize the key findings.",
        "Check for any conflicting information."
    ]
    
    # Replace with a valid index name from your local knowledgebase
    target_index = os.getenv("EVAL_INDEX", "demo")
    
    # Note: Backend must be running
    try:
        run_evaluation(target_index, test_questions)
    except Exception as e:
        print(f"Evaluation script failed: {e}. Is the backend running at http://localhost:5000?")
