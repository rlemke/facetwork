"""Tests for FFL runner service CLI entry point."""

import signal
from unittest.mock import MagicMock, patch

from facetwork.config import RunnerConfig


def _make_mock_config():
    """Create a mock FFL config with real runner defaults."""
    mock_cfg = MagicMock()
    runner = RunnerConfig()
    mock_cfg.runner = runner
    return mock_cfg


class TestRunnerMain:
    """Test the runner service __main__.py entry point."""

    def _run_main(self, argv, mock_service_cls, mock_config, mock_mongo):
        """Helper to run main with mocked dependencies."""
        from facetwork.runtime.runner.__main__ import main

        mock_config.return_value = _make_mock_config()
        mock_store = MagicMock()
        mock_mongo.from_config.return_value = mock_store

        with patch("sys.argv", argv):
            main()

        return mock_service_cls

    @patch("signal.signal")
    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_default_args(self, mock_config, mock_mongo, mock_signal):
        from facetwork.runtime.runner.__main__ import main

        mock_config.return_value = _make_mock_config()
        mock_mongo.from_config.return_value = MagicMock()

        with (
            patch("sys.argv", ["afl-runner"]),
            patch("facetwork.runtime.runner.__main__.RunnerService") as mock_svc,
        ):
            main()

        mock_config.assert_called_once_with(None)
        mock_svc.assert_called_once()
        config = mock_svc.call_args.kwargs["config"]
        assert config.server_group == "default"
        assert config.service_name == "afl-runner"
        assert config.task_list == "default"
        assert config.poll_interval_ms == 1000
        assert config.max_concurrent == 2
        assert config.http_port == 8080
        mock_svc.return_value.start.assert_called_once()

    @patch("signal.signal")
    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_custom_args(self, mock_config, mock_mongo, mock_signal):
        from facetwork.runtime.runner.__main__ import main

        mock_config.return_value = _make_mock_config()
        mock_mongo.from_config.return_value = MagicMock()

        with (
            patch(
                "sys.argv",
                [
                    "afl-runner",
                    "--server-group",
                    "prod",
                    "--service-name",
                    "my-runner",
                    "--topics",
                    "ns.DoWork",
                    "ns.Process",
                    "--poll-interval",
                    "500",
                    "--max-concurrent",
                    "10",
                    "--port",
                    "9090",
                    "--config",
                    "/custom/config.json",
                ],
            ),
            patch("facetwork.runtime.runner.__main__.RunnerService") as mock_svc,
        ):
            main()

        mock_config.assert_called_once_with("/custom/config.json")
        config = mock_svc.call_args.kwargs["config"]
        assert config.server_group == "prod"
        assert config.service_name == "my-runner"
        assert config.topics == ["ns.DoWork", "ns.Process"]
        assert config.poll_interval_ms == 500
        assert config.max_concurrent == 10
        assert config.http_port == 9090

    @patch("signal.signal")
    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_keyboard_interrupt_calls_stop(self, mock_config, mock_mongo, mock_signal):
        from facetwork.runtime.runner.__main__ import main

        mock_config.return_value = _make_mock_config()
        mock_mongo.from_config.return_value = MagicMock()

        with (
            patch("sys.argv", ["afl-runner"]),
            patch("facetwork.runtime.runner.__main__.RunnerService") as mock_svc,
        ):
            mock_svc.return_value.start.side_effect = KeyboardInterrupt()
            main()

        mock_svc.return_value.stop.assert_called_once()

    @patch("signal.signal")
    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_signal_handlers_registered(self, mock_config, mock_mongo, mock_signal):
        from facetwork.runtime.runner.__main__ import main

        mock_config.return_value = _make_mock_config()
        mock_mongo.from_config.return_value = MagicMock()

        with patch("sys.argv", ["afl-runner"]), patch("facetwork.runtime.runner.__main__.RunnerService"):
            main()

        signal_calls = [c[0][0] for c in mock_signal.call_args_list]
        assert signal.SIGTERM in signal_calls
        assert signal.SIGINT in signal_calls

    @patch("signal.signal")
    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_log_file_option(self, mock_config, mock_mongo, mock_signal, tmp_path):
        from facetwork.runtime.runner.__main__ import main

        mock_config.return_value = _make_mock_config()
        mock_mongo.from_config.return_value = MagicMock()
        log_file = tmp_path / "runner.log"

        with (
            patch(
                "sys.argv",
                [
                    "afl-runner",
                    "--log-level",
                    "DEBUG",
                    "--log-file",
                    str(log_file),
                ],
            ),
            patch("facetwork.runtime.runner.__main__.RunnerService"),
        ):
            main()
