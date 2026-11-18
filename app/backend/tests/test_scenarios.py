import os
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from langsmith.utils import get_env_var, tracing_is_enabled

from app.agent import graph
from scripts import run_scenarios
from scripts.fixtures.scenario_data import TODAY, FixtureLLM, run_fixture_tool


class ScenarioTests(unittest.TestCase):
    def test_live_disables_langsmith_with_tracing_enabled_in_environment(self):
        scenario = run_scenarios.SCENARIOS[0]
        filename, _, message = scenario
        tracing_by_tool = {}

        def tool_runner(tool, **args):
            tracing_by_tool.setdefault(tool, []).append(tracing_is_enabled())
            return run_fixture_tool(tool, **args)

        run_with_doubles = partial(
            graph.run_agent, llm=FixtureLLM({filename: message}), tool_runner=tool_runner, today=TODAY
        )
        self.addCleanup(get_env_var.cache_clear)
        with (
            TemporaryDirectory() as temp_dir,
            patch.dict(os.environ, {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_TRACING_V2": "true",
                "LANGCHAIN_TRACING_V2": "true",
            }),
            patch("app.config.has_key", return_value=True),
            patch.object(graph, "run_agent", run_with_doubles),
            patch.object(run_scenarios, "SCENARIOS", (scenario,)),
            patch.object(run_scenarios, "LOG_DIR", Path(temp_dir)),
            patch("langsmith.run_trees.get_cached_client") as trace_client,
        ):
            get_env_var.cache_clear()
            self.assertIs(tracing_is_enabled(), True)
            self.assertEqual(run_scenarios._run_live(), 0)
            self.assertIs(tracing_is_enabled(), True)
            trace_client.assert_not_called()

        self.assertIn("get_boxoffice", tracing_by_tool)
        self.assertIn("get_movie_detail", tracing_by_tool)
        for tool, states in tracing_by_tool.items():
            with self.subTest(tool=tool):
                self.assertEqual(states, [False] * len(states))
