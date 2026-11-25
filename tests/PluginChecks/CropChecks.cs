using System.Diagnostics;
using System.Text.Json;
using Jellyfin.Plugin.VsrEverywhere;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using MediaBrowser.Model.Entities;
using Microsoft.Extensions.Logging.Abstractions;

internal static class CropChecks
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static string Snapshot(PluginConfiguration configuration)
        => JsonSerializer.Serialize(configuration, new JsonSerializerOptions { IgnoreReadOnlyProperties = true });

    internal static async Task Run(string python)
    {
        Check(StreamingCrop.Fit(179d / 120, 3840, 2160) == (3222, 2160), "716x480 DAR fits the 4K limit");
        Check(StreamingCrop.Fit(4d / 3, 3840, 2160) == (2880, 2160), "4:3 fits the 4K limit");
        Check(StreamingCrop.Fit(1920d / 800, 3840, 2160) == (3840, 1600), "Wide source fits the 4K limit");
        Check(StreamingCrop.Fit(4d / 3, 640, 360) == (480, 360), "4:3 fits the smallest stream limit");

        foreach (var (width, height, dar, maxWidth, maxHeight, streamWidth, streamHeight) in new[]
        {
            (716, 480, "179:120", 3840, 2160, 3222, 2160),
            (720, 480, "4:3", 3840, 2160, 2880, 2160),
            (1920, 800, "", 3840, 2160, 3840, 1600),
            (720, 480, "4:3", 640, 360, 480, 360),
            (720, 480, "4/3", 3840, 2160, 2880, 2160),
        })
        {
            var configuration = new PluginConfiguration { StreamWidth = maxWidth, StreamHeight = maxHeight };
            var saved = Snapshot(configuration);
            var video = new MediaStream { Width = width, Height = height, AspectRatio = dar };
            var crop = StreamingCrop.Uncropped(video, configuration);
            Check(crop == new StreamingCrop(0, 0, width, height, streamWidth, streamHeight),
                $"Uncropped picture and delivery geometry for {width}x{height}, DAR {dar}");
            Check(Snapshot(configuration) == saved, "Geometry calculation must not change global settings");
            Check(video.Width == width && video.Height == height && video.AspectRatio == dar,
                "Geometry calculation must not change source metadata");
        }

        var cfg = new PluginConfiguration { StreamWidth = 3840, StreamHeight = 2160 };
        var originalSettings = Snapshot(cfg);
        foreach (var dar in new string?[] { null, "", "malformed", "4:3:2", "NaN:1", "1:NaN", "0:1", "1:0", "0:0", "-4:3", "Infinity:1" })
        {
            var crop = StreamingCrop.Uncropped(new MediaStream { Width = 716, Height = 480, AspectRatio = dar }, cfg);
            Check(crop == new StreamingCrop(0, 0, 716, 480, 3222, 2160),
                $"Invalid DAR {dar ?? "null"} must use the coded aspect");
        }
        Check(Snapshot(cfg) == originalSettings, "Invalid DAR fallback must not change global settings");

        foreach (var hdr in new[] { false, true })
        {
            var video = new MediaStream
            {
                Width = 716, Height = 480, AspectRatio = "179:120", IsAnamorphic = true,
                Codec = "h264", CodecTag = "avc1", Level = 40, Profile = "High", PixelFormat = "yuv420p", BitDepth = 8,
                ColorPrimaries = "old", ColorTransfer = "old", ColorSpace = "old", ColorRange = "pc",
            };
            new StreamingCrop(0, 0, 716, 480, 3222, 2160).Describe(video, hdr);
            Check(video.Width == 3222 && video.Height == 2160 && video.AspectRatio == "3222:2160" && video.IsAnamorphic == false,
                "Described stream must advertise actual square-pixel delivery dimensions and DAR");
            Check(video.Codec == "hevc" && video.CodecTag is null && video.Level is null
                && video.Profile == (hdr ? "Main 10" : "Main") && video.PixelFormat == (hdr ? "yuv420p10le" : "yuv420p")
                && video.BitDepth == (hdr ? 10 : 8), "Described stream must match SDR8 or HDR10 encoding");
            Check(video.ColorPrimaries == (hdr ? "bt2020" : "bt709")
                && video.ColorTransfer == (hdr ? "smpte2084" : "bt709")
                && video.ColorSpace == (hdr ? "bt2020nc" : "bt709") && video.ColorRange == "tv",
                "Described stream must advertise matching SDR BT.709 or HDR BT.2020 colour fields");
        }

        var worker = Path.Combine(AppContext.BaseDirectory, "worker", "vsr_everywhere.py");
        Check(File.Exists(worker), "Crop launch checks require Program's fake worker");
        var fakeWorker = await File.ReadAllTextAsync(worker);
        Check(fakeWorker.Contains("memory-arguments.json", StringComparison.Ordinal)
            && fakeWorker.Contains("output.with_name('arguments.json')", StringComparison.Ordinal),
            "Crop checks must never launch the real GPU worker");
        foreach (var memory in new[] { false, true })
        {
            var configuration = new PluginConfiguration
            {
                CacheMode = memory ? "memory" : "disk", PythonPath = python,
                ProcessingWidth = 5120, ProcessingHeight = 2880, StreamWidth = 7680, StreamHeight = 4320,
                EnableAutoHdr = false, EnableFrameInterpolation = false,
                EnabledDeviceIds = ["retained-device"], SegmentType = "mpegts",
            };
            var saved = Snapshot(configuration);
            var token = "crop-" + Guid.NewGuid().ToString("N");
            var root = Path.Combine(AppContext.BaseDirectory, "checks", token);
            var store = memory ? new MemoryHlsStore() : null;
            if (store is not null) Check(store.Register(token, 8 * 1024 * 1024), "Register crop memory session");
            var captured = memory ? Path.Combine(AppContext.BaseDirectory, "worker", "memory-arguments.json")
                : Path.Combine(root, "arguments.json");
            if (memory && File.Exists(captured)) File.Delete(captured);
            using var session = new BridgeSession(token, "crop-source.mp4", memory ? "" : root, configuration,
                NullLogger.Instance, store, crop: new StreamingCrop(0, 60, 640, 360, 3840, 2160));
            Check(await session.EnsureStarted(CancellationToken.None,
                memory ? $"http://127.0.0.1:8096/VsrEverywhere/ingest/{token}/0/index.m3u8" : null),
                "Crop fake worker launch");
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while ((!File.Exists(captured) || new FileInfo(captured).Length == 0) && DateTime.UtcNow < deadline)
                await Task.Delay(25);
            Check(File.Exists(captured) && new FileInfo(captured).Length > 0, "Crop worker did not capture its arguments");
            using var document = JsonDocument.Parse(await File.ReadAllTextAsync(captured));
            var arguments = memory ? document.RootElement.GetProperty("args") : document.RootElement;
            var command = arguments.EnumerateArray().Select(value => value.GetString()!).ToArray();
            string Value(string name)
            {
                var index = Array.IndexOf(command, name);
                Check(index >= 0 && index + 1 < command.Length, "Missing crop worker argument: " + name);
                return command[index + 1];
            }
            Check(Value("--crop") == "0:60:640:360", "Source crop must reach disk and memory workers");
            Check(Value("--stream-width") == "3840" && Value("--stream-height") == "2160",
                "Per-session delivery dimensions must override saved stream limits");
            Check(Value("--processing-width") == "5120" && Value("--processing-height") == "2880",
                "Crop must not change the VSR processing limit");
            Check(command.Contains("--memory-output") == memory, "Crop checks must cover disk and memory launches");
            Check(Snapshot(configuration) == saved, "Crop worker launch must not change global settings or device approvals");
        }
        await DetectChecks(python);
        Console.WriteLine("PASS: crop geometry/DAR fallback, SDR/HDR metadata, disk/memory worker arguments and unchanged global settings.");
    }

    private static async Task DetectChecks(string python)
    {
        var script = Path.Combine(AppContext.BaseDirectory, "worker", "vsr_crop.py");
        var originalScript = File.Exists(script) ? await File.ReadAllBytesAsync(script) : null;
        var root = Path.Combine(AppContext.BaseDirectory, "checks", "crop-probe-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var cfg = new PluginConfiguration { PythonPath = python, StreamWidth = 3840, StreamHeight = 2160 };
            var saved = Snapshot(cfg);
            var video = new MediaStream { Width = 640, Height = 480, AspectRatio = "4:3" };
            var expected = new StreamingCrop(0, 60, 640, 360, 3840, 2160);
            var fallback = new StreamingCrop(0, 0, 640, 480, 2880, 2160);
            var report = new Dictionary<string, int>
            {
                ["source_width"] = 640, ["source_height"] = 480,
                ["x"] = 0, ["y"] = 60, ["width"] = 640, ["height"] = 360,
                ["stream_width"] = 3840, ["stream_height"] = 2160,
                ["sar_num"] = 1, ["sar_den"] = 1,
            };
            string ReportScript() => "print(" + JsonSerializer.Serialize(JsonSerializer.Serialize(report)) + ")\n";
            var validScript = ReportScript();
            await File.WriteAllTextAsync(script, validScript);
            var source = Path.Combine(root, "cached-source.bin");
            await File.WriteAllBytesAsync(source, [1]);
            Check(await StreamingCrop.Detect(source, video, cfg, NullLogger.Instance, CancellationToken.None) == expected,
                "Crop probe must parse its subprocess JSON plan");
            await File.WriteAllTextAsync(script, "raise SystemExit(7)\n");
            Check(await StreamingCrop.Detect(source, video, cfg, NullLogger.Instance, CancellationToken.None) == expected,
                "Unchanged source and limits must reuse the cached crop without launching the failing probe");
            using (var cancelled = new CancellationTokenSource())
            {
                cancelled.Cancel();
                var propagated = false;
                try { await StreamingCrop.Detect(source, video, cfg, NullLogger.Instance, cancelled.Token); }
                catch (OperationCanceledException) { propagated = true; }
                Check(propagated, "Caller cancellation must also apply to a cached crop");
            }
            var modified = File.GetLastWriteTimeUtc(source);
            await File.WriteAllBytesAsync(source, [1, 2]);
            File.SetLastWriteTimeUtc(source, modified);
            Check(await StreamingCrop.Detect(source, video, cfg, NullLogger.Instance, CancellationToken.None) == fallback,
                "Changed source length must bypass the cache and use full-source fallback after probe failure");

            report["x"] = 2;
            var invalidScripts = new[] { "print('not JSON')\n", ReportScript() };
            for (var index = 0; index < invalidScripts.Length; index++)
            {
                await File.WriteAllTextAsync(script, invalidScripts[index]);
                var invalidSource = Path.Combine(root, $"invalid-{index}.bin");
                await File.WriteAllBytesAsync(invalidSource, [1]);
                Check(await StreamingCrop.Detect(invalidSource, video, cfg, NullLogger.Instance, CancellationToken.None) == fallback,
                    "Malformed or out-of-bounds crop JSON must preserve the full source picture");
            }

            var cancelSource = Path.Combine(root, "cancel-source.bin");
            await File.WriteAllBytesAsync(cancelSource, [1]);
            await File.WriteAllTextAsync(script, validScript);
            Check(await StreamingCrop.Detect(cancelSource, video, cfg, NullLogger.Instance, CancellationToken.None) == expected,
                "Cancellation fixture must first cache a successful crop");
            modified = File.GetLastWriteTimeUtc(cancelSource);
            await File.WriteAllBytesAsync(cancelSource, [1, 2]);
            File.SetLastWriteTimeUtc(cancelSource, modified);
            await File.WriteAllTextAsync(script, """
import os, sys, time
from pathlib import Path
source = sys.argv[1]
Path(source + '.pid').write_text(str(os.getpid()))
time.sleep(30)
Path(source + '.completed').write_text('finished')
""");
            using var cancellation = new CancellationTokenSource();
            var pending = StreamingCrop.Detect(cancelSource, video, cfg, NullLogger.Instance, cancellation.Token);
            Process? helper = null;
            try
            {
                var marker = cancelSource + ".pid";
                var deadline = DateTime.UtcNow.AddSeconds(5);
                while ((!File.Exists(marker) || new FileInfo(marker).Length == 0) && !pending.IsCompleted && DateTime.UtcNow < deadline)
                    await Task.Delay(10);
                Check(File.Exists(marker) && new FileInfo(marker).Length > 0, "Sleeping crop probe must start before cancellation");
                helper = Process.GetProcessById(int.Parse(await File.ReadAllTextAsync(marker)));
                cancellation.CancelAfter(TimeSpan.FromMilliseconds(100));
                var cancelled = false;
                try { await pending; }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { cancelled = true; }
                Check(cancelled, "Caller cancellation must propagate instead of returning crop fallback");
                await helper.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));
                Check(helper.HasExited && !File.Exists(cancelSource + ".completed"), "Cancelled crop probe must leave no sleeping helper");
            }
            finally
            {
                cancellation.Cancel();
                if (helper is not null)
                {
                    try { if (!helper.HasExited) helper.Kill(entireProcessTree: true); }
                    catch (InvalidOperationException) { }
                    helper.Dispose();
                }
                try { await pending; }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
            }
            Check(Snapshot(cfg) == saved, "Crop subprocesses must not change global settings");
            Console.WriteLine("PASS: crop probe JSON parsing, cache reuse/invalidation, invalid-output fallback, caller cancellation and helper cleanup.");
        }
        finally
        {
            if (originalScript is null) File.Delete(script);
            else await File.WriteAllBytesAsync(script, originalScript);
            var checksRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "checks")) + Path.DirectorySeparatorChar;
            Check(Path.GetFullPath(root).StartsWith(checksRoot, StringComparison.Ordinal), "Crop fixture cleanup escaped the check directory");
            Directory.Delete(root, true);
        }
    }
}
