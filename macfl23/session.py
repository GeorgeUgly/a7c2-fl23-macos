"""macOS USB ownership adapter, derived from the single-body Mac experiment.

Upstream firmware operations remain unchanged. Session ownership, timeouts,
identity matching and restoration belong to this module.
"""
import time
import usb.core
import usb.util
from .engine import load
from .host import unmount_camera

app, core, transport = load()


class UsbWaitTimeout(RuntimeError):
    pass


class NormalReturnTimeout(RuntimeError):
    """Service stop succeeded, but normal USB did not appear before deadline."""


class BorrowedBackend(transport.Bounded):
    def __del__(self):
        # The session alone owns release, reattach and close operations.
        pass


def normal_capture():
    unmount_camera()
    device = transport.get_device(0x0e8a)
    detached = False
    try:
        interface = device.get_active_configuration()[(0, 0)]
        if (interface.bInterfaceClass, interface.bInterfaceSubClass, interface.bInterfaceProtocol) != (8, 6, 0x50):
            raise RuntimeError('Unexpected normal USB interface')
        if device.is_kernel_driver_active(0):
            device.detach_kernel_driver(0)
            detached = True
        usb.util.claim_interface(device, 0)
        return device, detached, True
    except BaseException:
        try:
            if detached:
                device.attach_kernel_driver(0)
        finally:
            usb.util.dispose_resources(device)
        raise


def read_identity(device):
    backend = BorrowedBackend(device, 20)
    driver = transport.MscBbbDriver(backend)
    if transport.MscDevice(driver).getDeviceInfo().manufacturer.strip() != 'Sony':
        raise RuntimeError('Not a Sony storage device')
    info = transport.SonyExtCmdCamera(transport.SonyMscExtCmdDevice(driver)).getCameraInfo()
    if info.modelName != core.MODEL or not info.serial:
        raise RuntimeError('Unexpected camera model or missing identity')
    return {'model': info.modelName, 'serial_sha256': core.sha(info.serial.encode())}, backend


def wait_pid(pid, location, seconds=15):
    deadline = time.monotonic() + seconds
    while True:
        try:
            device = transport.get_device(pid)
        except RuntimeError:
            device = None
        if device is not None:
            if (device.bus, tuple(device.port_numbers or ())) != location:
                usb.util.dispose_resources(device)
                raise RuntimeError('USB physical port changed')
            return device
        if time.monotonic() >= deadline:
            raise UsbWaitTimeout('Expected USB identity did not appear: %04x' % pid)
        time.sleep(0.5)


class MacSession(transport.Session):
    audit = None

    def call(self, method, *args):
        # An interrupt during USB I/O also leaves the operation's result unknown.
        if not self.healthy:
            raise RuntimeError("Session outcome unknown; no further device commands allowed")
        self.backend.reset()
        try:
            return method(*args)
        except BaseException:
            self.healthy = False
            raise

    def jiritsu(self, cmd, data=b"", missing=False):
        try:
            return super().jiritsu(cmd, data, missing)
        except BaseException:
            self.healthy = False
            raise

    def __init__(self, expected=None):
        super().__init__(expected)
        self.normal = self.dev = None
        self.detached = self.claimed = self.switch_started = self.authenticated = False
        self.identity_backend = self.normal_backend = None
        self.exit_report = {}
        self.return_timeout = False

    def __enter__(self):
        try:
            backend = usb.backend.libusb1.get_backend(find_library=transport.libusb_package.find_library)
            if list(usb.core.find(find_all=True, idVendor=0x054c, idProduct=0x0336, backend=backend)):
                raise RuntimeError("Service identity already present; inspect the previous session first")
            self.normal, self.detached, self.claimed = normal_capture()
            self.identity, self.identity_backend = read_identity(self.normal)
            if self.expected is not None and self.identity != self.expected:
                raise RuntimeError("Connected camera does not match the verified backup")
            self.location = self.normal.bus, tuple(self.normal.port_numbers or ())
            if not self.location[1]:
                raise RuntimeError("Missing physical USB port identity")
            self.normal_backend = BorrowedBackend(self.normal, 20)
            first = transport.SonySenserAuthDevice(transport.GenericUsbDriver(self.normal_backend))
            self.switch_started = True
            first.start()
            first.authenticate()
            usb.util.dispose_resources(self.normal)
            self.normal = None
            self.dev = wait_pid(0x0336, self.location)
            time.sleep(2)
            if self.dev.is_kernel_driver_active(0):
                raise RuntimeError("Unexpected driver on service interface")
            usb.util.claim_interface(self.dev, 0)
            self.backend = BorrowedBackend(self.dev, 30)
            driver = transport.GenericUsbDriver(self.backend)
            self.auth = transport.SonySenserAuthDevice(driver)
            self.auth.start()
            self.auth.authenticate()
            self.authenticated = True
            self.channel = transport.SonySenserDevice(driver)
            self.camera = transport.SonySenserCamera(self.channel)
            self.backend.budget = 180
            if len(self.call(self.camera.readHasp)) != 88:
                raise RuntimeError("Unexpected ProductInfo response")
            return self
        except BaseException:
            self._close()
            raise

    def _close(self):
        errors = []
        if self.dev is not None:
            can_exit = self.authenticated and self.healthy
            if can_exit:
                try:
                    self.backend.reset()
                    self.auth.stop()
                    self.exit_report["service_exit_request"] = "sent"
                except BaseException as exc:
                    errors.append("service stop: " + str(exc))
                    can_exit = False
            else:
                self.exit_report["service_exit_request"] = "not_sent_unknown_session_state"
                errors.append("Session outcome unknown; no further device commands sent")
            try:
                usb.util.dispose_resources(self.dev)
            except BaseException as exc:
                errors.append("close service handle: " + str(exc))
            self.dev = None
            if can_exit:
                try:
                    print("服务模式已退出，等待普通 USB 连接；若相机询问连接模式，请选第2项「影像传输（MSC）」。", flush=True)
                    normal = wait_pid(0x0e8a, self.location, seconds=60)
                    usb.util.dispose_resources(normal)
                    self.exit_report["normal_usb_returned"] = True
                except BaseException as exc:
                    self.return_timeout = isinstance(exc, UsbWaitTimeout)
                    errors.append("normal USB return: " + str(exc))
        if self.normal is not None:
            if not self.switch_started:
                for operation, needed in [
                    (lambda: usb.util.release_interface(self.normal, 0), self.claimed),
                    (lambda: self.normal.attach_kernel_driver(0), self.detached),
                ]:
                    if needed:
                        try:
                            operation()
                        except BaseException as exc:
                            errors.append("normal USB restoration: " + str(exc))
            else:
                errors.append("Authentication transition incomplete; no stop/retry sent")
            try:
                usb.util.dispose_resources(self.normal)
            except BaseException as exc:
                errors.append("close normal handle: " + str(exc))
            self.normal = None
        self.exit_report["errors"] = errors
        if type(self).audit is not None:
            try:
                type(self).audit(dict(self.exit_report))
            except Exception as exc:
                errors.append("session log: " + str(exc))
        return errors

    def __exit__(self, exc_type, exc, tb):
        errors = self._close()
        if errors and exc_type is None:
            if self.return_timeout and len(errors) == 1:
                raise NormalReturnTimeout("; ".join(errors))
            raise RuntimeError("; ".join(errors))
        return False
