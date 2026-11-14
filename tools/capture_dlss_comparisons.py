"""Capture Original (Lanczos), VSR, and NR+VSR from the original comparison frames."""
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
VARIANTS = ('vsr', 'nr_vsr')
LABELS = ('Original (Lanczos)', 'VSR', 'NR+VSR')
HEADER = 72


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
    from vsr_everywhere import parse_args, run as run_worker
    sys.argv = [str(ROOT / 'worker/vsr_everywhere.py'), *sys.argv[2:]]
    args = parse_args()
    # Capture the shared server renderer directly, without its short-clip startup preview.
    args.serve = True
    run_worker(args)
    return 0


def compose(job):
    import numpy as np
    item, scene, folder, baseline = job
    slug = item['slug']
    frame_index = {1: 16, 2: 40}[scene]
    left = baseline / slug / f'scene-{scene}-original-4k.png'
    pictures = [left]
    for variant in VARIANTS:
        picture = folder / f'scene-{scene}-{variant}-4k-sdr.png'
        ff(['-i', folder / f'encoded/{variant}.mp4', '-vf', f'select=eq(n\\,{frame_index})',
            '-frames:v', '1', '-pix_fmt', 'rgb24', picture])
        pictures.append(picture)
    columns = [rgb(path) for path in pictures]
    assert all(pixels.shape == (2160, 3840, 3) for pixels in columns)
    assert all(int(pixels.max()) - int(pixels.min()) >= 8 for pixels in columns), 'Blank comparison output'
    assert all(not np.array_equal(columns[a], columns[b]) for a, b in ((0, 1), (0, 2), (1, 2))), 'Duplicate comparison columns'
    x, y = (768, 400) if slug == 'venture-bros' and scene == 1 else (1280, 600)
    paths = {}
    for kind in ('overview', 'detail'):
        crop = kind == 'detail'
        width, height = (1280, 960) if crop else (1600, 900)
        body = f'crop=1280:960:{x}:{y}' if crop else 'scale=1600:900:flags=lanczos'
        streams = [f'[{index}:v]{body},pad={width}:{height+HEADER}:0:{HEADER}:color=0x121826,'
                   f'drawtext={FONT}:text=\'{label}\':x=18:y=14:fontsize=40:fontcolor=white[p{index}]'
                   for index, label in enumerate(LABELS)]
        panel = folder / f'scene-{scene}-{kind}.png'
        inputs = [value for path in pictures for value in ('-i', path)]
        ff([*inputs, '-filter_complex', ';'.join(streams) + ';[p0][p1][p2]hstack=inputs=3[out]',
            '-map', '[out]', '-frames:v', '1', panel])
        output = ASSETS / f'{slug}-scene-{scene}-{kind}.webp'
        arguments = ['-i', panel]
        if not crop:
            arguments += ['-vf', 'scale=1600:324:flags=lanczos']
        ff([*arguments, '-frames:v', '1', '-pix_fmt', 'bgra', '-c:v', 'libwebp', '-lossless', '1',
            '-q:v', '100', '-compression_level', '6', output])
        pixels = rgb(output)
        if crop:
            assert np.array_equal(pixels, rgb(panel)), 'Detail WebP is not lossless'
            for index, source in enumerate(columns):
                assert np.array_equal(pixels[HEADER:, index*1280:(index+1)*1280], source[y:y+960, x:x+1280])
            old_panel = rgb(ASSETS.parent / output.name)
            assert np.array_equal(pixels[HEADER:, :1280], old_panel[56:, :1280]), 'Original comparison pixels changed'
        else:
            assert pixels.shape == (324, 1600, 3)
        paths[kind] = {'file': output.name, 'sha256': digest(output)}
    record = {'title': item['title'], 'slug': slug, 'scene': scene, 'source_time_s': item['times'][scene-1],
              'source_frame_index': frame_index, 'crop_xywh': [x, y, 1280, 960],
              'content_profile': 'animation' if slug in ('futurama', 'venture-bros') else 'live_action',
              'source_fps': item['fps'], 'source_clip_sha256': digest(item['combined']),
              'native_rgb_sha256': hashlib.sha256(rgb(baseline / slug / f'scene-{scene}-original-native.png').tobytes()).hexdigest(),
              'source_10bit_converted_to_8bit': item['converted_10bit'],
              'outputs': {variant: {'encoded_sha256': digest(folder / f'encoded/{variant}.mp4'),
                  'frame_rgb_sha256': hashlib.sha256(columns[index+1].tobytes()).hexdigest()}
                  for index, variant in enumerate(VARIANTS)}, 'assets': paths}
    print(f'Verified {slug} scene {scene}: frame {frame_index}, exact baseline/crops, VSR and NR+VSR.', flush=True)
    return record


def main():
    import av
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparisons', type=Path, default=ROOT.parent / 'comparison shots')
    parser.add_argument('--work', type=Path, default=ROOT / 'installer/build/dlss-three-way')
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
        for variant in VARIANTS:
            command = [sys.executable, __file__, '--worker', item['combined'],
                   '--output', folder / f'encoded/{variant}.mp4', '--ffmpeg', FFMPEG, '--ffprobe', FFPROBE,
                   '--quality', 'ULTRA', '--strength', '1', '--processing-width', '5120', '--processing-height', '2880',
                   '--stream-width', '3840', '--stream-height', '2160', '--video-mbps', '60',
                   '--nvenc-preset', 'p7', '--nvenc-tune', 'hq', '--nvenc-lookahead', '12', '--nvenc-cq', '1',
                   '--nvenc-spatial-aq', '--content-profile', profile,
                   '--seconds', str(float(Fraction(48) / fps) + 0.000001)]
            if variant == 'nr_vsr':
                command.append('--neural-rendering')
            print(f'Rendering {item["title"]}: {variant}, original 48 frames, HDR/interpolation off.', flush=True)
            log = run(command)
            (folder / f'{variant}-worker.log').write_bytes(log)
            events = [json.loads(line) for line in log.decode().splitlines() if line.startswith('{')]
            started = next(event for event in events if event.get('event') == 'start')
            assert started['neural_rendering'] == (variant == 'nr_vsr')
            assert not started['hdr'] and not started['frame_interpolation']
            video = json.loads(run([FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', folder / f'encoded/{variant}.mp4']))['streams'][0]
            assert int(video['nb_frames']) == 48 and Fraction(video['r_frame_rate']) == fps
            assert (video['width'], video['height'], video['color_transfer'], video['color_primaries'], video['color_space']) == (3840, 2160, 'bt709', 'bt709', 'bt709')
            with av.open(str(folder / f'encoded/{variant}.mp4')) as encoded:
                timestamps = [frame.pts * frame.time_base for frame in encoded.decode(video=0)]
                assert len(timestamps) == 48
                assert all(abs(timestamp - index / fps) <= Fraction(1, 24000) for index, timestamp in enumerate(timestamps)), 'Output presentation time changed'
        jobs.extend((item, scene, folder, args.comparisons) for scene in (1, 2))
    print('GPU renders complete; exporting and verifying comparison assets.', flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scenes = list(pool.map(compose, jobs))
    provenance = {'branch': 'testing-dlss', 'columns': list(LABELS),
                  'profile': {**saved['profile'], 'EnableAutoHdr': False, 'EnableFrameInterpolation': False,
                              'DeliveryColor': 'BT.709 SDR', 'NrOutputTransfer': 'sRGB to BT.709 before VSR'},
                  'variants': {'vsr': {'EnableNeuralRendering': False}, 'nr_vsr': {'EnableNeuralRendering': True}},
                  'processing_path': 'Shared server renderer; startup preview omitted for offline capture',
                  'worker_sha256': {name: digest(ROOT / 'worker' / name) for name in
                      ('vsr_everywhere.py', 'vsr_enhancements.py', 'vsr_temporal.py', 'vsr_gpu.py', 'vsr_colour.py')}, 'scenes': scenes}
    (ASSETS / 'capture.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8', newline='\n')
    assert len(scenes) == 8 and len(list(ASSETS.glob('*.webp'))) == 16
    print('PASS: 8 matched scenes, 16 three-way WebPs, unchanged originals, distinct VSR/NR+VSR, source FPS, SDR, and lossless crops.', flush=True)


if __name__ == '__main__':
    sys.exit(render_worker() if len(sys.argv) > 1 and sys.argv[1] == '--worker' else main())
