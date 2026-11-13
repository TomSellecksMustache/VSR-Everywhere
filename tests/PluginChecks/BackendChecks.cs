using Jellyfin.Plugin.VsrEverywhere;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.Extensions.Logging.Abstractions;

internal static class BackendChecks
{
    internal static async Task Run(string python)
    {
        var script = Path.Combine(AppContext.BaseDirectory, "worker", "vsr_backends.py");
        var count = Path.ChangeExtension(script, ".calls");
        File.Delete(count);
        await File.WriteAllTextAsync(script, """
import json, sys
from pathlib import Path
with Path(__file__).with_suffix('.calls').open('a') as output: output.write('x')
backend = sys.argv[sys.argv.index('--backend') + 1]
ffmpeg = sys.argv[sys.argv.index('--ffmpeg') + 1]
if ffmpeg == 'malformed': print('invalid probe result')
else:
    available = backend != 'intel'
    print(json.dumps({'available': available, 'backend': backend}))
    sys.exit(0 if available else 1)
""");
        var probe = new GpuBackendProbe();
        var cfg = new PluginConfiguration { PythonPath = python, Backend = "auto" };
        if (!await probe.IsAvailable(cfg, NullLogger.Instance, CancellationToken.None)) throw new Exception("Auto probe failed");
        if (!await probe.IsAvailable(cfg, NullLogger.Instance, CancellationToken.None) || (await File.ReadAllTextAsync(count)).Length != 1)
            throw new Exception("GPU discovery must be cached");
        cfg.Backend = "intel";
        if (await probe.IsAvailable(cfg, NullLogger.Instance, CancellationToken.None)) throw new Exception("Forced unavailable Intel accepted");
        if ((await File.ReadAllTextAsync(count)).Length != 2) throw new Exception("Backend override did not invalidate probe cache");
        cfg.Backend = "auto";
        cfg.FfmpegPath = "malformed";
        if (await probe.IsAvailable(cfg, NullLogger.Instance, CancellationToken.None)) throw new Exception("Malformed GPU report accepted");
        cfg.Backend = "amd";
        if (await probe.IsAvailable(cfg, NullLogger.Instance, CancellationToken.None)) throw new Exception("Unknown backend accepted");
        Console.WriteLine("PASS: GPU readiness cache, override invalidation, unsupported backend, and malformed report preserve normal playback.");
    }
}
