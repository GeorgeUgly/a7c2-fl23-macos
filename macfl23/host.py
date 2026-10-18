"""macOS registry inventory and narrowly targeted, non-forced unmounts."""
import plistlib
import re
import subprocess
import time


def command(*args):
    return subprocess.check_output(args, timeout=30)


def walk(item):
    if isinstance(item, list):
        for child in item:
            yield from walk(child)
    elif isinstance(item, dict):
        yield item
        yield from walk(item.get('IORegistryEntryChildren', []))


def camera_media():
    tree = plistlib.loads(command('/usr/sbin/ioreg', '-p', 'IOService', '-l', '-a'))
    targets = [x for x in walk(tree) if x.get('IOObjectClass') == 'IOUSBHostDevice'
               and x.get('idVendor') == 0x054c and x.get('idProduct') == 0x0e8a]
    if len(targets) != 1:
        raise RuntimeError('需要且只能连接一台影像传输（MSC）模式的 A7C II（054c:0e8a）')
    registry = targets[0].get('IORegistryEntryID')
    if registry is None:
        raise RuntimeError('Missing USB registry identity')
    media = {}
    for item in walk(targets[0]):
        if item.get('IOObjectClass') != 'IOMedia' or not item.get('BSD Name'):
            continue
        name = item['BSD Name']
        if not re.fullmatch(r'disk[0-9]+(?:s[0-9]+)*', name):
            raise RuntimeError('Unexpected disk identifier')
        info = plistlib.loads(command('/usr/sbin/diskutil', 'info', '-plist', name))
        if (info.get('DeviceIdentifier') != name or info.get('Internal') is not False
                or info.get('OSInternalMedia') is not False or info.get('BusProtocol') != 'USB'):
            raise RuntimeError('Camera disk external-USB identity check failed')
        media[name] = {'parent': info.get('ParentWholeDisk'), 'whole': info.get('WholeDisk'),
                       'mounted': bool(info.get('MountPoint'))}
    for row in media.values():
        if row['parent'] not in media or media[row['parent']]['whole'] is not True:
            raise RuntimeError('Disk parent is not a verified camera disk')
    return registry, media


def wait_for_stable_media(seconds=20):
    deadline = time.monotonic() + seconds
    previous = stable_since = None
    while True:
        registry, media = camera_media()
        topology = (registry, tuple(sorted((name, row['parent'], row['whole'])
                                          for name, row in media.items())))
        now = time.monotonic()
        if any(row['whole'] for row in media.values()):
            if topology != previous:
                previous, stable_since = topology, now
            elif now - stable_since >= 3:
                return
        else:
            previous = stable_since = None
        if now >= deadline:
            raise RuntimeError('Camera disks did not stabilize; USB capture not attempted')
        time.sleep(0.5)


def unmount_camera():
    wait_for_stable_media()
    registry, media = camera_media()
    if not any(row['whole'] for row in media.values()):
        raise RuntimeError('No camera media found; capture refused')
    for disk in sorted(name for name, row in media.items() if row['whole']):
        now, current = camera_media()
        if now != registry or set(current) != set(media):
            raise RuntimeError('Camera/disk identity changed before unmount')
        print('正常卸载已核实的相机磁盘：' + disk, flush=True)
        command('/usr/sbin/diskutil', 'unmountDisk', '/dev/' + disk)
    now, current = camera_media()
    if now != registry or set(current) != set(media) or any(x['mounted'] for x in current.values()):
        raise RuntimeError('Camera volumes did not all unmount; no USB capture attempted')
