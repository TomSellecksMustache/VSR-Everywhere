"""Re-render the original comparison frames through this branch's real worker."""
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / 'installer/build/test-encoder/ffmpeg.exe'
FFPROBE = FFMPEG.with_name('ffprobe.exe')
ASSETS = ROOT / 'docs/assets/comparisons/dlss'
FONT = "fontfile='C\\:/Windows/Fonts/segoeui.ttf'"
TONE_MAP = ('zscale=transfer=linear:npl=100,format=gbrpf32le,zscale=primaries=bt709,'
            'tonemap=tonemap=mobius:desat=2:peak=10,'
            'zscale=transfer=bt709:matrix=gbr:range=full,format=gbrp,format=rgb24')


def run(command):
    result = subprocess.run([str(value) for value in command], capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f'{command[0]} failed: {result.stderr.decode(errors="replace")[-4000:]}')
    return result.stdout


def ff(arguments):
    return run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *arguments])


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rgb(path):
    import av
    with av.open(str(path)) as source:
        return next(source.decode(video=0)).to_ndarray(format='rgb24')


def render_worker():
    sys.path.insert(0, str(ROOT / 'worker'))
    from vsr_enhancements import Enhancements
    from vsr_everywhere import main
    trace_path, sys.argv = Path(sys.argv[2]), [str(ROOT / 'worker/vsr_everywhere.py'), *sys.argv[3:]]
    trace = {}
    original = Enhancements.frame

    def record(self, pair, *args, **kwargs):
        image = original(self, pair, *args, **kwargs)
        trace[pair[3]] = {'anchors': [pair[4], pair[5]], 'alpha': str(pair[2])}
        return image

    Enhancements.frame = record
    try:
        return main()
    finally:
        trace_path.write_text(json.dumps(trace, indent=2), encoding='utf-8')


def compose(job):
    import numpy as np
    item, scene, folder, baseline = job
    slug = item['slug']
    frame_index = {1: 16, 2: 40}[scene]
    right = folder / f'scene-{scene}-dlss-4k-sdr.png'
    ff(['-i', folder / 'encoded/dlss.mp4', '-vf', f'select=eq(n\\,{frame_index}),' + TONE_MAP,
        '-frames:v', '1', '-pix_fmt', 'rgb24', right])
    left = baseline / slug / f'scene-{scene}-original-4k.png'
    left_pixels, right_pixels = rgb(left), rgb(right)
    assert left_pixels.shape == right_pixels.shape == (2160, 3840, 3)
    assert right_pixels.max() - right_pixels.min() >= 8, 'Blank enhancement output'
    assert not np.array_equal(left_pixels, right_pixels), 'Unchanged enhancement output'
    x, y = (768, 400) if slug == 'venture-bros' and scene == 1 else (1280, 600)
    paths = {}
    for kind in ('overview', 'detail'):
        crop = kind == 'detail'
        width, height = (1280, 960) if crop else (1600, 900)
        body = f'crop=1280:960:{x}:{y}' if crop else 'scale=1600:900:flags=lanczos'
        labels = ('Original - Lanczos upscale', 'Veyra NR + VSR + HDR - SDR preview')
        streams = [f'[{index}:v]{body},pad={width}:{height+56}:0:56:color=0x121826,'
                   f'drawtext={FONT}:text=\'{label}\':x=18:y=13:fontsize=26:fontcolor=white[p{index}]'
                   for index, label in enumerate(labels)]
        panel = folder / f'scene-{scene}-{kind}.png'
        ff(['-i', left, '-i', right, '-filter_complex', ';'.join(streams) + ';[p0][p1]hstack=inputs=2[out]',
            '-map', '[out]', '-frames:v', '1', panel])
        output = ASSETS / f'{slug}-scene-{scene}-{kind}.webp'
        arguments = ['-i', panel]
        if not crop:
            arguments += ['-vf', 'scale=1600:478:flags=lanczos']
        ff([*arguments, '-frames:v', '1', '-pix_fmt', 'bgra', '-c:v', 'libwebp', '-lossless', '1',
            '-q:v', '100', '-compression_level', '6', output])
        pixels = rgb(output)
        if crop:
            assert np.array_equal(pixels, rgb(panel)), 'Detail WebP is not lossless'
            assert np.array_equal(pixels[56:, :1280], left_pixels[y:y+960, x:x+1280])
            assert np.array_equal(pixels[56:, 1280:], right_pixels[y:y+960, x:x+1280])
            old_panel = rgb(ASSETS.parent / output.name)
            assert np.array_equal(pixels[56:, :1280], old_panel[56:, :1280]), 'Original comparison pixels changed'
            assert not np.array_equal(pixels[56:, 1280:], old_panel[56:, 1280:]), 'Old VSR image reused'
        else:
            assert pixels.shape == (478, 1600, 3)
        paths[kind] = {'file': output.name, 'sha256': digest(output)}
    trace = json.loads((folder / 'frame-trace.json').read_text())
    record = {'title': item['title'], 'slug': slug, 'scene': scene, 'source_time_s': item['times'][scene-1],
              'source_frame_index': frame_index, 'crop_xywh': [x, y, 1280, 960],
              'content_profile': 'animation' if slug in ('futurama', 'venture-bros') else 'live_action',
              'source_fps': item['fps'], 'source_clip_sha256': digest(item['combined']),
              'native_rgb_sha256': hashlib.sha256(rgb(baseline / slug / f'scene-{scene}-original-native.png').tobytes()).hexdigest(),
              'source_10bit_converted_to_8bit': item['converted_10bit'],
              'interpolation': {key: trace[str(frame_index)][key] for key in ('anchors', 'alpha')}, 'assets': paths}
    print(f'Verified {slug} scene {scene}: matched frame {frame_index}, exact baseline/crops, new DLSS output.', flush=True)
    return record


def main():
    import av
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparisons', type=Path, default=ROOT.parent / 'comparison shots')
    parser.add_argument('--work', type=Path, default=ROOT / 'installer/build/dlss-comparisons')
    args = parser.parse_args()
    saved = json.loads((args.comparisons / 'comparison-info.json').read_text(encoding='utf-8'))
    assert saved['profile'] == {'VsrQuality': 'ULTRA', 'VsrStrength': '1', 'ProcessingWidth': '5120',
        'ProcessingHeight': '2880', 'StreamWidth': '3840', 'StreamHeight': '2160', 'VideoMbps': '60',
        'NvencPreset': 'p7', 'NvencTune': 'hq', 'NvencLookahead': '12', 'NvencCq': '1',
        'NvencSpatialAq': 'true', 'NvencTemporalAq': 'false'}, 'Original capture profile changed'
    assert len(saved['items']) == 4 and {item['slug'] for item in saved['items']} == {'doctor-who', 'futurama', 'venture-bros', 'xena'}
    ASSETS.mkdir(parents=True, exist_ok=True)
    jobs = []
    for item in saved['items']:
        item = {**item, 'combined': str((args.comparisons / '.work' / item['slug'] / 'samples.mkv').resolve())}
        folder = args.work / item['slug']
        (folder / 'encoded').mkdir(parents=True, exist_ok=True)
        with av.open(item['combined']) as source:
            frames = list(source.decode(video=0))
            assert len(frames) == 48
            for scene, index in ((1, 16), (2, 40)):
                assert np.array_equal(frames[index].to_ndarray(format='rgb24'),
                    rgb(args.comparisons / item['slug'] / f'scene-{scene}-original-native.png')), 'Source frame identity changed'
        fps = Fraction(item['fps'])
        profile = 'animation' if item['slug'] in ('futurama', 'venture-bros') else 'live_action'
        command = [sys.executable, __file__, '--worker', folder / 'frame-trace.json', item['combined'],
                   '--output', folder / 'encoded/dlss.mp4', '--ffmpeg', FFMPEG, '--ffprobe', FFPROBE,
                   '--quality', 'ULTRA', '--strength', '1', '--processing-width', '5120', '--processing-height', '2880',
                   '--stream-width', '3840', '--stream-height', '2160', '--video-mbps', '60',
                   '--nvenc-preset', 'p7', '--nvenc-tune', 'hq', '--nvenc-lookahead', '12', '--nvenc-cq', '1',
                   '--nvenc-spatial-aq', '--neural-rendering', '--content-profile', profile, '--auto-hdr',
                   '--frame-interpolation', '--seconds', str(float(Fraction(48) / fps) + 0.000001)]
        print(f'Rendering {item["title"]}: original 48-frame sample, {profile}, all DLSS-branch features.', flush=True)
        (folder / 'worker.log').write_bytes(run(command))
        video = json.loads(run([FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', folder / 'encoded/dlss.mp4']))['streams'][0]
        assert int(video['nb_frames']) == 48 and Fraction(video['r_frame_rate']) == fps
        assert (video['width'], video['height'], video['color_transfer'], video['color_primaries'], video['color_space']) == (3840, 2160, 'smpte2084', 'bt2020', 'bt2020nc')
        with av.open(str(folder / 'encoded/dlss.mp4')) as encoded:
            timestamps = [frame.pts * frame.time_base for frame in encoded.decode(video=0)]
            assert len(timestamps) == 48
            assert all(abs(timestamp - index / fps) <= Fraction(1, 24000) for index, timestamp in enumerate(timestamps)), 'Output presentation time changed'
        jobs.extend((item, scene, folder, args.comparisons) for scene in (1, 2))
    print('GPU renders complete; exporting and verifying comparison assets.', flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scenes = list(pool.map(compose, jobs))
    provenance = {'branch': 'testing-dlss', 'profile': {**saved['profile'], 'EnableNeuralRendering': True,
                  'EnableAutoHdr': True, 'EnableFrameInterpolation': True, 'TrueHdrPeakNits': 1000},
                  'hdr_sdr_preview_filter': TONE_MAP,
                  'worker_sha256': {name: digest(ROOT / 'worker' / name) for name in
                      ('vsr_everywhere.py', 'vsr_enhancements.py', 'vsr_temporal.py')}, 'scenes': scenes}
    (ASSETS / 'capture.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    assert len(scenes) == 8 and len(list(ASSETS.glob('*.webp'))) == 16
    print('PASS: 8 original frame selections, 16 new DLSS WebPs, unchanged baseline pixels, HDR signaling, source FPS, and lossless crops.', flush=True)


if __name__ == '__main__':
    sys.exit(render_worker() if len(sys.argv) > 1 and sys.argv[1] == '--worker' else main())
