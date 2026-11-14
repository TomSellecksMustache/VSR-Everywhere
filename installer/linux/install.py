"""Native Linux/systemd setup for VSR Everywhere. Run through the bundled shell installer."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

GUID = '7ca129d0-7454-41c7-aad6-55250b1a7b5b'
CONFIG = 'Jellyfin.Plugin.VsrEverywhere.xml'
LOG = logging.getLogger('VSR Everywhere')
DEFAULTS = dict(Backend='nvidia', Enabled='true', EnableAllDevices='true', VideoMbps='20',
    VsrQuality='HIGH', VsrStrength='0.75', NvencPreset='p4', NvencTune='default',
    NvencLookahead='0', NvencSpatialAq='false', NvencTemporalAq='false', NvencCq='0',
    SegmentType='mpegts', MaxSessions='1', SessionMinutes='180', CacheMode='memory',
    MemoryBudgetMb='0', SeekLookaheadSeconds='90', SeekCatchupSeconds='20', CacheMaxGb='10',
    CacheMinFreeGb='5', CacheIdleMinutes='10', EnableRollingCache='false', RollingRewindMinutes='10',
    ProcessingWidth='3840', ProcessingHeight='2160', StreamWidth='3840', StreamHeight='2160')


def run(command, *, log=True, **kwargs):
    result = subprocess.run([str(part) for part in command], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=kwargs.pop('timeout', 120), **kwargs)
    if result.stdout and log:
        LOG.info('%s', result.stdout.rstrip())
    if result.returncode:
        raise RuntimeError(f'{command[0]} failed ({result.returncode}): {result.stdout[-4000:]}')
    return result.stdout


def driver_supported(text):
    parts = text.strip().split('.')
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        return False
    major, minor = map(int, parts[:2])
    return major > 590 or (major == 570 and minor >= 190) or \
        (major == 580 and minor >= 82) or (major == 590 and minor >= 44)


def option(arguments, name, fallback):
    for index, argument in enumerate(arguments):
        if argument.startswith(name + '='):
            return argument.partition('=')[2]
        if argument == name and index + 1 < len(arguments):
            return arguments[index + 1]
    return fallback


def beneath(root, path):
    root, path = Path(root).resolve(), Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.resolve() == root:
        raise ValueError(f'Unsafe managed path: {path}')
    return path


def verify_payload(payload):
    hashes = json.loads((payload / 'payload.sha256.json').read_text(encoding='utf-8'))
    for name, digest in hashes.items():
        path = beneath(payload, payload / name)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Package verification failed: {name}')


def discover(args):
    properties = dict(line.split('=', 1) for line in run(['systemctl', 'show', args.service,
        '--property=User,Group,MainPID,ActiveState,LoadState']).splitlines() if '=' in line)
    if properties.get('ActiveState') != 'active' or properties.get('LoadState') != 'loaded':
        raise RuntimeError('Start the initialized Jellyfin service before running setup.')
    pid = int(properties.get('MainPID', '0'))
    if pid <= 0:
        raise RuntimeError('Jellyfin has no active service process.')
    process = Path('/proc') / str(pid)
    arguments = (process / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
    environment = dict(item.split('=', 1) for item in (process / 'environ').read_bytes()
                       .decode().split('\0') if '=' in item)
    import pwd
    account = properties.get('User') or 'root'
    user = pwd.getpwuid(int(account)) if account.isdecimal() else pwd.getpwnam(account)
    if user.pw_uid == 0:
        raise RuntimeError('Jellyfin must run under a dedicated, non-root service account.')
    data = Path(args.data_dir or option(arguments, '--datadir', environment.get('JELLYFIN_DATA_DIR', '/var/lib/jellyfin'))).resolve()
    config = Path(args.config_dir or option(arguments, '--configdir', environment.get('JELLYFIN_CONFIG_DIR', '/etc/jellyfin'))).resolve()
    if not data.is_dir() or not (config / 'system.xml').is_file():
        raise RuntimeError('Jellyfin data/configuration was not found. Use --data-dir and --config-dir for custom paths.')
    executable = arguments[:2] if Path(arguments[0]).name == 'dotnet' else arguments[:1]
    version = run([*executable, '--version'])
    if not re.search(r'\b12\.1\.\d+(?:\.\d+)?\b', version):
        raise RuntimeError('This build requires Jellyfin 12.1.x.')
    selected_ffmpeg = args.ffmpeg or option(arguments, '--ffmpeg', environment.get('JELLYFIN_FFMPEG', ''))
    ffmpeg = next((Path(path) for path in [selected_ffmpeg, '/usr/lib/jellyfin-ffmpeg/ffmpeg',
        '/usr/lib64/jellyfin-ffmpeg/ffmpeg', '/usr/share/jellyfin-ffmpeg/ffmpeg', shutil.which('ffmpeg')]
        if path and Path(path).is_file() and os.access(path, os.X_OK)), None)
    ffprobe = Path(args.ffprobe) if args.ffprobe else ffmpeg.with_name('ffprobe') if ffmpeg else None
    if ffmpeg is None or ffprobe is None or not ffprobe.is_file():
        raise RuntimeError('Jellyfin FFmpeg/FFprobe was not found. Install jellyfin-ffmpeg or provide --ffmpeg and --ffprobe.')
    logs = Path(args.log_dir or option(arguments, '--logdir', environment.get('JELLYFIN_LOG_DIR', '/var/log/jellyfin'))).resolve()
    return dict(data=data, config=config, user=user, ffmpeg=ffmpeg, ffprobe=ffprobe, logs=logs, environment=environment)


def as_user(user, command, cwd, environment):
    environment = dict(environment, HOME=environment.get('HOME', user.pw_dir), USER=user.pw_name, LOGNAME=user.pw_name,
                       TMPDIR=str(cwd), PYTHONDONTWRITEBYTECODE='1')
    return run(command, cwd=cwd, env=environment, user=user.pw_uid, group=user.pw_gid,
               extra_groups=os.getgrouplist(user.pw_name, user.pw_gid), timeout=600)


def configuration(existing, python, ffmpeg, ffprobe, cache):
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(existing, parser=parser) if existing else ET.Element('PluginConfiguration')
    if root.tag != 'PluginConfiguration':
        raise ValueError('Unexpected VSR Everywhere configuration XML.')
    for key, value in DEFAULTS.items():
        if root.find(key) is None:
            ET.SubElement(root, key).text = value
    for key, value in dict(Enabled='true', PythonPath=str(python), FfmpegPath=str(ffmpeg),
                           FfprobePath=str(ffprobe), CacheDirectory=str(cache)).items():
        node = root.find(key)
        if node is None:
            node = ET.SubElement(root, key)
        node.text = value
    if root.find('EnabledDeviceIds') is None:
        ET.SubElement(root, 'EnabledDeviceIds')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def installed_plugins(data):
    plugins = beneath(data, data / 'plugins')
    if not plugins.is_dir():
        return []
    found = []
    for folder in plugins.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        metadata = folder / 'meta.json'
        if metadata.is_file() and not metadata.is_symlink():
            try:
                if str(json.loads(metadata.read_text(encoding='utf-8-sig')).get('guid', '')).lower() == GUID:
                    found.append(folder)
            except (ValueError, OSError):
                pass
    destination = plugins / 'VSR-Everywhere'
    if destination.exists() and destination not in found:
        raise RuntimeError('The destination plugin folder is not identified as VSR Everywhere; refusing to replace it.')
    return found


def copy_tree(source, destination):
    shutil.copytree(source, destination, symlinks=True)
    for path in [source, *source.rglob('*')]:
        info = path.lstat()
        os.chown(destination / path.relative_to(source), info.st_uid, info.st_gid, follow_symlinks=False)


def write_configuration(path, data, uid, gid, mode=0o600):
    temporary = beneath(path.parent, path.with_name(path.name + '.' + uuid.uuid4().hex))
    try:
        with temporary.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


LOADED = r'Loaded plugin:\s*"?VSR Everywhere"?\s+"?1\.1\.0(?:\.0)?"?\s*$'


def fresh_log_loaded(text, since):
    for line in text.splitlines():
        match = re.match(r'^\[([^\]]+)\].*' + LOADED, line)
        if match:
            try:
                stamp = datetime.fromisoformat(re.sub(r'\s+([+-]\d\d:\d\d|Z)$', r'\1', match[1]))
                if stamp.tzinfo is not None and stamp.timestamp() >= since:
                    return True
            except ValueError:
                pass
    return False


def wait_loaded(service, logs, since):
    # Journal timestamps are generated by systemd, so old startup entries cannot pass.
    pattern = re.compile(LOADED)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            output = run(['journalctl', '--unit', service, '--since', '@' + str(since), '--output=cat', '--no-pager'], log=False)
        except (RuntimeError, OSError):
            output = ''
        if any(pattern.search(line) for line in output.splitlines()):
            return
        for path in sorted(logs.glob('log*.log'), reverse=True)[:3]:
            try:
                with path.open('rb') as source:
                    source.seek(max(0, path.stat().st_size - 2 * 1024**2))
                    if fresh_log_loaded(source.read().decode('utf-8', errors='replace'), since):
                        return
            except OSError:
                continue
        time.sleep(1)
    raise RuntimeError('Jellyfin did not confirm loading VSR Everywhere 1.1.0. Inspect its journal and startup logs.')


def deploy(target, payload, settings, prefix, service, expected_config):
    data, user = target['data'], target['user']
    previous = installed_plugins(data)
    plugins = beneath(data, data / 'plugins')
    if not plugins.exists():
        plugins.mkdir()
        os.chown(plugins, user.pw_uid, user.pw_gid)
    config_dir = beneath(data, plugins / 'configurations')
    if not config_dir.exists():
        config_dir.mkdir()
        os.chown(config_dir, user.pw_uid, user.pw_gid)
    config_path = beneath(data, config_dir / CONFIG)
    destination = beneath(data, plugins / 'VSR-Everywhere')
    backup = prefix / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    original_config = config_path.read_bytes() if config_path.exists() else None
    config_stat = config_path.stat() if config_path.exists() else None
    saved = []
    removed = []
    changed_config = False
    new_plugin = False
    try:
        run(['systemctl', 'stop', service])
        original_config = config_path.read_bytes() if config_path.exists() else None
        config_stat = config_path.stat() if config_path.exists() else None
        if original_config != expected_config:
            raise RuntimeError('VSR settings changed during preparation. Rerun setup to preserve the latest settings.')
        for folder in previous:
            saved_path = backup / folder.name
            copy_tree(folder, saved_path)
            saved.append((folder, saved_path))
        if original_config is not None:
            saved_config = backup / 'configuration.xml'
            saved_config.write_bytes(original_config)
            os.chmod(saved_config, stat.S_IMODE(config_stat.st_mode))
            os.chown(saved_config, config_stat.st_uid, config_stat.st_gid)
        record = dict(product='VSR Everywhere', data=str(data), config=str(config_path),
                      plugins=[str(path) for path in previous], had_config=original_config is not None,
                      config_metadata=dict(uid=config_stat.st_uid, gid=config_stat.st_gid,
                          mode=stat.S_IMODE(config_stat.st_mode)) if config_stat else None)
        (backup / 'restore.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        for folder, _ in saved:
            removed.append(folder)
            shutil.rmtree(beneath(data, folder))
        new_plugin = True
        shutil.copytree(payload / 'plugin', destination)
        for path in [destination, *destination.rglob('*')]:
            os.chown(path, user.pw_uid, user.pw_gid)
        write_configuration(config_path, settings, user.pw_uid, user.pw_gid)
        changed_config = True
        since = time.time()
        run(['systemctl', 'start', service])
        wait_loaded(service, target['logs'], since)
        LOG.info('Installed VSR Everywhere 1.1.0. Backup: %s', backup)
    except BaseException as failure:
        errors = []
        try:
            run(['systemctl', 'stop', service])
        except BaseException as stop_error:
            errors.append(str(stop_error))
        try:
            if new_plugin and destination.exists():
                shutil.rmtree(beneath(data, destination))
            for folder, saved_path in saved:
                if folder in removed:
                    if folder.exists():
                        shutil.rmtree(beneath(data, folder))
                    copy_tree(saved_path, folder)
            if changed_config:
                if original_config is None:
                    config_path.unlink(missing_ok=True)
                else:
                    write_configuration(config_path, original_config, config_stat.st_uid,
                                        config_stat.st_gid, stat.S_IMODE(config_stat.st_mode))
        except BaseException as restore_error:
            errors.append(str(restore_error))
        try:
            run(['systemctl', 'start', service])
        except BaseException as start_error:
            errors.append(str(start_error))
        message = 'Setup failed; previous files restored.' if not errors else 'Rollback needs attention: ' + '; '.join(errors)
        raise RuntimeError(f'{failure}\n{message}\nBackup: {backup}') from failure


@contextmanager
def setup_lock(service):
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', service):
        raise ValueError('Use a systemd service name such as jellyfin.service.')
    import fcntl
    path = '/run/lock/vsr-everywhere-' + service + '.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise RuntimeError('The setup lock is not a secure root-owned file.')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description='VSR Everywhere 1.1.0 Linux service setup')
    parser.add_argument('--install-dir', default='/opt/vsr-everywhere')
    parser.add_argument('--service', default='jellyfin.service')
    for name in ('data-dir', 'config-dir', 'log-dir', 'ffmpeg', 'ffprobe'):
        parser.add_argument('--' + name)
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64') or os.geteuid() != 0:
        parser.error('Run the Linux x86-64 installer as root.')
    libc, version = platform.libc_ver()
    if libc != 'glibc' or tuple(map(int, version.split('.')[:2])) < (2, 28):
        parser.error('The NVIDIA runtime requires glibc 2.28 or newer; musl/Alpine and ARM are unsupported.')
    def interrupted(number, frame):
        raise KeyboardInterrupt(f'Interrupted by signal {number}')
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, interrupted)
    payload = Path(__file__).resolve().parent
    prefix = Path(args.install_dir).resolve()
    if prefix == Path('/') or not prefix.is_dir():
        parser.error('Use the bundled shell installer to create a private installation directory.')
    for path in (prefix, *prefix.parents, *(prefix / name for name in ('bootstrap', 'downloads', 'jobs', 'runtimes', 'backups'))):
        info = path.stat()
        if path.is_symlink() or not path.is_dir() or info.st_uid != 0 or info.st_mode & 0o022:
            parser.error(f'The private runtime path must be root-owned and not writable by other users: {path}')
    log = payload / 'setup.log'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S',
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log, encoding='utf-8')])
    verify_payload(payload)
    with setup_lock(args.service):
        target = discover(args)
        user = target['user']
        test_dir = payload / 'test'
        test_dir.mkdir(mode=0o700)
        os.chown(test_dir, user.pw_uid, user.pw_gid)
        gpu = as_user(user, ['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'], test_dir, target['environment'])
        driver = gpu.splitlines()[0].rsplit(',', 1)[-1].strip()
        if not driver_supported(driver):
            raise RuntimeError('Linux VSR requires NVIDIA 570.190+, 580.82+, 590.44+, or a newer driver branch.')
        plugins = installed_plugins(target['data'])
        config_path = target['data'] / 'plugins/configurations' / CONFIG
        existing = config_path.read_bytes() if config_path.is_file() else None
        runtime_id = sha256((payload / 'requirements.lock.txt').read_bytes() + (payload / 'runtime.json').read_bytes()).hexdigest()
        runtime = beneath(prefix, prefix / 'runtimes' / runtime_id[:16])
        python = runtime / 'bin/python3'
        marker = runtime / '.verified'
        reuse = marker.is_file() and marker.read_text() == runtime_id and python.is_file()
        if not reuse and shutil.disk_usage(prefix).free < 25 * 1024**3:
            raise RuntimeError('A fresh runtime needs 25 GiB free. Choose another --install-dir.')
        settings = configuration(existing, python, target['ffmpeg'], target['ffprobe'], target['data'] / 'cache/vsr-everywhere')
        LOG.info('Service: %s; account: %s; data: %s; runtime: %s', args.service, user.pw_name, target['data'], runtime)
        if args.check:
            LOG.info('Platform, package, service, GPU access, driver, configuration, and space checks passed.')
            return
        if not args.yes and input('Install with these settings? [Y/n] ').strip().lower() not in ('', 'y', 'yes'):
            return
        if not reuse:
            LOG.info('Installing the private CUDA/VFX runtime; Jellyfin is still running...')
            runtime.parent.mkdir(parents=True, exist_ok=True)
            if runtime.exists():
                raise RuntimeError(f'Incomplete runtime at {runtime}; move it aside and retry.')
            run([sys.executable, '-m', 'venv', '--copies', runtime])
            try:
                run([python, '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check', '--progress-bar', 'off',
                    '--cache-dir', prefix / 'downloads/pip', '--require-hashes', '--only-binary=:all:',
                    '-r', payload / 'requirements.lock.txt'], timeout=3600)
                run([python, '-m', 'pip', 'check'])
                marker.write_text(runtime_id)
            except BaseException:
                shutil.rmtree(beneath(prefix, runtime))
                raise
        LOG.info('Testing VSR, HEVC, AAC, and HLS as the Jellyfin service account...')
        profile = ET.fromstring(settings)
        command = [python, payload / 'Test-Runtime.py', '--worker', payload / 'plugin/worker/vsr_everywhere.py',
                   '--ffmpeg', target['ffmpeg'], '--ffprobe', target['ffprobe'], '--report', test_dir / 'runtime-gpu.json']
        for key, flag in [('ProcessingWidth', 'processing-width'), ('ProcessingHeight', 'processing-height'),
                          ('StreamWidth', 'stream-width'), ('StreamHeight', 'stream-height'), ('VsrQuality', 'quality')]:
            command += ['--' + flag, profile.findtext(key)]
        as_user(user, command, test_dir, target['environment'])
        LOG.info('Testing the compatible FFmpeg/NVENC lookahead path...')
        legacy = command.copy()
        legacy[legacy.index('--report') + 1] = test_dir / 'runtime-ffmpeg.json'
        legacy += ['--nvenc-lookahead', str(min(32, max(1, int(profile.findtext('NvencLookahead') or '0'))))]
        as_user(user, legacy, test_dir, target['environment'])
        deploy(target, payload, settings, prefix, args.service, existing)
        LOG.info('Setup complete. Review VSR Everywhere settings in Jellyfin. Log: %s', log)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f'Setup failed: {error}', file=sys.stderr, flush=True)
        sys.exit(1)
