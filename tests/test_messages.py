import contextlib
import io
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from macfl23 import cli
from macfl23.engine import load, verify
from macfl23.messages import simplify_message


class MessageTests(unittest.TestCase):
    def test_upstream_readback_uses_simplified_output_without_changing_data(self):
        app, core, _ = load()
        session = MagicMock()
        session.read.return_value = b'offline-program'
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            job = app.Job(directory)
            job.audit = MagicMock()
            with contextlib.redirect_stdout(output):
                job.files(session, [core.sha(b'offline-program')] * 2)
            self.assertEqual(output.getvalue(), '完整读回 cpapp-b.bin\n完整读回 appFw.so\n')
            for path in (job.root / 'readback').iterdir():
                self.assertEqual(path.read_bytes(), b'offline-program')
            job.audit.assert_called_once()
        verify()  # The pinned upstream sources remain byte-for-byte intact.

    def test_cli_failure_stays_failure_and_keeps_original_exception(self):
        error = ValueError('程式完整讀回不符；停止後續操作')
        output = io.StringIO()
        with patch.object(cli, 'load', side_effect=error), contextlib.redirect_stderr(output):
            self.assertEqual(cli.main(['check']), 1)
        self.assertEqual(output.getvalue(), 'STOPPED: 程序完整读回不符；停止后续操作\n')
        self.assertEqual(str(error), '程式完整讀回不符；停止後續操作')

    def test_dynamic_paths_identifiers_and_unknown_messages_are_preserved(self):
        path = '/tmp/備份與設定/appFw.so'
        self.assertEqual(simplify_message('備份副本雜湊不符：' + path), '备份副本哈希不符：' + path)
        self.assertEqual(simplify_message('發現額外設定變動：chip1/0x02a40001，請保存紀錄後檢查'),
                         '发现额外设置变动：chip1/0x02a40001，请保存记录后检查')
        self.assertEqual(simplify_message('需要且只能連接一台目標相機（USB 054c:0e8a）；請確認大量儲存模式及驅動'),
                         '需要且只能连接一台目标相机（USB 054c:0e8a）；请确认大量储存模式及驱动')
        for value in ('RESULT: verified_after_cold_start', path, 'USBError: [Errno 13] Access denied'):
            self.assertEqual(simplify_message(value), value)
