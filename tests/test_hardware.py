import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from main import create_app
from web.hardware import memory_usage
from web.logging_config import setup_logging


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setup_logging({'LOG_DIR': self.directory.name})

    @patch('web.hardware.subprocess.run')
    @patch('web.hardware.Path.read_text')
    def test_jetson_shared_memory_uses_available_memory_without_cuda_tools(self, read, run):
        read.side_effect = ['NVIDIA Jetson AGX Thor Developer Kit\0', 'MemTotal: 134217728 kB\nMemAvailable: 100663296 kB\nMemFree: 1024 kB\n']
        self.assertEqual(memory_usage(), {'available': True, 'kind': 'shared', 'used_bytes': 32 * 1024 ** 3,
                                         'total_bytes': 128 * 1024 ** 3, 'used_percent': 25.0})
        run.assert_not_called()

    @patch('web.hardware.Path.read_text')
    def test_reclaimable_cache_is_not_counted_as_unavailable(self, read):
        read.side_effect = ['NVIDIA Jetson', 'MemTotal: 1000 kB\nMemAvailable: 800 kB\nMemFree: 100 kB\nCached: 700 kB\n']
        self.assertEqual(memory_usage()['used_bytes'], 200 * 1024)

    @patch('web.hardware.subprocess.run')
    @patch('web.hardware.shutil.which', return_value='/usr/bin/nvidia-smi')
    @patch('web.hardware.Path.read_text', side_effect=FileNotFoundError())
    def test_dedicated_memory_is_reported_across_devices(self, read, which, run):
        run.return_value = Mock(stdout='8192, 2048\n12288, 3072\n')
        result = memory_usage()
        self.assertEqual(result['kind'], 'dedicated')
        self.assertEqual(result['total_bytes'], 20480 * 1024 ** 2)
        self.assertEqual(result['used_bytes'], 5120 * 1024 ** 2)
        self.assertEqual(result['used_percent'], 25.0)
        self.assertEqual(run.call_args.kwargs['timeout'], 2)
        self.assertTrue(run.call_args.kwargs['check'])

    @patch('web.hardware.subprocess.run')
    @patch('web.hardware.shutil.which', return_value='/usr/bin/nvidia-smi')
    @patch('web.hardware.Path.read_text', return_value='Desktop computer')
    def test_unsupported_gpu_counters_are_not_displayed_as_zero(self, read, which, run):
        for output in ('[N/A], [N/A]\n', '', '8192, 9000', 'nan, 0', 'inf, 0', '8192, -1', '0.000000001, 0'):
            with self.subTest(output=output):
                run.return_value = Mock(stdout=output)
                self.assertFalse(memory_usage()['available'])

    @patch('web.hardware.Path.read_text')
    def test_invalid_shared_memory_data_is_unavailable(self, read):
        for data in ('MemTotal: 1000 kB\n', 'MemTotal: 1000 kB\nMemAvailable: 2000 kB\n', 'MemTotal: 0 kB\nMemAvailable: 0 kB\n'):
            with self.subTest(data=data):
                read.side_effect = ['NVIDIA Jetson AGX Thor', data]
                self.assertFalse(memory_usage()['available'])

    @patch('web.hardware.shutil.which', return_value=None)
    @patch('web.hardware.Path.read_text', side_effect=FileNotFoundError())
    def test_machine_without_gpu_reporting_is_unavailable(self, read, which):
        self.assertFalse(memory_usage()['available'])

    @patch('web.hardware.subprocess.run', side_effect=subprocess.TimeoutExpired('nvidia-smi', 2))
    @patch('web.hardware.shutil.which', return_value='/usr/bin/nvidia-smi')
    @patch('web.hardware.Path.read_text', return_value='Desktop computer')
    def test_hardware_timeout_is_handled(self, read, which, run):
        self.assertFalse(memory_usage()['available'])

    @patch('main.memory_usage', return_value={'available': True, 'kind': 'shared', 'used_bytes': 1, 'total_bytes': 4, 'used_percent': 25})
    def test_api_is_not_cached_and_does_not_contact_ollama(self, usage):
        app = create_app({'TESTING': True, 'DATABASE': str(Path(self.directory.name) / 'test.sqlite3'),
                          'LOG_DIR': self.directory.name})
        with app.test_client() as client, patch('main.requests.get') as get:
            response = client.get('/api/system/vram')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['used_percent'], 25)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            get.assert_not_called()
