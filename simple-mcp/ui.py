import streamlit as st
import requests

st.set_page_config(page_title="Simple MCP Demo", page_icon="🤖", layout="centered")

st.title("🤖 Model Context Protocol Demo")
st.write("Ask me about **weather** or **news**, and I’ll fetch live context before answering!")

query = st.text_input("Your question", placeholder="e.g., What’s the weather in Austin?")
submit = st.button("Ask")

if submit and query.strip():
    with st.spinner("Fetching context and generating response..."):
        try:
            response = requests.post(
                "http://127.0.0.1:8000/query",
                json={"query": query},
                timeout=20
            )
            if response.status_code == 200:
                data = response.json()
                st.subheader("🧠 Context")
                st.info(data.get("context", "No context found."))

                st.subheader("💬 AI Response")
                st.success(data.get("response", "No response generated."))
            else:
                st.error(f"Server returned {response.status_code}: {response.text}")
        except requests.exceptions.ConnectionError:
            st.error("❌ Could not connect to MCP server. Make sure it's running on port 8000.")
        except Exception as e:
            st.error(f"Unexpected error: {e}")
