import argparse
import asyncio
import datetime
import json
import re
import shutil
import sys
import traceback
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Final, Sequence, cast
from urllib.parse import quote

from langchain.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from image_references import IMAGE_TAG_RE, resolve_image_path

from agent import (
    CodeLanguage,
    DEAgent,
    DEAgentState,
    DEFAULT_LLM_MODEL,
    LLM_MODELS,
    ModelProvider,
    RunCodeResult,
)


APP_DIR: Final[Path] = Path(__file__).resolve().parent
DEFAULT_CLI_RUNS_DIR: Final[Path] = APP_DIR / "agent_cli_runs"
DEFAULT_MODEL: Final[str] = LLM_MODELS[DEFAULT_LLM_MODEL][1]
TASK_NAMES: Final[tuple[str, ...]] = (
    "Data Cleaning",
    "DE Method Screening",
    "DE Analysis Execution",
)
NON_INTERACTIVE_ANSWER: Final[str] = (
    "The user is running this analysis in non-interactive batch mode and will "
    "not provide any additional information. Use the information already "
    "available, make the best reasonable judgment, state any necessary "
    "assumptions, and continue the analysis."
)


class CLIUsageError(Exception):
    """Raised when command-line inputs cannot define a valid run."""


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def format_timestamp(value: datetime.datetime) -> str:
    return value.isoformat(timespec="seconds")


def markdown_fence(content: str, language: str = "") -> str:
    longest_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", content)),
        default=0,
    )
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{content}\n{fence}"


def image_tags_to_markdown(content: str, working_dir: Path) -> str:
    def replace_image(match: re.Match[str]) -> str:
        try:
            path = resolve_image_path(working_dir, match.group(1))
        except (OSError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "File is missing or unreadable."
            return f"[Image unavailable: {reason}]"
        relative_path = path.relative_to(working_dir.resolve()).as_posix()
        return f"![Image](<working/{quote(relative_path, safe='/')}>)"

    return IMAGE_TAG_RE.sub(replace_image, content)


class MarkdownLogWriter:
    def __init__(
        self,
        path: Path,
        *,
        thread_id: str,
        model_name: str,
        working_dir: Path,
        interactive: bool,
    ) -> None:
        self.path = path
        self.started_at = utc_now()
        self.working_dir = working_dir
        self._file = path.open("x", encoding="utf-8")
        self._write(
            "---\n"
            f"started_at: {json.dumps(format_timestamp(self.started_at))}\n"
            f"model: {json.dumps(model_name)}\n"
            "code_environment: \"local\"\n"
            f"thread_id: {json.dumps(thread_id)}\n"
            f"interactive: {str(interactive).lower()}\n"
            "---\n\n"
            "# DE-Rigor-Agent Run\n"
        )

    def _write(self, content: str) -> None:
        self._file.write(content)
        self._file.flush()

    def section(self, heading: str, content: str | None = None, level: int = 2) -> None:
        self._write(f"\n{'#' * level} {heading}\n")
        if content is not None:
            self._write(f"\n{content}\n")

    def text(self, content: str) -> None:
        self._write(f"\n{content}\n")

    def record_request(
        self, message: str, input_files: list[tuple[Path, str]]
    ) -> None:
        self.section("Request", message)
        self.section("Input files")
        if not input_files:
            self.text("No input files were provided.")
            return
        for source_path, uploaded_name in input_files:
            self._write(
                f"\n- `working/uploads/{uploaded_name}` "
                f"(source: `{source_path}`)"
            )
        self._write("\n")

    def record_prompt(self, prompt: str, subagent: str | None) -> None:
        label = "Sub-agent prompt" if subagent else "Loaded prompt"
        self.section(label, level=5 if subagent else 4)
        self.text(
            "<details>\n<summary>Show prompt</summary>\n\n"
            f"{markdown_fence(prompt, 'text')}\n\n"
            "</details>"
        )

    def record_reasoning(self, reasoning: str | None, subagent: str | None) -> None:
        self.section("Reasoning", level=5 if subagent else 4)
        self.text(reasoning or "No reasoning summary was provided.")

    def record_agent_response(self, content: str, subagent: str | None) -> None:
        label = "Sub-agent response" if subagent else "Agent response"
        self.section(
            label,
            image_tags_to_markdown(content, self.working_dir),
            level=5 if subagent else 4,
        )

    def record_custom_stream(self, content: str) -> None:
        self.section("Custom event", content, level=4)

    def record_code(
        self,
        language: CodeLanguage,
        code: str,
        observation: RunCodeResult,
        subagent: str | None,
    ) -> None:
        self.section(f"Code execution: {language}", level=5 if subagent else 4)
        self.text(markdown_fence(code, "python" if language == "python" else "r"))
        self.text(
            f"Exit code: `{observation['exit_code']}`  \n"
            f"Message: {observation['message']}"
        )
        self.section("stdout", level=6 if subagent else 5)
        self.text(markdown_fence(observation["stdout"], "text"))
        self.section("stderr", level=6 if subagent else 5)
        self.text(markdown_fence(observation["stderr"], "text"))

    def record_artifacts(self) -> None:
        self.section("Generated artifacts")
        artifacts: list[Path] = []
        for directory_name in ("data", "results", "intermediate"):
            directory = self.working_dir / directory_name
            artifacts.extend(path for path in directory.rglob("*") if path.is_file())

        if not artifacts:
            self.text("No generated artifacts were found.")
            return

        for artifact in sorted(artifacts):
            relative_path = artifact.relative_to(self.working_dir)
            link = PurePosixPath("working") / PurePosixPath(relative_path.as_posix())
            self._write(
                f"\n- [{relative_path.as_posix()}](<{link.as_posix()}>) "
                f"({artifact.stat().st_size} bytes)"
            )
        self._write("\n")

    def record_usage(self, token_usage: dict[str, dict[str, int]], cost: float) -> None:
        self.section("Usage")
        self.text(markdown_fence(json.dumps(token_usage, indent=2), "json"))
        self.text(f"Total estimated cost: `${cost:.6f}`")

    def finish(self, status: str, error: str | None = None) -> None:
        finished_at = utc_now()
        self.section("Run result")
        self.text(
            f"Status: **{status}**  \n"
            f"Finished at: `{format_timestamp(finished_at)}`  \n"
            f"Duration: `{finished_at - self.started_at}`"
        )
        if error:
            self.section("Error", markdown_fence(error, "text"), level=3)
        self._file.close()


class MarkdownDEAgentEventHandler:
    def __init__(self, writer: MarkdownLogWriter, interactive: bool) -> None:
        self.writer = writer
        self.interactive = interactive
        self.subagent_stack: list[str] = []

    @property
    def current_subagent(self) -> str | None:
        return self.subagent_stack[-1] if self.subagent_stack else None

    async def display_task_list(self) -> None:
        self.writer.section("Workflow")
        for task_index, task_name in enumerate(TASK_NAMES, start=1):
            self.writer._write(f"\n{task_index}. {task_name}")
        self.writer._write("\n")
        self.writer.section(f"Task 1: {TASK_NAMES[0]}", level=3)
        self.writer.text("Status: running")

    async def update_task_list(
        self, completed_task_index: int, langchain_msg_id: str | None
    ) -> None:
        task_name = TASK_NAMES[completed_task_index]
        self.writer.section(
            f"Task completed: {task_name}",
            "Status: completed",
            level=4,
        )
        next_index = completed_task_index + 1
        if next_index < len(TASK_NAMES):
            self.writer.section(
                f"Task {next_index + 1}: {TASK_NAMES[next_index]}", level=3
            )
            self.writer.text("Status: running")

    async def prompt_loaded(self, prompt: str) -> None:
        self.writer.record_prompt(prompt, self.current_subagent)

    async def thinking_started(self) -> None:
        return None

    async def thinking_finished(self, reasoning: str | None) -> None:
        self.writer.record_reasoning(reasoning, self.current_subagent)

    async def code_snippet_executed(
        self, language: CodeLanguage, code: str, observation: RunCodeResult
    ) -> None:
        self.writer.record_code(language, code, observation, self.current_subagent)

    async def ask_user(self, question: str) -> str:
        self.writer.section(
            "Question for user",
            question,
            level=5 if self.current_subagent else 4,
        )
        if self.interactive:
            try:
                answer = await asyncio.to_thread(input, "Agent question> ")
            except EOFError:
                answer = NON_INTERACTIVE_ANSWER
        else:
            answer = NON_INTERACTIVE_ANSWER
        self.writer.section(
            "User response",
            answer,
            level=5 if self.current_subagent else 4,
        )
        return answer

    async def ask_de_method_approval(self) -> tuple[bool, str | None]:
        self.writer.section("DE method approval", level=4)
        if not self.interactive:
            self.writer.text(
                "Automatically approved because this run is using non-interactive "
                "batch mode."
            )
            return True, None

        try:
            answer = await asyncio.to_thread(
                input, "Approve the selected DE method? [Y/n] "
            )
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"n", "no"}:
            self.writer.text("Approved by the terminal user.")
            return True, None

        try:
            feedback = await asyncio.to_thread(input, "Feedback> ")
        except EOFError:
            feedback = ""
        self.writer.text(f"Declined by the terminal user. Feedback: {feedback}")
        return False, feedback

    async def notify(self, message: str) -> None:
        self.writer.section("Notification", message, level=4)

    async def subagent_started(self, de_method: str) -> None:
        self.subagent_stack.append(de_method)
        self.writer.section(f"Sub-agent: {de_method}", "Status: running", level=4)

    async def subagent_finished(self, de_method: str) -> None:
        self.writer.section(
            f"Sub-agent completed: {de_method}", "Status: completed", level=5
        )
        if self.subagent_stack and self.subagent_stack[-1] == de_method:
            self.subagent_stack.pop()
        elif de_method in self.subagent_stack:
            self.subagent_stack.remove(de_method)


class MarkdownStreamLogger:
    def __init__(
        self, writer: MarkdownLogWriter, event_handler: MarkdownDEAgentEventHandler
    ) -> None:
        self.writer = writer
        self.event_handler = event_handler
        self.buffers: dict[str, list[str]] = {}
        self.contexts: dict[str, str | None] = {}
        self.anonymous_message_id: str | None = None

    def _message_id(self, token: AIMessageChunk) -> str:
        if token.id is not None:
            return token.id
        if self.anonymous_message_id is None:
            self.anonymous_message_id = f"anonymous-{uuid.uuid4().hex}"
        return self.anonymous_message_id

    def process(self, stream_mode: str, data: Any) -> None:
        if stream_mode == "custom":
            self.writer.record_custom_stream(str(data))
            return
        if stream_mode != "messages":
            return

        token, metadata = cast(tuple[Any, dict[str, Any]], data)
        if metadata.get("langgraph_node") == "step0_classify_intent_node":
            return
        if not isinstance(token, AIMessageChunk):
            return

        message_id = self._message_id(token)
        if message_id not in self.buffers:
            self.buffers[message_id] = []
            self.contexts[message_id] = self.event_handler.current_subagent
        if token.text:
            self.buffers[message_id].append(str(token.text))

        if token.chunk_position == "last":
            content = "".join(self.buffers.pop(message_id, []))
            subagent = self.contexts.pop(message_id, None)
            if content:
                self.writer.record_agent_response(content, subagent)
            if token.id is None:
                self.anonymous_message_id = None

    def flush_incomplete(self) -> None:
        incomplete_note = (
            "\n\n*[Response stream ended before a final chunk was received.]*"
        )
        for message_id, chunks in list(self.buffers.items()):
            content = "".join(chunks)
            if content:
                self.writer.record_agent_response(
                    content + incomplete_note,
                    self.contexts.get(message_id),
                )
        self.buffers.clear()
        self.contexts.clear()
        self.anonymous_message_id = None


def available_models() -> dict[str, tuple[ModelProvider, str]]:
    return {
        model_name: (provider, model_name)
        for provider, model_name in LLM_MODELS.values()
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run DE-Rigor-Agent once without Chainlit and write all agent events "
            "to a Markdown log."
        )
    )
    message_group = parser.add_mutually_exclusive_group(required=True)
    message_group.add_argument("--message", help="User request sent to the agent.")
    message_group.add_argument(
        "--message-file",
        type=Path,
        help="UTF-8 text file containing the user request.",
    )
    parser.add_argument(
        "--input",
        dest="input_files",
        action="append",
        type=Path,
        default=[],
        help="Input file to upload. Repeat this option for multiple files.",
    )
    parser.add_argument(
        "--model",
        choices=sorted(available_models()),
        default=DEFAULT_MODEL,
        help=f"OpenAI model ID (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="New directory for run.md and the isolated working directory.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Allow the agent to ask questions and request DE method approval "
            "in the terminal."
        ),
    )
    return parser.parse_args(argv)


def resolve_request(args: argparse.Namespace) -> str:
    if args.message is not None:
        message = cast(str, args.message)
    else:
        message_file = cast(Path, args.message_file)
        if not message_file.is_file():
            raise CLIUsageError(f"Message file does not exist: {message_file}")
        message = message_file.read_text(encoding="utf-8")
    if not message.strip():
        raise CLIUsageError("The user request must not be empty.")
    return message


def resolve_input_files(input_files: list[Path]) -> list[Path]:
    resolved_files: list[Path] = []
    uploaded_names: set[str] = set()
    for input_file in input_files:
        resolved = input_file.resolve()
        if not resolved.is_file():
            raise CLIUsageError(f"Input file does not exist: {input_file}")
        if resolved.name in uploaded_names:
            raise CLIUsageError(
                f"Multiple input files have the same basename: {resolved.name}"
            )
        uploaded_names.add(resolved.name)
        resolved_files.append(resolved)
    return resolved_files


def create_run_directories(
    run_dir_arg: Path | None, thread_id: str
) -> tuple[Path, Path]:
    if run_dir_arg is None:
        timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
        run_dir = DEFAULT_CLI_RUNS_DIR / f"{timestamp}_{thread_id[:8]}"
    else:
        run_dir = run_dir_arg.resolve()

    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir()
    except FileExistsError as error:
        raise CLIUsageError(f"Run directory already exists: {run_dir}") from error

    working_dir = run_dir / "working"
    for directory_name in ("uploads", "data", "results", "intermediate"):
        (working_dir / directory_name).mkdir(parents=True, exist_ok=True)
    return run_dir, working_dir


def stage_input_files(
    input_files: list[Path], working_dir: Path
) -> list[tuple[Path, str]]:
    staged_files: list[tuple[Path, str]] = []
    for source_path in input_files:
        uploaded_name = source_path.name
        shutil.copy2(source_path, working_dir / "uploads" / uploaded_name)
        staged_files.append((source_path, uploaded_name))
    return staged_files


async def run_agent(args: argparse.Namespace) -> tuple[Path, Path]:
    message = resolve_request(args)
    input_files = resolve_input_files(cast(list[Path], args.input_files))
    thread_id = uuid.uuid4().hex
    run_dir, working_dir = create_run_directories(args.run_dir, thread_id)
    staged_files = stage_input_files(input_files, working_dir)
    log_path = run_dir / "run.md"
    model_options = available_models()
    provider, model_name = model_options[cast(str, args.model)]
    writer = MarkdownLogWriter(
        log_path,
        thread_id=thread_id,
        model_name=model_name,
        working_dir=working_dir,
        interactive=cast(bool, args.interactive),
    )
    writer.record_request(message, staged_files)
    event_handler = MarkdownDEAgentEventHandler(
        writer, interactive=cast(bool, args.interactive)
    )
    stream_logger = MarkdownStreamLogger(writer, event_handler)

    print(f"Run started. Log: {log_path}", flush=True)

    try:
        agent = DEAgent(
            model_name=model_name,
            provider=provider,
            local_working_dir=working_dir,
            event_handler=event_handler,
        )
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        agent_state: DEAgentState = {
            "user_message": message,
            "user_input_files": [
                f"uploads/{uploaded_name}" for _, uploaded_name in staged_files
            ],
            "messages": [],
        }

        async for stream_mode, data in agent.graph.astream(
            input=agent_state,
            config=config,
            stream_mode=["messages", "custom"],
        ):
            stream_logger.process(stream_mode, data)

        stream_logger.flush_incomplete()
        writer.record_artifacts()
        writer.record_usage(agent.token_usage, agent.cost_total)
        writer.finish("completed")
        return log_path, working_dir
    except asyncio.CancelledError:
        stream_logger.flush_incomplete()
        writer.finish("interrupted", "The run was interrupted by the user.")
        raise
    except Exception:
        stream_logger.flush_incomplete()
        writer.finish("failed", traceback.format_exc())
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        log_path, working_dir = asyncio.run(run_agent(args))
    except CLIUsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Run interrupted by the user.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Run failed: {error}", file=sys.stderr)
        return 1

    print("Run completed successfully.")
    print(f"Log: {log_path}")
    print(f"Working directory: {working_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
