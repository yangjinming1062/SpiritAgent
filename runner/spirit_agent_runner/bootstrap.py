import asyncio
import atexit
import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from spirit_agent_runner.contracts.context import invocation_scope
from spirit_agent_runner.features import execution
from spirit_agent_runner.features.browser import BrowserManager
from spirit_agent_runner.features.browser import get_tool_definitions as browser_tools
from spirit_agent_runner.features.desktop import cu_tool, system_tools
from spirit_agent_runner.features.desktop.manager import DesktopManager, desktop_scope
from spirit_agent_runner.features.execution.api import execution_scope
from spirit_agent_runner.features.execution.manager import ExecutionManager
from spirit_agent_runner.features.files import file_tools
from spirit_agent_runner.features.files.manager import FileManager, file_scope
from spirit_agent_runner.features.media.vision import get_tool_definitions as media_tools
from spirit_agent_runner.features.skills import skill_manager_tool, skills_tool
from spirit_agent_runner.features.skills.manager import SkillManager, skill_scope
from spirit_agent_runner.runtime.application import RunnerRuntime
from spirit_agent_runner.runtime.config import ConfigStore
from spirit_agent_runner.runtime.tools import ToolCatalog, ToolExecutor
from spirit_agent_runner.transport.desktop import DesktopEndpoint
from spirit_agent_runner.transport.rpc import RunnerTransport

logger = logging.getLogger(__name__)


class RunnerApplication:
    def __init__(self) -> None:
        self.config = ConfigStore()
        self.execution = ExecutionManager(self.config.snapshot)
        self.files = FileManager()
        self.desktop = DesktopManager(cu_tool.create_backend)
        self.skills = SkillManager()
        self.browser = BrowserManager(config_provider=self.config.snapshot)
        self.execution.set_environment_cleanup(self.files.clear)
        definitions = [
            *execution.get_tool_definitions(),
            *file_tools.get_tool_definitions(),
            *browser_tools(self.browser),
            *cu_tool.get_tool_definitions(),
            *system_tools.get_tool_definitions(),
            *media_tools(),
            *skills_tool.get_tool_definitions(),
            *skill_manager_tool.get_tool_definitions(),
        ]
        self.catalog = ToolCatalog(definitions)
        self.executor = ToolExecutor(self.catalog, self.scope)
        self.execution.set_tool_invoker(self.executor.invoke_sync)
        self.runtime = RunnerRuntime(self.config, self.executor)
        self.transport = RunnerTransport(self.runtime)
        self._closed = False

    @contextmanager
    def scope(self) -> Iterator[None]:
        with ExitStack() as stack:
            stack.enter_context(execution_scope(self.execution))
            stack.enter_context(file_scope(self.files))
            stack.enter_context(desktop_scope(self.desktop))
            stack.enter_context(skill_scope(self.skills))
            yield

    def check(self) -> int:
        with invocation_scope(self.config.context()), self.scope():
            self.catalog.validate()
        return len(self.catalog.names)

    def close_resources(self) -> None:
        if self._closed:
            return
        self._closed = True
        for resource in (self.browser, self.desktop, self.execution, self.files):
            try:
                resource.close()
            except Exception:
                logger.exception("Runner resource cleanup failed: %s", type(resource).__name__)

    async def run(self, endpoint: DesktopEndpoint) -> None:
        atexit.register(self.close_resources)
        try:
            self.execution.start()
            self.browser.start()
            await self.runtime.start()
            await self.transport.run(endpoint)
        finally:
            await self.runtime.close()
            await asyncio.to_thread(self.close_resources)
            atexit.unregister(self.close_resources)
