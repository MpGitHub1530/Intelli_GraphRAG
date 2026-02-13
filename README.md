# IntelliGraph: Multi-Agent Knowledge Discovery Platform



## 🖥 Application UI

![IntelliGraphRAG UI](assets/App.jpg)



### Author: Mittal Panchal

**IntelliGraph** is an advanced AI-powered knowledge discovery platform that bridges the gap between structured knowledge retrieval and unstructured text analysis. By combining **GraphRAG (Graph Retrieval-Augmented Generation)** with **Hybrid Context Strategies**, IntelliGraph empowers users to extract deep insights from complex document sets (PDFs, Markdown, Text) with high precision and reduced hallucinations.

---

## 🚀 Key Features

### 🧠 Hybrid Context Engine
- **GraphRAG Integration**: Constructs a knowledge graph from your documents to understand relationships, entities, and communities, going beyond simple keyword matching.
- **Raw Text Fallback**: Seamlessly combines graph-based insights with raw textual retrieval to ensure no detail is lost.
- **Context-Aware Responses**: Dynamically selects the best retrieval method based on query complexity.

### 💬 Intelligent Chat Interface
- **Context-Rich Conversations**: Chat with your documents as if they were a knowledgeable expert.
- **Citation Tracking**: Every claim is backed by clickable citations linked directly to the source document.
- **Refinement Capabilities**: iteratively refine answers for better clarity or depth.

### 📂 Document Management
- **Multi-Format Support**: Upload and index PDF, Markdown, and Text files.
- **Automated Indexing**: Files are automatically processed, chunked, and indexed for rapid retrieval.
- **Secure Handling**: Local-first architecture ensures your data stays under your control.

---

## 🛠️ Technology Stack

- **Frontend**: React, Styled Components (Cyberpunk/Neon Theme)
- **Backend**: Python, Flask, AsyncIO
- **AI/LLM**: OpenAI GPT models (configurable via environment)
- **Indexing**: GraphRAG, LanceDB (Vector Store)

---

## ⚡ Getting Started

### Prerequisites
- **Python 3.10+**
- **Node.js 16+**
- **OpenAI API Key**

### 1. Installation

Clone the repository:
```bash
git clone https://github.com/MpGitHub1530/Intelli_GraphRAG.git
cd intelligraph
```

### 2. Backend Setup

Create a virtual environment and install dependencies:
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file in the root directory:
```env
OPENAI_API_KEY=your_openai_api_key_here
GRAPHRAG_LLM_MODEL=gpt-4o
```

Start the backend server:
```bash
python main.py
```
*The server will start on `http://localhost:5000`*

### 3. Frontend Setup

Navigate to the frontend directory and install dependencies:
```bash
cd frontend
npm install
```

Start the React development server:
```bash
npm start
```
*The application will open at `http://localhost:3000`*

---

## 📖 Usage

1.  **Upload**: Go to the "Upload" tab and drag-and-drop your PDF or text documents.
2.  **Index**: The system will process the files. Wait for the "Indexing Completed" status.
3.  **Chat**: Switch to the "Chat" tab. Select your index and start asking questions!
    - *Example: "What are the key conclusions in this report?"*
    - *Example: "Compare the revenue growth between 2022 and 2023."*

---


📊 Benchmark Results

The system was evaluated on a structured 50 question benchmark covering:

- Entity extraction
- Relationship reasoning
- Community detection
- Cross document reasoning
- Exact sentence quoting
- Architecture understanding

## Configuration

- Index: test_1
- Retrieval Strategy: Hybrid Context (GraphRAG + Raw Text Grounding)
- LLM: GPT 4o mini
- Questions: 50 structured evaluation queries

## 📊 Benchmark Performance

| Metric | Value | Interpretation |
|--------|--------|----------------|
| Total Questions | 50 | Structured evaluation set |
| Accuracy | **88%** | Hybrid GraphRAG + Raw Text grounding |
| Retrieval Coverage | **100%** | Graph reports returned for every query |
| Average Latency | 10.71 seconds | Mean end-to-end query time |
| P95 Latency | 25.09 seconds | Worst-case latency under heavy context |


## 🤝 Contribution

Contributions are welcome! Please fork the repository and submit a pull request.

---

## 📄 License

This project is licensed under the MIT License.

---

*Built with ❤️ by Mittal Panchal*
