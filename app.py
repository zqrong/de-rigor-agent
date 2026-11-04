import asyncio
import datetime
import json
import re
from pathlib import Path, PurePosixPath
import shutil
import uuid
from typing import Any, Final, Literal, TypedDict, cast, overload

import chainlit as cl
from convert_chat_history import chat_history_to_html
from image_references import IMAGE_TAG_RE, resolve_image_path
from chainlit.data import BaseDataLayer
from chainlit.element import Element, ElementDict
from chainlit.step import StepDict
from chainlit.types import (
    AskActionResponse,
    ChatProfile,
    CommandDict,
    Feedback,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.user import PersistedUser, User
from langchain.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from agent import (
    CodeLanguage,
    DEAgent,
    DEAgentState,
    DEFAULT_LLM_MODEL,
    LLM_MODELS,
    RunCodeResult,
)

APP_DIR: Final[Path] = Path(__file__).resolve().parent
LOCAL_WEB_RUNS_DIR: Final[Path] = APP_DIR / "agent_web_runs"

MessageTarget = cl.Message | cl.Step
MessageMap = dict[str | None, MessageTarget]
MessageStreamData = tuple[AnyMessage, dict[str, Any]]


class AppSettings(TypedDict):
    model: str


class ExportDataLayer(BaseDataLayer):
    def __init__(self) -> None:
        pass

    async def create_step(self, step_dict: StepDict) -> None:
        history = cast(
            list[StepDict], cl.user_session.get("full_chat_history", [])
        )
        history.append(step_dict)
        cl.user_session.set("full_chat_history", history)

    async def update_step(self, step_dict: StepDict) -> None:
        history = cast(
            list[StepDict], cl.user_session.get("full_chat_history", [])
        )
        for i, s in enumerate(history):
            if s["id"] == step_dict["id"]:
                history[i] = step_dict
                break
        cl.user_session.set("full_chat_history", history)

    async def get_user(self, identifier: str) -> PersistedUser | None:
        return None

    async def create_user(self, user: User) -> PersistedUser | None:
        return None

    async def delete_feedback(self, feedback_id: str) -> bool:
        return True

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return ""

    async def create_element(self, element: Element) -> None:
        pass

    async def get_element(
        self, thread_id: str, element_id: str
    ) -> ElementDict | None:
        return None

    async def delete_element(
        self, element_id: str, thread_id: str | None = None
    ) -> None:
        pass

    async def delete_step(self, step_id: str) -> None:
        pass

    async def get_thread_author(self, thread_id: str) -> str:
        return ""

    async def delete_thread(self, thread_id: str) -> None:
        pass

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        return PaginatedResponse(data=[], page_info=None)

    async def get_thread(self, thread_id: str) -> ThreadDict | None:
        return None

    async def update_thread(
        self,
        thread_id: str,
        name: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        pass

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        pass

    async def get_favorite_steps(self, user_id: str) -> list[StepDict]:
        return []


async def extract_inline_elements_from_agent_response(
    content: str,
) -> tuple[str, list[cl.Image]]:
    elements: list[cl.Image] = []

    def collect_image(match: re.Match[str]) -> str:
        relative_path = match.group(1)
        local_working_dir = cast(Path | None, cl.user_session.get("local_working_dir"))
        if local_working_dir is None:
            return "[Image unavailable: no local working directory.]"
        try:
            path = resolve_image_path(local_working_dir, relative_path)
            content = path.read_bytes()
            if not content:
                return "[Image unavailable: file is empty.]"
        except (OSError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "File is missing or unreadable."
            return f"[Image unavailable: {reason}]"
        elements.append(
            cl.Image(
                content=content,
                name=path.name,
                display="inline",
            )
        )
        return ""

    cleaned_content = IMAGE_TAG_RE.sub(collect_image, content)
    return cleaned_content, elements


@overload
async def stream_agent_response(
    stream_mode: Literal["messages"], data: MessageStreamData
) -> None: ...


@overload
async def stream_agent_response(
    stream_mode: Literal["custom"], data: str
) -> None: ...


async def stream_agent_response(
    stream_mode: Literal["messages", "custom"],
    data: MessageStreamData | str,
) -> None:
    msg_map = cast(MessageMap, cl.user_session.get("msg_map"))
    if stream_mode == "messages":
        token, metadata = cast(MessageStreamData, data)
        if metadata.get("langgraph_node") == "step0_classify_intent_node":
            # Skip streaming for intent classification step
            return None
        if isinstance(token, AIMessageChunk) and token.text:
            if token.id not in msg_map:
                if cl.user_session.get("subagent_step") is not None:
                    msg = cast(cl.Step, cl.user_session.get("subagent_step"))
                else:
                    msg = await cl.Message(content="").send()
                msg_map[token.id] = msg
                cl.user_session.set("msg_map", msg_map)
            else:
                msg = msg_map[token.id]
            await msg.stream_token(token.text)
        if (
            isinstance(token, AIMessageChunk)
            and token.id in msg_map
            and token.chunk_position == "last"
        ):
            msg = msg_map[token.id]
            if isinstance(
                msg, cl.Message
            ):  # if msg is a cl.Step (in subagent), it doesn't have content or elements attributes.
                content, elements = await extract_inline_elements_from_agent_response(
                    msg.content
                )
                msg.content = content
                if elements:
                    msg.elements = elements

            await msg.update()
    # Not used currently
    elif stream_mode == "custom":
        msg = await cl.Message(content="").send()
        await msg.stream_token(cast(str, data))
        await msg.update()
    return None


def extract_uploaded_filename(file_name: str) -> str:
    return PurePosixPath(file_name.replace("\\", "/")).name


def upload_files_local(
    files: list[tuple[Path, str]],
) -> None:
    local_working_dir = cast(Path, cl.user_session.get("local_working_dir"))
    for file_path, file_name in files:
        dest_path = local_working_dir / "uploads" / file_name
        shutil.copy(file_path, dest_path)


class ChainlitDEAgentEventHandler:
    async def display_task_list(self) -> None:
        task_list = cl.TaskList()
        task_list.status = "Running..."
        await task_list.add_task(
            cl.Task(title="Data Cleaning", status=cl.TaskStatus.RUNNING)
        )
        await task_list.add_task(cl.Task(title="DE Method Screening"))
        await task_list.add_task(cl.Task(title="DE Analysis Execution"))
        cl.user_session.set("de_task_list", task_list)
        await task_list.send()

    async def update_task_list(
        self, completed_task_index: int, langchain_msg_id: str | None
    ) -> None:
        task_list = cast(cl.TaskList | None, cl.user_session.get("de_task_list"))
        if task_list is None:
            return

        msg_map = cast(
            MessageMap, cl.user_session.get("msg_map", {})
        )
        if langchain_msg_id not in msg_map:
            chat_context = cl.chat_context.get()
            chainlit_msg = chat_context[-1] if chat_context else None
            print("=" * 20)
            print(
                f"Warning: langchain_msg_id {langchain_msg_id} not found in msg_map. This is a known issue when using OpenAI api with langchain."
            )
            print("=" * 20)
        else:
            chainlit_msg = msg_map[langchain_msg_id]

        task = task_list.tasks[completed_task_index]
        if chainlit_msg is not None:
            task.forId = chainlit_msg.id
        task.status = cl.TaskStatus.DONE
        if completed_task_index + 1 < len(task_list.tasks):
            task_list.tasks[completed_task_index + 1].status = cl.TaskStatus.RUNNING
        else:
            task_list.status = "Completed"
        await task_list.send()

    async def prompt_loaded(self, prompt: str) -> None:
        async with cl.Step(name="Loading prompt", show_input=False) as step:
            step.output = prompt

    async def thinking_started(self) -> None:
        step = cl.Step(name="Thinking", show_input=False)
        await step.__aenter__()
        cl.user_session.set("thinking_step", step)

    async def thinking_finished(self, reasoning: str | None) -> None:
        step = cast(cl.Step | None, cl.user_session.get("thinking_step"))
        cl.user_session.set("thinking_step", None)
        if step is not None:
            step.output = reasoning
            await step.__aexit__(None, None, None)

    async def code_snippet_executed(
        self, language: CodeLanguage, code: str, observation: RunCodeResult
    ) -> None:
        async with cl.Step(
            name="Running code snippet",
            show_input=language,
        ) as step:
            step.input = code
            await step.stream_token(f"\n```stdout\n{observation['stdout']}\n```")
            await step.stream_token(f"\n```stderr\n{observation['stderr']}\n```")

    async def ask_user(self, question: str) -> str:
        result = await cl.AskUserMessage(content=question, timeout=600).send()
        if result is None:
            return ""
        return result["output"]

    async def ask_de_method_approval(self) -> tuple[bool, str | None]:
        approval: AskActionResponse | None = await cl.AskActionMessage(
            content="Do you approve the selected DE method and wish to proceed?",
            actions=[
                cl.Action(
                    name="continue",
                    payload={"value": "continue"},
                    label="✅ Continue",
                ),
                cl.Action(name="cancel", payload={"value": "cancel"}, label="❌ Cancel"),
            ],
            timeout=600,
        ).send()
        if approval is None:
            await self.notify(
                "No response received. Auto-approved and proceeding with the selected DE method."
            )
            return True, None
        if approval.get("payload").get("value") == "continue":
            return True, None

        feedback = await cl.AskUserMessage(content="feedback?", timeout=600).send()
        return False, "" if feedback is None else feedback["output"]

    async def notify(self, message: str) -> None:
        await cl.Message(content=message).send()

    async def subagent_started(self, de_method: str) -> None:
        step_context = cl.Step(
            name=f"Running {de_method} DE sub-agent", show_input=False
        )
        step = await step_context.__aenter__()
        cl.user_session.set("subagent_step_context", step_context)
        cl.user_session.set("subagent_step", step)

    async def subagent_finished(self, de_method: str) -> None:
        step_context = cast(
            cl.Step | None, cl.user_session.get("subagent_step_context")
        )
        cl.user_session.set("subagent_step", None)
        cl.user_session.set("subagent_step_context", None)
        if step_context is not None:
            await step_context.__aexit__(None, None, None)


@cl.data_layer
def get_data_layer() -> BaseDataLayer:
    return ExportDataLayer()


@cl.set_chat_profiles
async def chat_profile() -> list[ChatProfile]:
    return [
        cl.ChatProfile(
            name="Local",
            markdown_description="Code execution happens on the local machine.",
            default=True,
        ),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("full_chat_history", [])
    cl.user_session.set("subagent_step", None)

    commands: list[CommandDict] = [
        {
            "id": "Download",
            "icon": "download",
            "description": "Download chat history in JSON and HTML formats.",
        },
    ]
    await cl.context.emitter.set_commands(commands)

    chat_settings = await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="model",
                label="LLM Model",
                values=list(LLM_MODELS.keys()),
                initial_index=list(LLM_MODELS).index(DEFAULT_LLM_MODEL),
            ),
        ]
    ).send()
    thread_id = uuid.uuid4().hex
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    typed_chat_settings = cast(AppSettings, chat_settings)
    provider, model_name = LLM_MODELS[typed_chat_settings["model"]]
    agent = DEAgent(
        model_name=model_name,
        provider=provider,
        event_handler=ChainlitDEAgentEventHandler(),
    )
    cl.user_session.set("thread_id", thread_id)
    cl.user_session.set("config", config)
    cl.user_session.set("agent", agent)
    cl.user_session.set("msg_map", cast(MessageMap, {}))

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    local_working_dir = LOCAL_WEB_RUNS_DIR / f"{timestamp}_{thread_id}"
    local_working_dir.mkdir(parents=True, exist_ok=True)
    (local_working_dir / "uploads").mkdir(exist_ok=True)
    (local_working_dir / "data").mkdir(exist_ok=True)
    (local_working_dir / "results").mkdir(exist_ok=True)
    (local_working_dir / "intermediate").mkdir(exist_ok=True)
    cl.user_session.set("local_working_dir", local_working_dir)
    agent.local_working_dir = local_working_dir


@cl.on_settings_update
async def on_settings_update(settings: AppSettings) -> None:
    agent = cast(DEAgent, cl.user_session.get("agent"))

    # Update model if changed
    provider, model_name = LLM_MODELS[settings["model"]]
    if agent.provider != provider or agent.model_name != model_name:
        agent.provider = provider
        agent.model_name = model_name
        agent.init_llm_model()


async def chat_history_to_json(history: list[StepDict]) -> str:
    json_list: list[str] = []
    for step_dict in history:
        json_list.append(json.dumps(step_dict, indent=2))
    return "[\n" + ",\n".join(json_list) + "\n]"


@cl.on_message
async def on_message(user_message: cl.Message) -> None:

    if user_message.command == "Download":
        history = cast(
            list[StepDict], cl.user_session.get("full_chat_history", [])
        )
        history_json_content = await chat_history_to_json(history)
        history_json_bytes = history_json_content.encode("utf-8")
        # Render the same snapshot as the JSON attachment off the event loop.
        history_html_content = await asyncio.to_thread(
            chat_history_to_html, json.loads(history_json_content)
        )

        elements = [
            cl.File(
                name="chat_history.json",
                content=history_json_bytes,
                mime="application/json",
            ),
            cl.File(
                name="chat_history.html",
                content=history_html_content.encode("utf-8"),
                mime="text/html",
            ),
        ]
        await cl.Message(
            content="Here is the full chat history in JSON and HTML formats.",
            elements=elements,
        ).send()

        return

    agent = cast(DEAgent, cl.user_session.get("agent"))
    config = cast(RunnableConfig, cl.user_session.get("config"))

    files = [
        (Path(file.path), extract_uploaded_filename(file.name))
        for file in user_message.elements
    ]
    if len(files) > 0:
        async with cl.Step(name="Uploading files", show_input=False) as step:
            await asyncio.to_thread(upload_files_local, files)
        await step.remove()

    agent_state: DEAgentState = {
        "user_message": user_message.content,
        "user_input_files": [f"uploads/{file_name}" for _, file_name in files],
        "messages": [],
    }

    async for stream_mode, data in agent.graph.astream(
        input=agent_state,
        config=config,
        stream_mode=["messages", "custom"],
    ):
        await stream_agent_response(stream_mode, data)

    return


@cl.on_chat_end
async def on_chat_end() -> None:
    await cl.Message(content="Bye~").send()


@cl.on_stop
async def on_stop() -> None:
    task_list = cast(cl.TaskList | None, cl.user_session.get("de_task_list"))
    if task_list:
        task_list.status = "Failed"
        for task in task_list.tasks:
            if task.status == cl.TaskStatus.RUNNING:
                task.status = cl.TaskStatus.FAILED
        await task_list.send()
