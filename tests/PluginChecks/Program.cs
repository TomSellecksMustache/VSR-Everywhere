using System.Reflection;
using System.Text.Json;
using Jellyfin.Plugin.VsrEverywhere;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.Extensions.Logging.Abstractions;

static void Check(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}

var assembly = typeof(BridgeSession).Assembly;
Check(assembly.GetName().Version == new Version(1, 3, 0, 0), "Expected version 1.3.0.0");
Check(assembly.GetName().Name == "Jellyfin.Plugin.VsrEverywhere", "Configuration-compatible assembly identity changed");
Check(assembly.GetCustomAttribute<AssemblyProductAttribute>()?.Product == "VSR Everywhere", "Wrong product name");
Check(Plugin.PluginId == new Guid("7ca129d0-7454-41c7-aad6-55250b1a7b5b"), "Plugin ID changed");
EnhancementChecks.Run();
DeviceCompatibilityChecks.Run();
using (var resource = assembly.GetManifestResourceStream("Jellyfin.Plugin.VsrEverywhere.Configuration.configPage.html")!)
using (var reader = new StreamReader(resource))
{
    var page = await reader.ReadToEndAsync();
    Check(page.Contains("<title>VSR Everywhere</title>") && page.Contains("id=\"ProcessingResolution\""), "Updated page missing from compiled plugin");
}

// This worker records arguments inside this check project's output directory.
if (args.Length == 4)
{
    await PlaybackChecks.Run(args[0], args[1], args[2], args[3]);
    return;
}
var worker = Path.Combine(AppContext.BaseDirectory, "worker", "vsr_everywhere.py");
Directory.CreateDirectory(Path.GetDirectoryName(worker)!);
await File.WriteAllTextAsync(worker, """
import json, os, sys
from pathlib import Path
if '--memory-output' in sys.argv:
    Path(__file__).with_name('memory-arguments.json').write_text(json.dumps({'args': sys.argv[1:], 'key': os.environ.get('VSR_INGEST_KEY')}))
    print(json.dumps({'event': 'ready', 'fps_num': 24, 'fps_den': 1, 'gop_frames': 48, 'duration_s': 12}), flush=True)
    for line in sys.stdin:
        with Path(__file__).with_name('commands.jsonl').open('a') as captured:
            captured.write(line)
        if json.loads(line).get('cmd') == 'stop':
            break
else:
    output = Path(sys.argv[sys.argv.index('--output') + 1])
    output.with_name('arguments.json').write_text(json.dumps(sys.argv[1:]))
""");
var cases = new[] {
    (5120, 2880, 3840, 2160), (7680, 4320, 7680, 4320),
    (1920, 1080, 7680, 4320), (7680, 4320, 1920, 1080)
};
foreach (var (pw, ph, sw, sh) in cases)
{
    var cfg = new PluginConfiguration {
        CacheMode = "disk",
        PythonPath = args.FirstOrDefault() ?? "python", ProcessingWidth = pw, ProcessingHeight = ph,
        StreamWidth = sw, StreamHeight = sh, SegmentType = "mpegts",
        EnabledDeviceIds = ["retained-device"], EnableVsr = pw != 1920
    };
    var root = Path.Combine(AppContext.BaseDirectory, "checks", Guid.NewGuid().ToString("N"));
    using var session = new BridgeSession("test-token", "source.mp4", root, cfg, NullLogger.Instance);
    Check(await session.EnsureStarted(CancellationToken.None), "Worker launch failed");
    var captured = Path.Combine(root, "arguments.json");
    var timeout = DateTime.UtcNow.AddSeconds(10);
    while ((!File.Exists(captured) || new FileInfo(captured).Length == 0) && DateTime.UtcNow < timeout)
        await Task.Delay(50);
    var command = JsonSerializer.Deserialize<string[]>(await File.ReadAllTextAsync(captured))!;
    string Value(string key) => command[Array.IndexOf(command, key) + 1];
    Check(Value("--processing-width") == pw.ToString() && Value("--processing-height") == ph.ToString(), "Processing size changed during launch");
    Check(Value("--stream-width") == sw.ToString() && Value("--stream-height") == sh.ToString(), "Stream size was clamped or changed during launch");
    Check(Value("--segment-type") == "mpegts", "Audio/segment mode changed");
    Check(command.Contains("--frame-interpolation") == cfg.EnableFrameInterpolation, "Interpolation setting was not passed to the worker");
    Check(command.Contains("--no-vsr") == !cfg.EnableVsr, "VSR setting was not passed to the worker");
    Check(cfg.EnabledDeviceIds.SequenceEqual(new[] { "retained-device" }), "Device approvals changed");
}
Console.WriteLine("PASS: compiled plugin identity/version, embedded settings, and actual worker arguments (5K/4K, 8K/8K, 1080p/8K, 8K/1080p).");
await CropChecks.Run(args.FirstOrDefault() ?? "python");
await MemoryChecks.Run(args.FirstOrDefault() ?? "python");
if (args.Length > 1) await TransportChecks.Run(args[0], Path.GetFullPath(args[1]));
