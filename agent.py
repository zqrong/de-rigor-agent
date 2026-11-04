import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Literal, Protocol, TypedDict, cast

from dotenv import load_dotenv

from langchain.chat_models import init_chat_model
from langchain.messages import (
    AnyMessage,
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.messages import ChatMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

class DeveloperMessage(ChatMessage):
    """Message containing application-level developer instructions."""

    role: Literal["developer"] = "developer"


ModelProvider = Literal["openai"]
CodeLanguage = Literal["python", "R"]
DEMethod = Literal[
    "wilcoxon",
    "DESeq2",
    "edgeR",
    "limma-voom",
    "limma-no-voom",
    "NOISeq",
]

LLM_MODELS: Final[dict[str, tuple[ModelProvider, str]]] = {
    "openai/GPT-6 Luna": ("openai", "gpt-6-luna"),
    "openai/GPT-5.6 Sol": ("openai", "gpt-5.6-sol"),
    "openai/GPT-5.6 Terra": ("openai", "gpt-5.6-terra"),
    "openai/GPT-5.6 Luna": ("openai", "gpt-5.6-luna"),
}
# Display name (key of LLM_MODELS) used by the web app and CLI by default
DEFAULT_LLM_MODEL: Final[str] = "openai/GPT-6 Luna"

# Pricing per 1M tokens in USD
MODEL_PRICING: Final[dict[str, dict[str, float]]] = {
    "gpt-6-luna": {
        "input": 0.1,
        "output": 0.5,
        "cache_read": 0.01,
        "cache_write": 0.125,
    },
    "gpt-5.6-sol": {
        "input": 5.0,
        "output": 30.0,
        "cache_read": 0.5,
        "cache_write": 6.25,
    },
    "gpt-5.6-terra": {
        "input": 2.0,
        "output": 12.0,
        "cache_read": 0.2,
        "cache_write": 2.5,
    },
    "gpt-5.6-luna": {
        "input": 0.2,
        "output": 1.2,
        "cache_read": 0.02,
        "cache_write": 0.25,
    },
}

LONG_CONTEXT_MODELS: Final[frozenset[str]] = frozenset(
    {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
)
LONG_CONTEXT_TOKEN_THRESHOLD: Final[int] = 272_000


class RunCodeResult(TypedDict):
    stdout: str
    stderr: str
    exit_code: int
    message: str


class DEAgentState(MessagesState):
    user_message: str
    user_input_files: list[str]


class DEAgentEventHandler(Protocol):
    async def display_task_list(self) -> None: ...

    async def update_task_list(
        self, completed_task_index: int, langchain_msg_id: str | None
    ) -> None: ...

    async def prompt_loaded(self, prompt: str) -> None: ...

    async def thinking_started(self) -> None: ...

    async def thinking_finished(self, reasoning: str | None) -> None: ...

    async def code_snippet_executed(
        self, language: CodeLanguage, code: str, observation: RunCodeResult
    ) -> None: ...

    async def ask_user(self, question: str) -> str: ...

    async def ask_de_method_approval(self) -> tuple[bool, str | None]: ...

    async def notify(self, message: str) -> None: ...

    async def subagent_started(self, de_method: str) -> None: ...

    async def subagent_finished(self, de_method: str) -> None: ...


class NoOpDEAgentEventHandler:
    async def display_task_list(self) -> None:
        return None

    async def update_task_list(
        self, completed_task_index: int, langchain_msg_id: str | None
    ) -> None:
        return None

    async def prompt_loaded(self, prompt: str) -> None:
        return None

    async def thinking_started(self) -> None:
        return None

    async def thinking_finished(self, reasoning: str | None) -> None:
        return None

    async def code_snippet_executed(
        self, language: CodeLanguage, code: str, observation: RunCodeResult
    ) -> None:
        return None

    async def ask_user(self, question: str) -> str:
        return ""

    async def ask_de_method_approval(self) -> tuple[bool, str | None]:
        return True, None

    async def notify(self, message: str) -> None:
        print(message)

    async def subagent_started(self, de_method: str) -> None:
        return None

    async def subagent_finished(self, de_method: str) -> None:
        return None


def load_prompt_template(template_name: str, **kwargs: object) -> str:
    # Get the directory containing this file (agent.py)
    agent_dir = Path(__file__).resolve().parent
    template_path = agent_dir / "prompts" / f"{template_name}.md"
    with template_path.open("r", encoding="utf-8") as f:
        template = f.read()
    if kwargs:
        return template.format(**kwargs)
    else:
        return template


def _truncate_output(text: str, max_len: int = 2000) -> tuple[str, bool]:
    if len(text) <= max_len:
        return text, False
    half = max_len // 2
    truncated = (
        f"[Output truncated: showing first {half} and last {half} characters]\n\n"
        f"{text[:half]}\n... [middle omitted] ...\n{text[-half:]}"
    )
    return truncated, True


async def _run_code_snippet_local(
    language: CodeLanguage, code: str, local_working_dir: Path
) -> RunCodeResult:
    if language == "python":
        args = [sys.executable, "-c", code]
    elif language == "R":
        args = ["Rscript", "-e", code]
    else:
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "message": "Unsupported language.",
        }

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=local_working_dir,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600.0)
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()
        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "exit_code": process.returncode or 0,
            "message": "Execution completed successfully.",
        }
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "message": "Execution timed out or failed.",
        }


class DEAgent:

    def __init__(
        self,
        model_name: str,
        provider: ModelProvider,
        local_working_dir: Path | None = None,
        event_handler: DEAgentEventHandler | None = None,
    ) -> None:
        self.model_name = model_name
        self.provider = provider
        self.local_working_dir = local_working_dir
        self.event_handler = event_handler or NoOpDEAgentEventHandler()
        self.token_usage: dict[str, dict[str, int]] = {}
        self.cost_total = 0.0
        self.init_llm_model()
        self.graph = self._build_graph()

    def get_run_code_snippet_tool(self) -> BaseTool:
        @tool
        async def run_code_snippet(
            language: CodeLanguage, code: str
        ) -> RunCodeResult:
            """
            Executes a code snippet on the local machine in the session working directory.

            CRITICAL: RAM is NOT preserved, but the DISK IS.

            - STATELESS MEMORY: Variables, functions, and imports are LOST after each call.
            - PERSISTENT FILESYSTEM: Files survive between calls.
                * Intermediate files MUST be stored in `./intermediate/`.
                * Store final task results (cleaned data, DE tables) in the specified location (default is ./).
                * Plots follow their purpose: exploratory plots may go in ./intermediate/, final plots in ./results/.
                  To display a saved plot, use <image path="results/plot.png" /> with its actual path relative
                  to the session working directory, using forward slashes and staying inside that directory.
            - SELF-CONTAINED: Every snippet must include its own imports and data-loading logic.
            - OUTPUT RESTRICTION: Do not use print() for conversation.
            Use it ONLY to inspect data (e.g., shapes, summaries, head) to inform your next step.
            - LIMITS: Output is truncated at 2000 chars.

            Args:
                language (Literal["python", "R"]): "python" or "R".
                code (str): The complete, executable code block.

            Returns:
                RunCodeResult:
                    - stdout/stderr: Captured output (truncated to 2k chars).
                    - exit_code: 0 for success, non-zero for errors.
            Notes:
                - Max execution time: 600s.
                - Use pre-installed packages only.
            """
            if self.local_working_dir is None:
                results: RunCodeResult = {
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                    "message": "Local working directory is not configured.",
                }
            else:
                results = await _run_code_snippet_local(
                    language, code, self.local_working_dir
                )

            stdout_final, stdout_warn = _truncate_output(results["stdout"])
            stderr_final, stderr_warn = _truncate_output(results["stderr"])

            stdout_warning = (
                "\nWARNING: stdout was truncated due to length.\n"
                if stdout_warn
                else ""
            )
            stderr_warning = (
                "\nWARNING: stderr was truncated due to length.\n"
                if stderr_warn
                else ""
            )

            final_message = results["message"] + stdout_warning + stderr_warning

            return {
                "stdout": stdout_final,
                "stderr": stderr_final,
                "exit_code": results["exit_code"],
                "message": final_message,
            }

        return run_code_snippet

    def get_ask_user_tool(self) -> BaseTool:
        @tool
        async def ask_user(question: str) -> str:
            """
            Prompt the user with a question and retrieve their response.

            This function should be used as a last resort when the necessary
            information or experimental context cannot be inferred.

            Args:
                question (str): A clear, concise question to the user.

            Returns:
                str: The user's response.
            """
            return await self.event_handler.ask_user(question)

        return ask_user

    def init_llm_model(self) -> None:
        # Load environment variables from .env file
        load_dotenv()

        llm = init_chat_model(
            model=self.model_name,
            model_provider="openai",
            api_key=os.getenv("OPENAI_API_KEY"),
            reasoning={
                "effort": "medium",
                "summary": "auto",
            },
        )
        llm_light = init_chat_model(
            model=self.model_name,
            model_provider="openai",
            api_key=os.getenv("OPENAI_API_KEY"),
        )

        self.run_code_snippet_tool = self.get_run_code_snippet_tool()
        self.ask_user_tool = self.get_ask_user_tool()
        self.de_subagent_tool = self.get_de_subagent_tool()
        tools = [
            self.run_code_snippet_tool,
            self.ask_user_tool,
            self.de_subagent_tool,
        ]
        self.tools_by_name: dict[str, BaseTool] = {
            tool.name: tool for tool in tools
        }

        # LLM with reasoning and tool calling capabilities
        self.llm: BaseChatModel = llm
        # Another Separate LLM for light tasks
        self.llm_light: BaseChatModel = llm_light

        return None

    def get_de_subagent_tool(self) -> BaseTool:

        @tool
        async def de_subagent_invoke(
            de_method: DEMethod,
            prompt: str,
        ) -> str:
            """
            Invokes a specialized bioinformatics sub-agent to perform Differential Expression (DE) analysis.

            This tool should be used for calling DE method to identify genes or features that change
            significantly between groups. The sub-agent is equipped with specialized system prompts
            and code-execution capabilities (R/Python) to handle complex genomic workflows.

            Args:
                de_method (Literal["wilcoxon", "DESeq2", "edgeR", "limma-voom", "limma-no-voom", "NOISeq"]): The DE method to employ.
                prompt (str): A concise instruction specifying the comparison to make.

            Returns:
                str: A comprehensive summary from the sub-agent containing the results of the analysis,
                    statistical summaries, and identification of top differentially expressed genes.
            """
            developer_message = DeveloperMessage(
                content=load_prompt_template(
                    f"subagent_{de_method}_system_prompt",
                )
            )
            await self.event_handler.subagent_started(de_method)
            try:
                await self.event_handler.prompt_loaded(prompt)
                response_message = await self.llm_with_tools_invoke(
                    prev_messages=[
                        developer_message,
                        DeveloperMessage(content=prompt),
                    ],
                    tools=[self.run_code_snippet_tool],
                )
            finally:
                await self.event_handler.subagent_finished(de_method)
            return response_message[-1].text

        return de_subagent_invoke

    def _build_graph(
        self,
    ) -> CompiledStateGraph[DEAgentState, None, DEAgentState, DEAgentState]:
        # Build workflow
        workflow = StateGraph(DEAgentState)

        # Add nodes
        workflow.add_node("step0_classify_intent_node", self.step0_classify_intent_node)
        workflow.add_node("step1_data_cleaning_node", self.step1_data_cleaning_node)
        workflow.add_node(
            "step2_de_method_screening_node",
            self.step2_de_method_screening_node,
        )
        workflow.add_node(
            "step3_de_analysis_execution_node", self.step3_de_analysis_execution_node
        )
        workflow.add_node("single_llm_node", self.single_llm_node)

        # Add edges to connect nodes
        workflow.add_edge(START, "step0_classify_intent_node")
        agent_memory = MemorySaver()
        return workflow.compile(checkpointer=agent_memory)

    async def update_token_usage(self, last_message: AIMessage) -> None:
        response_metadata = last_message.response_metadata
        usage_metadata = last_message.usage_metadata

        if not response_metadata or not usage_metadata:
            print("No usage metadata found.")
            return

        model_name = cast(str, response_metadata.get("model_name"))
        if model_name not in self.token_usage:
            self.token_usage[model_name] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "input_cache_read": 0,
                "input_cache_creation": 0,
            }
        input_tokens_delta = usage_metadata.get("input_tokens")
        output_tokens_delta = usage_metadata.get("output_tokens")
        input_cache_read_delta = usage_metadata.get("input_token_details", {}).get(
            "cache_read", 0
        )
        input_cache_creation_delta = usage_metadata.get("input_token_details", {}).get(
            "cache_creation", 0
        )
        self.token_usage[model_name]["input_tokens"] += input_tokens_delta
        self.token_usage[model_name]["output_tokens"] += output_tokens_delta
        self.token_usage[model_name]["input_cache_read"] += input_cache_read_delta
        self.token_usage[model_name]["input_cache_creation"] += input_cache_creation_delta

        def calculate_cost(
            model_name: str,
            total_in: int,
            out: int,
            cache_read: int,
            cache_creation: int,
        ) -> float:
            regular_in = total_in - cache_read - cache_creation
            pricing_model_name = model_name
            pricing = MODEL_PRICING[pricing_model_name]
            is_long_context = (
                pricing_model_name in LONG_CONTEXT_MODELS
                and total_in > LONG_CONTEXT_TOKEN_THRESHOLD
            )
            input_multiplier = 2 if is_long_context else 1
            output_multiplier = 1.5 if is_long_context else 1

            cost = (
                (regular_in * pricing["input"] * input_multiplier)
                + (cache_read * pricing["cache_read"] * input_multiplier)
                + (cache_creation * pricing["cache_write"] * input_multiplier)
                + (out * pricing["output"] * output_multiplier)
            ) / 1_000_000
            return cost

        cost_delta = calculate_cost(
            model_name,
            input_tokens_delta,
            output_tokens_delta,
            input_cache_read_delta,
            input_cache_creation_delta,
        )
        self.cost_total += cost_delta

        print()
        print("=" * 20)
        print(f"Current Message:\n")
        print(f"{last_message.text[:50]}...\n")
        print(f"Token Usage Delta:")
        print(json.dumps(usage_metadata, indent=2))
        print(f"Total Token Usage:")
        print(json.dumps(self.token_usage, indent=2))
        print()
        print(f"Delta Cost: ${cost_delta:.6f}")
        print(f"Total Cost: ${self.cost_total:.6f}")
        print("=" * 20)
        print()

        return

    async def tool_invoke(self, last_message: AIMessage) -> list[ToolMessage]:
        """Execute all tool calls from the last message and return tool messages"""

        tool_messages: list[ToolMessage] = []
        for tool_call in last_message.tool_calls:
            tool = self.tools_by_name[tool_call["name"]]

            if tool_call["name"] == "run_code_snippet":
                observation = cast(
                    RunCodeResult, await tool.ainvoke(tool_call["args"])
                )
                await self.event_handler.code_snippet_executed(
                    language=tool_call["args"]["language"],
                    code=tool_call["args"]["code"],
                    observation=observation,
                )
            else:
                observation = await tool.ainvoke(tool_call["args"])

            tool_messages.append(
                ToolMessage(content=observation, tool_call_id=tool_call["id"])
            )

        return tool_messages

    async def llm_with_tools_invoke(
        self,
        prev_messages: list[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> list[AnyMessage]:
        """Invoke LLM and execute tools until getting a non-tool-calling response"""
        new_messages: list[AnyMessage] = []
        llm_with_tools = self.llm.bind_tools(tools) if tools else self.llm
        while True:
            reasoning: str | None = None
            await self.event_handler.thinking_started()
            try:
                last_message = await llm_with_tools.ainvoke(
                    prev_messages + new_messages
                )
                first_block = (
                    last_message.content_blocks[0]
                    if last_message.content_blocks
                    else None
                )
                reasoning = (
                    first_block.get("reasoning")
                    if isinstance(first_block, dict)
                    else None
                )
            finally:
                await self.event_handler.thinking_finished(reasoning)
            new_messages.append(last_message)

            await self.update_token_usage(last_message)

            if not last_message.tool_calls:
                break

            # Execute tools and append tool messages
            tool_messages = await self.tool_invoke(last_message)
            new_messages.extend(tool_messages)

        return new_messages

    class IntentClassificationResult(TypedDict):
        intent: Literal["new DE analysis", "others"]

    async def step0_classify_intent_node(
        self, state: DEAgentState
    ) -> Command[Any]:
        developer_message = DeveloperMessage(
            content=load_prompt_template("system_prompt"),
            id=state["messages"][0].id if len(state["messages"]) > 0 else None,
        )

        if state["user_input_files"] == []:
            intent = "others"
        else:
            prompt = load_prompt_template(
                "step0_classify_intent_prompt",
                user_message=state["user_message"],
                user_input_files=state["user_input_files"],
            )
            structured_llm = self.llm_light.with_structured_output(
                schema=DEAgent.IntentClassificationResult,
                include_raw=True,
            )
            # Only keep the base developer message to save token usage.
            messages = [developer_message, DeveloperMessage(content=prompt)]
            response = await structured_llm.ainvoke(messages)
            last_message = cast(AIMessage, response["raw"])
            structured_response = cast(
                DEAgent.IntentClassificationResult, response["parsed"]
            )
            intent = structured_response["intent"]

            print(f"Classified intent: {intent}")

            await self.update_token_usage(last_message)

        update = {"messages": [developer_message]}

        if intent == "new DE analysis":
            await self.event_handler.display_task_list()
            return Command(update=update, goto="step1_data_cleaning_node")
        else:
            return Command(update=update, goto="single_llm_node")

    async def single_llm_node(self, state: DEAgentState) -> Command[Any]:
        prompt = load_prompt_template(
            "single_llm_prompt",
            user_message=state["user_message"],
            user_input_files=state["user_input_files"],
        )
        new_messages = [HumanMessage(content=prompt)]
        new_messages.extend(
            await self.llm_with_tools_invoke(
                prev_messages=state["messages"] + new_messages,
                tools=[self.run_code_snippet_tool],
            )
        )

        return Command(update={"messages": new_messages}, goto=END)

    async def step1_data_cleaning_node(
        self, state: DEAgentState
    ) -> Command[Any]:
        prompt = load_prompt_template(
            "step1_data_cleaning_prompt",
            user_message=state["user_message"],
            user_input_files=state["user_input_files"],
        )
        await self.event_handler.prompt_loaded(prompt)
        new_messages = [DeveloperMessage(content=prompt)]
        new_messages.extend(
            await self.llm_with_tools_invoke(
                prev_messages=state["messages"] + new_messages,
                tools=[self.ask_user_tool, self.run_code_snippet_tool],
            )
        )

        await self.event_handler.update_task_list(
            completed_task_index=0, langchain_msg_id=new_messages[-1].id
        )

        if len(new_messages) > 2:
            trimmed_new_messages = [new_messages[0], new_messages[-1]]
            print("=" * 20)
            print(
                f"Trimmed this step's messages from {len(new_messages)} to {len(trimmed_new_messages)}"
            )
            print("=" * 20)
        else:
            trimmed_new_messages = new_messages

        return Command(
            update={"messages": trimmed_new_messages},
            goto="step2_de_method_screening_node",
        )

    async def step2_de_method_screening_node(
        self, state: DEAgentState
    ) -> Command[Any]:
        prompt = load_prompt_template(
            "step2_de_method_screening_prompt",
        )
        await self.event_handler.prompt_loaded(prompt)
        new_messages = [DeveloperMessage(content=prompt)]
        new_messages.extend(
            await self.llm_with_tools_invoke(
                prev_messages=state["messages"] + new_messages,
                tools=[self.ask_user_tool, self.run_code_snippet_tool],
            )
        )

        while True:
            approved, feedback_text = await self.event_handler.ask_de_method_approval()
            if approved:
                break
            else:
                prompt = load_prompt_template(
                    "step2_decline_method_prompt",
                    feedback=feedback_text or "",
                )

                new_messages.append(DeveloperMessage(content=prompt))
                new_messages.extend(
                    await self.llm_with_tools_invoke(
                        prev_messages=state["messages"] + new_messages,
                        tools=[self.ask_user_tool, self.run_code_snippet_tool],
                    )
                )

        await self.event_handler.update_task_list(
            completed_task_index=1, langchain_msg_id=new_messages[-1].id
        )

        if len(new_messages) > 2:
            trimmed_new_messages = [new_messages[0], new_messages[-1]]
            print("=" * 20)
            print(
                f"Trimmed this step's messages from {len(new_messages)} to {len(trimmed_new_messages)}"
            )
            print("=" * 20)
        else:
            trimmed_new_messages = new_messages

        return Command(
            update={"messages": trimmed_new_messages},
            goto="step3_de_analysis_execution_node",
        )

    async def step3_de_analysis_execution_node(
        self, state: DEAgentState
    ) -> Command[Any]:
        prompt = load_prompt_template(
            "step3_de_analysis_execution_prompt",
        )
        await self.event_handler.prompt_loaded(prompt)
        new_messages = [DeveloperMessage(content=prompt)]
        new_messages.extend(
            await self.llm_with_tools_invoke(
                prev_messages=state["messages"] + new_messages,
                tools=[
                    self.ask_user_tool,
                    self.run_code_snippet_tool,
                    self.de_subagent_tool,
                ],
            )
        )

        await self.event_handler.update_task_list(
            completed_task_index=2, langchain_msg_id=new_messages[-1].id
        )

        print(1)

        return Command(update={"messages": new_messages}, goto=END)
