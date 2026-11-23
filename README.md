# deja-fix


### Problem Statement
Customer support teams frequently encounter repeated problems. However, solutions to previously resolved issues are often buried in old conversations, resulting in repetitive work, longer resolution times, and inefficient knowledge management.

### Proposed Solution
DejaFix is an AI-powered customer support assistant designed to capture, organize, and reuse solutions from previous customer interactions.

Rather than treating every customer query as a new problem, DejaFix aims to build a reusable knowledge base from resolved conversations.

### Key Features
- **AI-powered assistance:** Uses an LLM to help generate responses to customer queries.
- **Knowledge extraction:** Identifies potentially reusable solutions from conversations.
- **Knowledge repository:** Stores extracted solutions for future retrieval.
- **Conversation persistence:** Uses SQLite for storing conversation-related information.

### Technology Stack
- Python
- Streamlit
- SQLite
- Gemini API (free-tier access)


### Current Status
DejaFix is a prototype demonstrating the concept of AI-assisted customer support and reusable organizational knowledge. Some advanced features remain under development.
Running Locally

Clone the repository:

git clone YOUR_GITHUB_REPOSITORY_URL
cd YOUR_PROJECT_FOLDER

Install dependencies:

pip install -r requirements.txt

Configure the required

