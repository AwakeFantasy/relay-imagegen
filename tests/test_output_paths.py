import argparse
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from scripts import relay_imagegen


class OutputPathTests(unittest.TestCase):
    def test_model_output_matches_preferred_source_and_failover(self):
        sources = [
            {"name": "primary", "api_key": "fake-a", "base_url": "https://a.invalid/v1", "model": "gpt-image-2.5-flare"},
            {"name": "backup", "api_key": "fake-b", "base_url": "https://b.invalid/v1", "model": "gpt-image-2"},
        ]
        for preferred in (True, False):
            with self.subTest(preferred=preferred):
                backup = Mock()
                backup.generate.return_value = [b"image"]
                primary = Mock()
                primary.generate.side_effect = TimeoutError("test timeout")
                argv = ["relay_imagegen.py", "generate", "--prompt", "test", "--out", "generated/test.png"]
                if preferred:
                    argv.extend(["--source", "backup"])
                with patch("sys.argv", argv), patch(
                    "scripts.relay_imagegen.load_config", return_value=({"sources": sources}, None)
                ), patch("scripts.relay_imagegen.ImageClient", side_effect=[backup] if preferred else [primary, backup]), patch(
                    "scripts.relay_imagegen.write_output_images"
                ), patch("scripts.relay_imagegen.verify_dimensions", return_value={}), patch(
                    "scripts.relay_imagegen.write_sidecar"
                ) as sidecar, contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(relay_imagegen.main(), 0)
                    lines = output.getvalue().splitlines()
                    self.assertIn("PLANNED_MODEL=" + ("gpt-image-2" if preferred else "gpt-image-2.5-flare"), lines)
                    self.assertIn("MODEL=gpt-image-2", lines)
                    self.assertIn("SOURCE=backup", lines)
                    self.assertEqual(sidecar.call_args.args[2]["model"], "gpt-image-2")

    def test_auto_extension_matches_output_format(self):
        for output_format, suffix in ((None, ".png"), ("png", ".png"), ("jpeg", ".jpg"), ("webp", ".webp")):
            with self.subTest(output_format=output_format):
                args = argparse.Namespace(
                    output_dir="generated", name="test", prompt_file=None,
                    mode="generate", size="1024x1024", output_format=output_format,
                )
                path = relay_imagegen.default_output_path(args)
                self.assertEqual(path.suffix, suffix)
                self.assertTrue(path.name.startswith("test-"))

    def test_explicit_output_path_is_preserved(self):
        config = {"api_key": "fake", "base_url": "https://relay.invalid/v1"}
        for output_format in ("png", "jpeg", "webp"):
            with self.subTest(output_format=output_format), patch(
                "sys.argv", ["relay_imagegen.py", "generate", "--prompt", "test",
                             "--output-format", output_format, "--out", "generated/custom.png", "--dry-run"]
            ), patch("scripts.relay_imagegen.load_config", return_value=(config, None)), patch(
                "scripts.relay_imagegen.default_output_path"
            ) as auto_path, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(relay_imagegen.main(), 0)
                auto_path.assert_not_called()
                self.assertIn("OUT=generated", output.getvalue())
                self.assertIn("custom.png", output.getvalue())


if __name__ == "__main__":
    unittest.main()
