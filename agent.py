import json 
import logging 
import os 

load_dotenv()
main_api_key = os.environ.get("MAIN_API_KEY") 

base_prompt = """You are a helpful assistant that can answer questions based ONLY on the provided knowledge base.
                First, identify the exact problem that the user is facing and check it existing knowledge
                base for a solution. 
                If the problem is not found in the knowledge base, respond with "I don't know" and do not attempt to provide an answer based on your own knowledge or assumptions.
                """

knowledge_base = 


