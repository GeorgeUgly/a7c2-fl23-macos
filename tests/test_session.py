"""Offline session tests. Every USB entry point is replaced before use."""
import contextlib
import unittest
from unittest.mock import MagicMock, patch
import macfl23.session as m
IDENTITY = {'model': 'ILCE-7CM2', 'serial_sha256': 'offline-test'}

class SessionTests(unittest.TestCase):
    def simulate(self, failure=None):
        normal = MagicMock(bus=0, port_numbers=(1,))
        service = MagicMock()
        service.is_kernel_driver_active.return_value = False
        first, second = MagicMock(), MagicMock()
        if failure == "first_auth":
            first.authenticate.side_effect = TimeoutError("unknown first authentication")
        if failure == "second_auth":
            second.authenticate.side_effect = TimeoutError("unknown second authentication")
        if failure == "stop":
            second.stop.side_effect = TimeoutError("unknown stop")
        camera = MagicMock()
        camera.readHasp.return_value = bytes(88)
        seen = dict(IDENTITY)
        if failure == "identity":
            seen["serial_sha256"] = "different-body"
        wait = MagicMock(side_effect=[service, normal])
        if failure == "normal_return":
            wait.side_effect = [service, m.UsbWaitTimeout("normal USB timeout")]
        session = m.MacSession(IDENTITY)
        error = None
        with contextlib.ExitStack() as stack:
            for obj, name, value in [
                (m, "normal_capture", lambda: (normal, True, True)),
                (m, "read_identity", lambda dev: (seen, object())),
                (m, "BorrowedBackend", lambda *args: MagicMock()),
                (m, "wait_pid", wait),
                (m.usb.backend.libusb1, "get_backend", lambda **kw: object()),
                (m.usb.core, "find", lambda **kw: []),
                (m.usb.util, "claim_interface", MagicMock()),
                (m.usb.util, "release_interface", MagicMock()),
                (m.usb.util, "dispose_resources", MagicMock()),
                (m.transport, "GenericUsbDriver", lambda x: x),
                (m.transport, "SonySenserDevice", lambda x: MagicMock()),
                (m.transport, "SonySenserCamera", lambda x: camera),
                (m.time, "sleep", lambda n: None),
            ]:
                stack.enter_context(patch.object(obj, name, value))
            stack.enter_context(patch.object(m.transport, "SonySenserAuthDevice", side_effect=[first, second]))
            try:
                with session:
                    if failure in ("read", "interrupt"):
                        def fail():
                            raise KeyboardInterrupt() if failure == "interrupt" else TimeoutError("unknown read")
                        session.call(fail)
            except BaseException as exc:
                error = exc
        return session, first, second, normal, wait, error

    def test_success_exits_service_and_observes_normal_usb(self):
        session, first, second, normal, wait, error = self.simulate()
        self.assertIsNone(error)
        first.stop.assert_not_called()
        second.stop.assert_called_once()
        self.assertTrue(session.exit_report["normal_usb_returned"])
        self.assertEqual(wait.call_count, 2)

    def test_identity_mismatch_restores_driver_without_authentication(self):
        session, first, second, normal, wait, error = self.simulate("identity")
        self.assertIsInstance(error, RuntimeError)
        first.start.assert_not_called()
        normal.attach_kernel_driver.assert_called_once_with(0)
        wait.assert_not_called()

    def test_failed_authentication_never_sends_stop_or_retries(self):
        for failure in ("first_auth", "second_auth"):
            with self.subTest(failure=failure):
                session, first, second, normal, wait, error = self.simulate(failure)
                self.assertIsInstance(error, TimeoutError)
                first.stop.assert_not_called()
                second.stop.assert_not_called()
                self.assertLessEqual(wait.call_count, 1)

    def test_timeout_and_interrupt_prevent_further_device_commands(self):
        for failure in ("read", "interrupt"):
            with self.subTest(failure=failure):
                session, first, second, normal, wait, error = self.simulate(failure)
                self.assertIsNotNone(error)
                self.assertFalse(session.healthy)
                second.stop.assert_not_called()
                method = MagicMock()
                with self.assertRaises(RuntimeError):
                    session.call(method)
                method.assert_not_called()

    def test_stop_failure_is_reported_and_no_normal_probe_follows(self):
        session, first, second, normal, wait, error = self.simulate("stop")
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(wait.call_count, 1)
        self.assertTrue(session.exit_report["errors"])

    def test_malformed_jiritsu_reply_marks_session_unknown(self):
        session = m.MacSession(IDENTITY)
        session.backend = MagicMock()
        session.channel = MagicMock()
        session.channel.sendSenserPacket.return_value = (0, b"x")
        with self.assertRaises(RuntimeError):
            session.jiritsu(17, b"\0\xff\0")
        self.assertFalse(session.healthy)


    def test_only_normal_return_timeout_has_distinct_completion_type(self):
        session, first, second, normal, wait, error = self.simulate('normal_return')
        self.assertIsInstance(error, m.NormalReturnTimeout)
        second.stop.assert_called_once()
        self.assertEqual(session.exit_report['service_exit_request'], 'sent')
        self.assertEqual(len(session.exit_report['errors']), 1)
