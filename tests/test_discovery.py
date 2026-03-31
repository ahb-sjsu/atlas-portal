"""Tests for research_portal.discovery -- all external calls are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

from research_portal.discovery import (
    discover_pipelines,
    get_cpu_temps,
    get_disk,
    get_gpu_info,
    get_load,
    get_memory,
    get_system_info,
    get_tmux_sessions,
)

# ---------------------------------------------------------------------------
# get_system_info
# ---------------------------------------------------------------------------


class TestGetSystemInfo:
    """get_system_info() should return a well-typed dict with expected keys."""

    EXPECTED_KEYS = {
        "hostname",
        "os",
        "kernel",
        "cpu_model",
        "cpu_cores",
        "cpu_threads",
        "gpu_models",
        "total_ram_gb",
    }

    @patch("research_portal.discovery.shutil.which", return_value=None)
    @patch("research_portal.discovery.platform")
    def test_returns_expected_keys(self, mock_platform, _mock_which):
        mock_platform.node.return_value = "test-host"
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "6.1.0"
        mock_platform.version.return_value = "#1 SMP"
        mock_platform.processor.return_value = "x86_64"

        cpuinfo = "model name\t: Intel(R) Xeon(R) CPU\nphysical id\t: 0\ncore id\t: 0\n"
        meminfo = "MemTotal:       131072 kB\n"

        def fake_open(path, *a, **kw):
            if "cpuinfo" in str(path):
                return mock_open(read_data=cpuinfo)()
            if "meminfo" in str(path):
                return mock_open(read_data=meminfo)()
            raise FileNotFoundError(path)

        with patch("builtins.open", side_effect=fake_open):
            info = get_system_info()

        assert isinstance(info, dict)
        assert self.EXPECTED_KEYS.issubset(info.keys())
        assert info["hostname"] == "test-host"
        assert isinstance(info["gpu_models"], list)
        assert isinstance(info["total_ram_gb"], float)

    @patch("research_portal.discovery.shutil.which", return_value=None)
    @patch("research_portal.discovery.platform")
    def test_handles_missing_proc_gracefully(self, mock_platform, _mock_which):
        mock_platform.node.return_value = "fallback"
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "5.0"
        mock_platform.version.return_value = ""
        mock_platform.processor.return_value = "aarch64"

        with patch("builtins.open", side_effect=OSError("no /proc")):
            info = get_system_info()

        assert info["hostname"] == "fallback"
        assert info["cpu_model"] == "aarch64"
        assert info["total_ram_gb"] == 0.0
        assert info["gpu_models"] == []


# ---------------------------------------------------------------------------
# get_cpu_temps
# ---------------------------------------------------------------------------


class TestGetCpuTemps:
    def test_parses_sensors_output(self):
        sensors_output = (
            "coretemp-isa-0000\n"
            "Package id 0:  +62.0\u00b0C  (high = +82.0\u00b0C, crit = +100.0\u00b0C)\n"
            "Package id 1:  +55.0\u00b0C  (high = +82.0\u00b0C, crit = +100.0\u00b0C)\n"
        )
        with patch(
            "research_portal.discovery.subprocess.check_output", return_value=sensors_output
        ):
            temps = get_cpu_temps()

        assert "Package id 0" in temps
        assert temps["Package id 0"] == 62.0
        assert "Package id 1" in temps
        assert temps["Package id 1"] == 55.0

    def test_returns_empty_on_failure(self):
        with patch(
            "research_portal.discovery.subprocess.check_output",
            side_effect=FileNotFoundError,
        ):
            assert get_cpu_temps() == {}


# ---------------------------------------------------------------------------
# get_gpu_info
# ---------------------------------------------------------------------------


class TestGetGpuInfo:
    def test_parses_nvidia_smi_output(self):
        nvidia_output = (
            "0, Quadro GV100, 35, 4096, 32768, 58\n1, Quadro GV100, 12, 1024, 32768, 45\n"
        )
        with patch("research_portal.discovery.shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch(
                "research_portal.discovery.subprocess.check_output",
                return_value=nvidia_output,
            ):
                gpus = get_gpu_info()

        assert len(gpus) == 2
        assert gpus[0]["name"] == "Quadro GV100"
        assert gpus[0]["util"] == 35
        assert gpus[0]["mem_used"] == 4096
        assert gpus[1]["temp"] == 45

    def test_returns_empty_when_no_gpu_tool(self):
        with patch("research_portal.discovery.shutil.which", return_value=None):
            assert get_gpu_info() == []


# ---------------------------------------------------------------------------
# get_memory
# ---------------------------------------------------------------------------


class TestGetMemory:
    def test_parses_meminfo(self):
        meminfo = (
            "MemTotal:       131072000 kB\n"
            "MemFree:         2048000 kB\n"
            "MemAvailable:   65536000 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            mem = get_memory()

        assert mem["total_mb"] == 131072000 // 1024
        assert mem["available_mb"] == 65536000 // 1024
        assert mem["used_mb"] == mem["total_mb"] - mem["available_mb"]

    def test_returns_empty_on_failure(self):
        with patch("builtins.open", side_effect=OSError):
            assert get_memory() == {}


# ---------------------------------------------------------------------------
# get_load
# ---------------------------------------------------------------------------


class TestGetLoad:
    def test_returns_load_averages(self):
        with patch(
            "research_portal.discovery.os.getloadavg",
            create=True,
            return_value=(1.5, 2.0, 1.8),
        ):
            load = get_load()

        assert load == {"1min": 1.5, "5min": 2.0, "15min": 1.8}

    def test_returns_empty_on_windows(self):
        with patch(
            "research_portal.discovery.os.getloadavg",
            create=True,
            side_effect=OSError,
        ):
            assert get_load() == {}


# ---------------------------------------------------------------------------
# get_disk
# ---------------------------------------------------------------------------


class TestGetDisk:
    def test_returns_disk_usage(self):
        mock_statvfs = MagicMock()
        mock_statvfs.f_frsize = 4096
        mock_statvfs.f_blocks = 500000000  # ~1.8 TB
        mock_statvfs.f_bavail = 250000000

        with patch("research_portal.discovery.os.statvfs", create=True, return_value=mock_statvfs):
            disk = get_disk()

        assert "total_gb" in disk
        assert "free_gb" in disk
        assert "used_gb" in disk
        assert disk["used_gb"] == disk["total_gb"] - disk["free_gb"]


# ---------------------------------------------------------------------------
# get_tmux_sessions
# ---------------------------------------------------------------------------


class TestGetTmuxSessions:
    def test_parses_tmux_output(self):
        tmux_output = "train-run:1711800000\neval-run:1711800100\n"
        with patch(
            "research_portal.discovery.subprocess.check_output",
            return_value=tmux_output,
        ):
            sessions = get_tmux_sessions()

        assert len(sessions) == 2
        assert sessions[0]["name"] == "train-run"
        assert sessions[1]["name"] == "eval-run"

    def test_returns_empty_when_no_tmux(self):
        with patch(
            "research_portal.discovery.subprocess.check_output",
            side_effect=FileNotFoundError,
        ):
            assert get_tmux_sessions() == []


# ---------------------------------------------------------------------------
# discover_pipelines
# ---------------------------------------------------------------------------


class TestDiscoverPipelines:
    def test_returns_list(self):
        """Even with all external calls failing, discover_pipelines returns a list."""
        with patch(
            "research_portal.discovery.subprocess.check_output",
            side_effect=FileNotFoundError,
        ):
            result = discover_pipelines()

        assert isinstance(result, list)

    def test_detects_python_pipeline_from_ps(self):
        """A python process with >1% CPU should appear as a pipeline."""

        # Stub tmux (no sessions)
        def fake_check_output(cmd, **kw):
            if "tmux" in cmd[0]:
                raise FileNotFoundError
            if "ps" in cmd[0]:
                return (
                    "  1234  1000  3  45.2  2.1  3600 python3 "
                    "python3 /home/user/projects/train_model.py --epochs 10\n"
                )
            raise FileNotFoundError

        with patch(
            "research_portal.discovery.subprocess.check_output",
            side_effect=fake_check_output,
        ):
            # Also need to patch /proc/{pid}/environ reading
            with patch("builtins.open", side_effect=OSError):
                result = discover_pipelines()

        assert len(result) == 1
        assert result[0]["name"] == "train_model"
        assert result[0]["process_count"] == 1
        assert result[0]["stages"][0]["status"] == "running"

    def test_detects_multiple_pipelines(self):
        """Multiple distinct processes yield multiple pipelines."""

        def fake_check_output(cmd, **kw):
            if "tmux" in cmd[0]:
                raise FileNotFoundError
            if "ps" in cmd[0]:
                return (
                    "  100  1  0  80.0  5.0  1000 python3 "
                    "python3 /a/train.py\n"
                    "  200  1  4  60.0  3.0   500 python3 "
                    "python3 /b/evaluate.py\n"
                )
            raise FileNotFoundError

        with patch(
            "research_portal.discovery.subprocess.check_output",
            side_effect=fake_check_output,
        ):
            with patch("builtins.open", side_effect=OSError):
                result = discover_pipelines()

        names = {p["name"] for p in result}
        assert "train" in names
        assert "evaluate" in names
