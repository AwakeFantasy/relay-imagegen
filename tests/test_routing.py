import unittest
from unittest.mock import Mock, patch

from scripts import relay_imagegen


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"name": "primary", "api_key": "key-a", "base_url": "https://a.test/v1", "model": "gpt-image-2.5-flare"},
            {"name": "backup", "api_key": "key-b", "base_url": "https://b.test/v1", "model": "gpt-image-2.5-sunburst"},
        ]

    @patch("scripts.relay_imagegen.ImageClient")
    def test_transport_failure_uses_next_source(self, client_class):
        first = Mock()
        first.generate.side_effect = TimeoutError("relay timed out")
        second = Mock()
        second.generate.return_value = [b"image"]
        client_class.side_effect = [first, second]

        images, selected, route = relay_imagegen.request_with_failover(
            sources=self.sources,
            preferred_source=None,
            explicit_model=None,
            mode="generate",
            prompt="test",
            size="1024x1024",
            quality="low",
            image_paths=[],
            output_format=None,
            timeout=10,
        )

        self.assertEqual(images, [b"image"])
        self.assertEqual(selected["name"], "backup")
        self.assertEqual(route["selected_source"], "backup")
        self.assertEqual([item["status"] for item in route["attempts"]], ["failed", "success"])
        self.assertEqual([item["model"] for item in route["attempts"]],
                         ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"])
        self.assertEqual(first.generate.call_args.kwargs["model"], "gpt-image-2.5-flare")
        self.assertEqual(second.generate.call_args.kwargs["model"], "gpt-image-2.5-sunburst")
        first.generate.assert_called_once()
        second.generate.assert_called_once()

    @patch("scripts.relay_imagegen.ImageClient")
    def test_client_error_does_not_use_next_source(self, client_class):
        first = Mock()
        first.generate.side_effect = HttpError(400)
        client_class.return_value = first

        with self.assertRaises(HttpError):
            relay_imagegen.request_with_failover(
                sources=self.sources,
                preferred_source=None,
                explicit_model=None,
                mode="generate",
                prompt="test",
                size="1024x1024",
                quality="low",
                image_paths=[],
                output_format=None,
                timeout=10,
            )

        client_class.assert_called_once_with("key-a", "https://a.test/v1", timeout=10)

    @patch("scripts.relay_imagegen.ImageClient")
    def test_preferred_source_is_tried_first(self, client_class):
        backup = Mock()
        backup.generate.return_value = [b"image"]
        client_class.return_value = backup
        _, selected, _ = relay_imagegen.request_with_failover(
            sources=self.sources,
            preferred_source="backup",
            explicit_model=None,
            mode="generate",
            prompt="test",
            size="1024x1024",
            quality="low",
            image_paths=[],
            output_format=None,
            timeout=10,
        )
        self.assertEqual(selected["name"], "backup")
        client_class.assert_called_once_with("key-b", "https://b.test/v1", timeout=10)

    @patch("scripts.relay_imagegen.ImageClient")
    def test_stream_failure_does_not_fail_over(self, client_class):
        primary = Mock()
        primary.generate_stream.side_effect = TimeoutError("stream disconnected")
        client_class.return_value = primary
        with self.assertRaises(TimeoutError):
            relay_imagegen.request_with_failover(
                sources=self.sources,
                preferred_source=None,
                explicit_model=None,
                mode="generate",
                prompt="test",
                size="1024x1024",
                quality="low",
                image_paths=[],
                output_format=None,
                timeout=10,
                stream=True,
                on_partial=lambda _index, _image: None,
            )
        client_class.assert_called_once_with("key-a", "https://a.test/v1", timeout=10)

    def test_legacy_and_ordered_config_shapes(self):
        legacy = relay_imagegen.normalize_sources(
            {"api_key": "key", "base_url": "https://relay.test", "model": "gpt-image-2"}
        )
        self.assertEqual(legacy[0]["base_url"], "https://relay.test/v1")
        self.assertEqual(legacy[0]["model"], "gpt-image-2")

        ordered = relay_imagegen.normalize_sources(
            {
                "sources": [
                    {"name": "one", "api_key": "a", "base_url": "https://one.test/v1"},
                    {"name": "two", "api_key": "b", "base_url": "https://two.test/v1", "enabled": False},
                ],
            }
        )
        self.assertEqual([item["name"] for item in ordered], ["one", "two"])
        self.assertFalse(ordered[1]["enabled"])

    def test_local_file_errors_are_not_retryable(self):
        self.assertFalse(relay_imagegen.is_retryable_relay_error(FileNotFoundError("missing input")))
        self.assertFalse(relay_imagegen.is_retryable_relay_error(PermissionError("input denied")))


if __name__ == "__main__":
    unittest.main()
