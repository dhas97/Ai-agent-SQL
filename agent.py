import pandas as pd
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from config import OPENAI_MODEL
from sql_prompt import SYSTEM_PROMPT
from tools import TOOLS


llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, max_tokens=512)

agent = create_agent(
    model=llm,
    tools=TOOLS,
    system_prompt=SYSTEM_PROMPT,
)


def run_agent(question: str):

    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    messages = result["messages"]

    # Last successful SQL execution: its query and result table
    sql, data = "", pd.DataFrame()

    for message in messages:
        if (
            isinstance(message, ToolMessage)
            and message.name in ("run_sql_query", "call_procedure")
            and isinstance(message.artifact, pd.DataFrame)
        ):
            data = message.artifact
            sql = _find_query(messages, message.tool_call_id)

    return {
        "answer": messages[-1].content,
        "sql": sql,
        "data": data,
    }


def _find_query(messages, tool_call_id: str) -> str:

    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["id"] == tool_call_id:
                    args = call["args"]
                    if "query" in args:
                        return args["query"]
                    return f"CALL {args.get('name')}({', '.join(map(repr, args.get('args') or []))})"

    return ""
