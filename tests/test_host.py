import contextlib
import io
import plistlib
import unittest
from unittest.mock import patch
from macfl23 import host


class HostTests(unittest.TestCase):
    def test_waits_for_storage_children_to_settle(self):
        clock = [0.0]
        def inventory():
            media = {}
            if clock[0] >= 1:
                media['disk4'] = {'parent': 'disk4', 'whole': True, 'mounted': False}
            if clock[0] >= 2:
                media['disk4s1'] = {'parent': 'disk4', 'whole': False, 'mounted': True}
            return 123, media
        with patch.object(host, 'camera_media', side_effect=inventory), \
             patch.object(host.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(host.time, 'sleep', side_effect=lambda x: clock.__setitem__(0, clock[0]+x)):
            host.wait_for_stable_media()
        self.assertEqual(clock[0], 5)

    def test_empty_inventory_times_out_without_unmount(self):
        clock = [0.0]
        with patch.object(host, 'camera_media', return_value=(1, {})), \
             patch.object(host.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(host.time, 'sleep', side_effect=lambda x: clock.__setitem__(0, clock[0]+x)), \
             patch.object(host, 'command') as command:
            with self.assertRaises(RuntimeError):
                host.wait_for_stable_media(seconds=2)
            command.assert_not_called()

    def test_only_target_disks_are_unmounted_and_mount_state_checked(self):
        before = {'disk8': {'parent': 'disk8', 'whole': True, 'mounted': False},
                  'disk8s1': {'parent': 'disk8', 'whole': False, 'mounted': True}}
        after = {k: dict(v, mounted=False) for k, v in before.items()}
        with patch.object(host, 'wait_for_stable_media'), \
             patch.object(host, 'camera_media', side_effect=[(1, before), (1, before), (1, after)]), \
             patch.object(host, 'command') as command, contextlib.redirect_stdout(io.StringIO()):
            host.unmount_camera()
        command.assert_called_once_with('/usr/sbin/diskutil', 'unmountDisk', '/dev/disk8')

    def test_identity_change_prevents_unmount(self):
        media = {'disk8': {'parent': 'disk8', 'whole': True, 'mounted': False}}
        with patch.object(host, 'wait_for_stable_media'), \
             patch.object(host, 'camera_media', side_effect=[(1, media), (2, media)]), \
             patch.object(host, 'command') as command:
            with self.assertRaises(RuntimeError):
                host.unmount_camera()
            command.assert_not_called()

    def test_internal_media_under_usb_is_rejected(self):
        child = {'IOObjectClass': 'IOMedia', 'BSD Name': 'disk0'}
        target = {'IOObjectClass': 'IOUSBHostDevice', 'IORegistryEntryID': 1,
                  'idVendor': 0x054c, 'idProduct': 0x0e8a, 'IORegistryEntryChildren': [child]}
        info = {'DeviceIdentifier': 'disk0', 'Internal': True, 'OSInternalMedia': True, 'BusProtocol': 'USB'}
        with patch.object(host, 'command', side_effect=[plistlib.dumps([target]), plistlib.dumps(info)]):
            with self.assertRaises(RuntimeError):
                host.camera_media()

    def test_duplicate_usb_properties_in_descendants_are_not_extra_cameras(self):
        child = {'IOObjectClass': 'IOUSBHostInterface', 'idVendor': 0x054c, 'idProduct': 0x0e8a}
        target = {'IOObjectClass': 'IOUSBHostDevice', 'IORegistryEntryID': 55,
                  'idVendor': 0x054c, 'idProduct': 0x0e8a, 'IORegistryEntryChildren': [child]}
        with patch.object(host, 'command', return_value=plistlib.dumps([target])):
            self.assertEqual(host.camera_media(), (55, {}))
