# ui.py

import streamlit as st
import requests

st.set_page_config(page_title="Food MCP Demo", page_icon="🍎", layout="centered")

st.title("Food-Safety & Workout Demo")

st.write(
    "Ask about **recalls**, **nutrition**, or **workout ideas** based on food!"
)

query = st.text_input(
    "Your question", 
    placeholder="e.g., 'workout after eating pork' or 'nutrition for coca-cola'"
)

submit = st.button("Ask")

if submit and query.strip():
    with st.spinner("Processing your request..."):
        try:
            response = requests.post(
                "http://127.0.0.1:8000/query",
                json={"query": query},
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                response_type = data.get("type", "ai") # Default to "ai"
                
                if response_type == "plan":
                    # 1. More "human" title
                    st.subheader("Suggested Workout")
                    # 2. Use st.success() for the green box
                    st.success(data.get("response", "No plan generated."))
                else:
                    st.subheader("AI Response")
                    # Use st.info for standard AI answers (blue box)
                    st.info(data.get("response", "No response generated."))
                
                # --- Context is always shown in a blue box ---
                st.subheader("Context")
                st.info(data.get("context", "No context found."))

            else:
                st.error(f"Server returned {response.status_code}: {response.text}")
        
        except requests.exceptions.ConnectionError:
            st.error("❌ Could not connect to MCP server. Make sure it's running on port 8000.")
        except Exception as e:
            st.error(f"Unexpected error: {e}")