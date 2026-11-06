import streamlit as st

from agent import run_agent


st.set_page_config(
    page_title="AI Data Agent",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 AI Data Agent")

st.write(
    "Ask questions about your database "
    "using natural language."
)


question = st.text_input(
    "Enter your question",
    placeholder="Show top 5 employees by salary"
)


if st.button("Generate Results"):

    if not question.strip():

        st.warning("Please enter a question.")

    else:

        with st.spinner("AI Agent is working..."):

            try:

                response = run_agent(question)

                st.subheader("Answer")

                st.write(response["answer"])

                st.subheader("Generated SQL")

                st.code(
                    response["sql"],
                    language="sql"
                )

                st.subheader("Query Results")

                st.dataframe(
                    response["data"],
                    use_container_width=True
                )

                st.success(
                    f"Found {len(response['data'])} records."
                )

            except Exception as e:

                st.error(f"Error: {str(e)}")