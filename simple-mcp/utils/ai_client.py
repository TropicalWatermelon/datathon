import os
from google import genai
from dotenv import load_dotenv

# Load the .env file
load_dotenv()

# Initialize Gemini client
# The client automatically looks for the GEMINI_API_KEY environment variable.
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def ask_ai(query, context):
    """
    Sends a query and context to the Gemini model for a grounded answer.
    """
    # Construct the prompt for grounded Q&A
    prompt = (
        f"CONTEXT:\n{context}\n\n"
        f"USER QUERY:\n{query}\n\n"
        "Answer based *only* on the context above."
    )

    # Call the Gemini API
    response = client.models.generate_content(
        model="gemini-2.5-flash", # A powerful and fast model for general tasks
        contents=prompt
    )

    # The response object is structured differently,
    # the text is directly accessible via the .text attribute.
    return response.text

# --- Example Usage (Assuming you have a GEMINI_API_KEY in your .env file) ---
# context_data = "The capital of France is Paris. It is known for the Eiffel Tower."
# user_question = "What is the capital of France?"
# answer = ask_ai(user_question, context_data)
# print(answer)