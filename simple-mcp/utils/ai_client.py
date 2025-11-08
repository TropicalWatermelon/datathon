import os
from openai import OpenAI
from dotenv import load_dotenv

# Load the .env file
load_dotenv()

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ask_ai(query, context):
    prompt = f"CONTEXT:\n{context}\n\nUSER QUERY:\n{query}\n\nAnswer based on the context above."

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
