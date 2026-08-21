"""Static safety assertions for the CPU-test production package."""

import ast
from pathlib import Path
import unittest


class CpuTestSafetyTests(unittest.TestCase):
    def test_core_has_no_external_commands_sockets_or_hardware_writes(self) -> None:
        root = (
            Path(__file__).parents[1]
            / "src"
            / "hardware_validator"
            / "cpu_test"
        )
        forbidden_imports = {
            "socket",
            "subprocess",
            "requests",
            "urllib",
            "httpx",
        }
        forbidden_calls = {
            "system",
            "popen",
            "Popen",
            "check_call",
            "check_output",
            "socket",
            "create_connection",
            "write_text",
            "write_bytes",
            "unlink",
            "mkdir",
            "rename",
            "cpu_affinity",
            "nice",
            "setpriority",
            "sched_setaffinity",
        }

        for path in root.glob("*.py"):
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports = {
                    alias.name.split(".", 1)[0]
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names
                }
                calls = {
                    (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                    )
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, (ast.Attribute, ast.Name))
                }
                self.assertTrue(imports.isdisjoint(forbidden_imports))
                self.assertTrue(calls.isdisjoint(forbidden_calls))

    def test_core_contains_no_other_component_load_tests_or_scores(self) -> None:
        root = (
            Path(__file__).parents[1]
            / "src"
            / "hardware_validator"
            / "cpu_test"
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in root.glob("*.py")
        ).casefold()
        for forbidden in (
            "memory load",
            "storage load",
            "gpu load",
            "network load",
            "benchmark score",
            "sudo ",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
