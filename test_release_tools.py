from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.smoke_release import wait_for_uninstall


class UninstallCompletionTests(unittest.TestCase):
    def test_waits_for_uninstaller_after_application_has_been_removed(self):
        remaining = iter((True, True, False))
        def exists(path):
            return next(remaining) if path.name == 'unins000.exe' else False
        with patch.object(Path,'exists',exists), patch('scripts.smoke_release.time.sleep') as sleep:
            wait_for_uninstall(Path('isolated-test-installation'))
        self.assertEqual(sleep.call_count,2)

    def test_locked_uninstaller_causes_explicit_timeout(self):
        with patch.object(Path,'exists',return_value=True), patch('scripts.smoke_release.time.monotonic',side_effect=[0,31]):
            with self.assertRaisesRegex(TimeoutError,'Uninstaller'):
                wait_for_uninstall(Path('isolated-test-installation'))


if __name__ == '__main__':
    unittest.main()
