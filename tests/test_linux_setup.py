"""Linux installer helpers and service transactions, without GPU or service changes."""
import importlib.util
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('linux_setup', ROOT / 'installer/linux/install.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class LinuxSetupChecks(unittest.TestCase):
    def test_hardware_specific_requirements(self):
        nvidia = dict(backend='nvidia', driver='590.44')
        intel = dict(backend='intel')
        self.assertEqual(setup.runtime_requirements([intel], 'auto'), ['requirements-intel.lock.txt'])
        self.assertEqual(setup.runtime_requirements([nvidia], 'nvidia'), ['requirements.lock.txt'])
        self.assertEqual(setup.runtime_requirements([intel, nvidia], 'auto'),
                         ['requirements.lock.txt', 'requirements-intel.lock.txt'])
        for devices, requested in [([], 'auto'), ([intel], 'nvidia'), ([nvidia], 'intel'),
                                   ([dict(backend='nvidia', driver='550.1')], 'auto'), ([intel], 'amd')]:
            with self.subTest(devices=devices, requested=requested), self.assertRaises(RuntimeError):
                setup.runtime_requirements(devices, requested)

    def test_model_download_checksums_and_managed_paths(self):
        data = b'verified model'
        item = dict(file='model.xml', url='https://example.test/model.xml', sha256=setup.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(setup.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                setup.install_models(root, [item])
            self.assertEqual((root / 'models/intel/model.xml').read_bytes(), data)
            with mock.patch.object(setup.urllib.request, 'urlopen', return_value=io.BytesIO(b'corrupt')):
                with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                    setup.install_models(root, [item])
            self.assertEqual((root / 'models/intel/model.xml').read_bytes(), data)
            with self.assertRaises(ValueError):
                setup.install_models(root, [dict(item, file='../outside')])
            with self.assertRaises(ValueError):
                setup.install_models(root, [dict(item, url='http://example.test/model.xml')])

    def test_driver_boundaries(self):
        for value in ('570.190', '570.200.1', '580.82', '590.44', '600.00'):
            with self.subTest(value=value):
                self.assertTrue(setup.driver_supported(value))
        for value in ('560.100', '570.189', '580.81', '590.43', '575.99', 'invalid', '', '590.x', '590'):
            with self.subTest(value=value):
                self.assertFalse(setup.driver_supported(value))

    def test_process_options(self):
        self.assertEqual(setup.option(['--datadir=/a'], '--datadir', '/b'), '/a')
        self.assertEqual(setup.option(['--datadir', '/a'], '--datadir', '/b'), '/a')
        self.assertEqual(setup.option(['--datadir'], '--datadir', '/b'), '/b')
        self.assertEqual(setup.option(['--other', '/a'], '--datadir', '/b'), '/b')

    def test_reject_service_path_before_lock_creation(self):
        for unit in ('../bad.service', 'name/other.service', 'jellyfin', 'name.service\n'):
            with self.assertRaises(ValueError):
                with setup.setup_lock(unit):
                    self.fail('Invalid service acquired a lock')

    def test_preflight_uses_service_environment(self):
        user = SimpleNamespace(pw_name='jellyfin', pw_dir='/var/lib/jellyfin', pw_uid=1000, pw_gid=1000)
        environment = dict(CUDA_VISIBLE_DEVICES='1', LD_LIBRARY_PATH='/driver/libs', HOME='/service/home')
        with mock.patch.object(setup.os, 'getgrouplist', return_value=[1000, 44], create=True), \
             mock.patch.object(setup, 'run', return_value='ok') as command:
            setup.as_user(user, ['worker'], Path('/test'), environment)
            called = command.call_args.kwargs
            self.assertEqual(called['env']['CUDA_VISIBLE_DEVICES'], '1')
            self.assertEqual(called['env']['HOME'], '/service/home')
            self.assertEqual(called['env']['TMPDIR'], str(Path('/test')))
            self.assertEqual(called['extra_groups'], [1000, 44])

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Requires Linux root')
    def test_native_file_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            (source / 'original').write_bytes(b'owner')
            os.chown(source, 12345, 12345)
            os.chown(source / 'original', 12345, 12345)
            setup.copy_tree(source, root / 'backup')
            self.assertEqual((root / 'backup/original').stat().st_uid, 12345)
            setup.write_configuration(root / 'config.xml', b'<PluginConfiguration/>', 12345, 12345)
            self.assertEqual((root / 'config.xml').stat().st_uid, 12345)

    def test_new_configuration(self):
        root = ET.fromstring(setup.configuration(None, '/runtime/python', '/bin/ffmpeg', '/bin/ffprobe', '/cache'))
        for key, expected in dict(Enabled='true', EnableAllDevices='true', CacheMode='memory',
            Backend='auto',
            ProcessingWidth='3840', ProcessingHeight='2160', StreamWidth='3840', StreamHeight='2160',
            VsrQuality='HIGH', NvencLookahead='0', PythonPath='/runtime/python').items():
            self.assertEqual(root.findtext(key), expected)

    def test_preserve_profile(self):
        existing = b'''<PluginConfiguration><Backend>intel</Backend><Enabled>false</Enabled><EnableAllDevices>false</EnableAllDevices>
        <EnabledDeviceIds><string>living-room</string></EnabledDeviceIds><VsrQuality>ULTRA</VsrQuality>
        <ProcessingWidth>5120</ProcessingWidth><ProcessingHeight>2880</ProcessingHeight>
        <StreamWidth>3840</StreamWidth><StreamHeight>2160</StreamHeight><NvencLookahead>12</NvencLookahead>
        <UnknownSetting>keep</UnknownSetting><PythonPath>old</PythonPath></PluginConfiguration>'''
        root = ET.fromstring(setup.configuration(existing, '/new/python', '/bin/ffmpeg', '/bin/ffprobe', '/cache'))
        for key, expected in dict(Backend='intel', Enabled='true', EnableAllDevices='false', VsrQuality='ULTRA',
            ProcessingWidth='5120', ProcessingHeight='2880', StreamWidth='3840', StreamHeight='2160',
            NvencLookahead='12', UnknownSetting='keep', PythonPath='/new/python').items():
            self.assertEqual(root.findtext(key), expected)
        self.assertEqual(root.findtext('EnabledDeviceIds/string'), 'living-room')

    def test_unexpected_configuration(self):
        with self.assertRaises(ValueError):
            setup.configuration(b'<OtherPlugin/>', 'p', 'f', 'fp', 'c')

    def test_fresh_startup(self):
        stamp = '2026-11-13 13:00:00.000 +00:00'
        since = setup.datetime(2026, 11, 13, 12, 59, 59, tzinfo=setup.timezone.utc).timestamp()
        line = f'[{stamp}] [INF] Loaded plugin: "VSR Everywhere" "1.0.0.0"'
        self.assertTrue(setup.fresh_log_loaded(line, since))
        self.assertFalse(setup.fresh_log_loaded(line, since + 2))
        self.assertFalse(setup.fresh_log_loaded(line.replace('1.0.0.0', '1.0.0.1'), since))
        self.assertFalse(setup.fresh_log_loaded(line.replace(stamp, '13:00:00'), since))

    def test_transaction_success(self):
        self.transaction()

    def test_failed_startup_restores_old_files(self):
        self.transaction(fail_verify=True)

    def test_partial_copy_restores_old_files(self):
        self.transaction(fail_copy=True)

    def test_interrupt_restores_old_files(self):
        self.transaction(interrupt=True)

    def test_config_changed_during_preparation_is_retained(self):
        self.transaction(changed_config=True)

    def test_failed_initial_stop_starts_original_service(self):
        self.transaction(fail_stop=True)

    def transaction(self, fail_verify=False, fail_copy=False, changed_config=False, fail_stop=False, interrupt=False):
        with tempfile.TemporaryDirectory(prefix='vsr-linux-setup-') as temporary:
            root = Path(temporary)
            data, payload, prefix = root / 'data', root / 'payload', root / 'runtime'
            old = data / 'plugins/OldVsr'
            old.mkdir(parents=True)
            (old / 'meta.json').write_text(json.dumps(dict(guid=setup.GUID)))
            (old / 'old.dll').write_bytes(b'original')
            other = data / 'plugins/OtherPlugin'
            other.mkdir()
            (other / 'other.dll').write_bytes(b'untouched')
            config = data / 'plugins/configurations' / setup.CONFIG
            config.parent.mkdir()
            original = b'<PluginConfiguration><VsrQuality>ULTRA</VsrQuality></PluginConfiguration>'
            config.write_bytes(original)
            new = payload / 'plugin'
            new.mkdir(parents=True)
            (new / 'new.dll').write_bytes(b'new')
            target = dict(data=data, logs=root / 'logs', user=SimpleNamespace(pw_uid=1000, pw_gid=1000))
            expected = b'<PluginConfiguration/>' if changed_config else original
            events = []

            def command(parts, **kwargs):
                events.append(parts[1])
                if fail_stop and len(events) == 1:
                    raise RuntimeError('stop failed')
                return ''

            original_copy = setup.shutil.copytree

            def copy(source, destination, **kwargs):
                if Path(source) == new and fail_copy:
                    Path(destination).mkdir()
                    (Path(destination) / 'partial').write_bytes(b'partial')
                    raise OSError('copy interrupted')
                return original_copy(source, destination, **kwargs)

            native_ownership = os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0
            ownership = nullcontext() if native_ownership else mock.patch.object(setup.os, 'chown', create=True)
            with ownership, \
                 mock.patch.object(setup, 'run', side_effect=command), \
                 mock.patch.object(setup, 'wait_loaded', side_effect=KeyboardInterrupt() if interrupt else RuntimeError('not loaded') if fail_verify else None), \
                 mock.patch.object(setup.shutil, 'copytree', side_effect=copy):
                if fail_verify or fail_copy or changed_config or fail_stop or interrupt:
                    with self.assertRaises(RuntimeError):
                        setup.deploy(target, payload, b'<PluginConfiguration><Enabled>true</Enabled></PluginConfiguration>', prefix, 'jellyfin.service', expected)
                    self.assertEqual((old / 'old.dll').read_bytes(), b'original')
                    self.assertEqual(config.read_bytes(), original)
                    self.assertFalse((data / 'plugins/VSR-Everywhere').exists())
                else:
                    setup.deploy(target, payload, b'<PluginConfiguration><Enabled>true</Enabled></PluginConfiguration>', prefix, 'jellyfin.service', expected)
                    self.assertTrue((data / 'plugins/VSR-Everywhere/new.dll').is_file())
                    self.assertFalse(old.exists())
                    if native_ownership:
                        self.assertEqual(config.stat().st_uid, 1000)
                self.assertEqual(events[0], 'stop')
                self.assertEqual(events[-1], 'start')
                self.assertEqual((other / 'other.dll').read_bytes(), b'untouched')

    def test_reject_other_plugin_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            (data / 'plugins/VSR-Everywhere').mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                setup.installed_plugins(data)

    def test_file_log_fallback_without_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary)
            stamp = setup.datetime.now(setup.timezone.utc)
            (logs / 'log_today.log').write_text(f'[{stamp.isoformat()}] [INF] Loaded plugin: "VSR Everywhere" "1.0.0.0"\n')
            with mock.patch.object(setup, 'run', side_effect=RuntimeError('Journal disabled')):
                setup.wait_loaded('jellyfin.service', logs, stamp.timestamp() - 1)

    def test_managed_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(setup.beneath(root, root / 'safe'), root / 'safe')
            for path in (root, root / '../outside'):
                with self.assertRaises(ValueError):
                    setup.beneath(root, path)


if __name__ == '__main__':
    unittest.main()
