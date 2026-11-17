using System.Diagnostics;
using System.Net;
using System.Reflection;
using System.Text.Json;
using Jellyfin.Plugin.VsrEverywhere;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

internal static class PlaybackChecks
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    internal static async Task Run(string python, string ffmpeg, string source, string workerDirectory)
    {
        var deployedWorker = Path.Combine(AppContext.BaseDirectory, "worker");
        Directory.CreateDirectory(deployedWorker);
        foreach (var file in Directory.EnumerateFiles(workerDirectory, "*.py"))
            File.Copy(file, Path.Combine(deployedWorker, Path.GetFileName(file)), true);
        using var logging = LoggerFactory.Create(builder => builder.AddSimpleConsole().SetMinimumLevel(LogLevel.Debug));
        using var sessions = new BridgeSessions(logging.CreateLogger<BridgeSessions>());
        var middleware = new BridgeMiddleware(_ => throw new Exception("Unexpected route"), NullLogger<BridgeMiddleware>.Instance);
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options => options.Listen(IPAddress.Loopback, 0));
        await using var app = builder.Build();
        app.Run(context => middleware.InvokeAsync(context, sessions));
        await app.StartAsync();
        var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(40) };
        var advanced = Environment.GetEnvironmentVariable("VSR_TEST_ADVANCED") == "1";
        var cropped = Environment.GetEnvironmentVariable("VSR_TEST_CROP") == "1";
        var production = Environment.GetEnvironmentVariable("VSR_TEST_PRODUCTION") == "1";
        var sourceProbe = new ProcessStartInfo(Path.Combine(Path.GetDirectoryName(ffmpeg)!, "ffprobe.exe"))
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true };
        foreach (var argument in new[] { "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", source }) sourceProbe.ArgumentList.Add(argument);
        using var sourceProcess = Process.Start(sourceProbe)!;
        var sourceRate = (await sourceProcess.StandardOutput.ReadToEndAsync()).Trim();
        await sourceProcess.WaitForExitAsync();
        Check(sourceProcess.ExitCode == 0, "Source rate probe failed");
        var rateParts = sourceRate.Split('/');
        var sourceFps = double.Parse(rateParts[0], System.Globalization.CultureInfo.InvariantCulture) / double.Parse(rateParts[1], System.Globalization.CultureInfo.InvariantCulture);
        foreach (var kind in production ? new[] { "mpegts" } : new[] { "fmp4", "mpegts" })
        {
            var cache = Path.Combine(AppContext.BaseDirectory, "checks", "unused-cache-" + Guid.NewGuid().ToString("N"));
            var cfg = new PluginConfiguration
            {
                PythonPath = python, FfmpegPath = ffmpeg,
                FfprobePath = Path.Combine(Path.GetDirectoryName(ffmpeg)!, "ffprobe.exe"),
                CacheMode = "memory", MemoryBudgetMb = 512, CacheDirectory = cache,
                SegmentType = kind, SeekLookaheadSeconds = 2, SeekCatchupSeconds = 0,
                EnableAutoHdr = advanced, EnableFrameInterpolation = advanced
            };
            if (kind == "mpegts") { cfg.ProcessingWidth = 5120; cfg.ProcessingHeight = 2880; }
            if (advanced) { cfg.ProcessingWidth = 1280; cfg.ProcessingHeight = 720; cfg.StreamWidth = 640; cfg.StreamHeight = 360; }
            if (cropped) { cfg.ProcessingWidth = 1280; cfg.ProcessingHeight = 720; cfg.StreamWidth = 1280; cfg.StreamHeight = 720; }
            if (production)
            {
                cfg.ProcessingWidth = 5120; cfg.ProcessingHeight = 2880; cfg.StreamWidth = 3840; cfg.StreamHeight = 2160;
                cfg.VsrQuality = "ULTRA"; cfg.VsrStrength = 1; cfg.VideoMbps = 40;
                cfg.NvencPreset = "p7"; cfg.NvencTune = "hq"; cfg.NvencLookahead = 12; cfg.NvencCq = 1; cfg.NvencTemporalAq = true;
            }
            var crop = cropped ? await StreamingCrop.Detect(source,
                new MediaBrowser.Model.Entities.MediaStream { Width = 640, Height = 480, AspectRatio = "4:3" },
                cfg, logging.CreateLogger("Crop"), CancellationToken.None) : null;
            if (cropped) Check(crop == new StreamingCrop(0, 60, 640, 360, 1280, 720), "Playback fixture crop detection");
            var token = sessions.Register(source, cfg, advanced, null, crop)!;
            var session = sessions.Find(token)!;
            var url = address + "/VsrEverywhere/hls/" + token + "/";
            var output = Path.Combine(AppContext.BaseDirectory, "checks", "playback-" + kind + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(output);
            var clock = Stopwatch.StartNew();
            var playlist = await client.GetStringAsync(url + "index.m3u8");
            Check(playlist.Contains("#EXT-X-PLAYLIST-TYPE:VOD") && playlist.Contains("#EXT-X-ENDLIST"), "Complete seekable playlist");
            var names = playlist.Split('\n').Where(line => line.StartsWith("seg_", StringComparison.Ordinal)).ToArray();
            var durationLine = playlist.Split('\n').First(line => line.StartsWith("#EXTINF:", StringComparison.Ordinal));
            var segmentSeconds = double.Parse(durationLine[8..].TrimEnd(','), System.Globalization.CultureInfo.InvariantCulture);
            Check(names.Length >= 6, "Playback fixture needs at least twelve seconds");
            Console.WriteLine($"READY {kind}: {clock.Elapsed.TotalSeconds:F3}s");
            if (kind == "fmp4") await File.WriteAllBytesAsync(Path.Combine(output, "init.mp4"), await client.GetByteArrayAsync(url + "init.mp4"));
            var worker = (Process)typeof(BridgeSession).GetField("_worker", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(session)!;
            var pid = worker.Id;
            var middle = advanced || cropped ? names.Length - 1 : Math.Min(1200, names.Length / 2);
            if (advanced || cropped)
            {
                clock.Restart();
                double firstResponse = 0;
                for (var index = 0; index < Math.Min(6, names.Length); index++)
                {
                    var bytes = await client.GetByteArrayAsync(url + names[index]);
                    await File.WriteAllBytesAsync(Path.Combine(output, names[index]), bytes);
                    Console.WriteLine($"STARTUP {kind} {index}: {clock.Elapsed.TotalSeconds:F3}s");
                    if (index == 0)
                    {
                        firstResponse = clock.Elapsed.TotalSeconds;
                        Check(names.Take(3).All(name => { using var body = session.OpenMemory(name); return body is not null; }),
                            "Playback began without a three-segment reserve");
                    }
                    else Check(clock.Elapsed.TotalSeconds - firstResponse < index * segmentSeconds + .25,
                        "Startup handoff missed a real-time playback deadline");
                }
                Check(session.AcceptsRun(1), "Sequential startup requests must not restart the encoder run");
            }
            foreach (var index in new[] { middle, 0, middle })
            {
                clock.Restart();
                var bytes = await client.GetByteArrayAsync(url + names[index]);
                Check(bytes.Length > 0, "Requested segment is empty");
                await File.WriteAllBytesAsync(Path.Combine(output, names[index]), bytes);
                Console.WriteLine($"SEGMENT {kind} {index}: {clock.Elapsed.TotalSeconds:F3}s, {bytes.Length} bytes");
                Check(!worker.HasExited && worker.Id == pid, "Seek must retain the worker and VSR model");
            }
            using (var missing = await client.GetAsync(url + "seg_999999." + (kind == "fmp4" ? "m4s" : "ts")))
                Check(missing.StatusCode == HttpStatusCode.NotFound, "Out-of-range segment must return 404");
            var samples = advanced || cropped ? new[] { Enumerable.Range(0, Math.Min(6, names.Length)).ToArray(), new[] { middle } } : [new[] { middle }];
            foreach (var sampleIndices in samples)
            {
                var sample = "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:3\n#EXT-X-MEDIA-SEQUENCE:" + sampleIndices[0] + "\n"
                    + (kind == "fmp4" ? "#EXT-X-MAP:URI=\"init.mp4\"\n" : "")
                    + string.Concat(sampleIndices.Select(index => durationLine + "\n" + names[index] + "\n")) + "#EXT-X-ENDLIST\n";
                var samplePath = Path.Combine(output, $"sample-{sampleIndices[0]}.m3u8");
                await File.WriteAllTextAsync(samplePath, sample);
                var probe = new ProcessStartInfo(cfg.FfprobePath) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                foreach (var argument in new[] { "-v", "error", "-show_streams", "-show_frames", "-of", "json", samplePath }) probe.ArgumentList.Add(argument);
                using (var process = Process.Start(probe)!)
                {
                    var json = await process.StandardOutput.ReadToEndAsync();
                    var errors = await process.StandardError.ReadToEndAsync();
                    await process.WaitForExitAsync();
                    Check(process.ExitCode == 0, "Seek segment ffprobe failed: " + errors);
                    await File.WriteAllTextAsync(Path.Combine(output, $"ffprobe-{sampleIndices[0]}.json"), json);
                    using var data = JsonDocument.Parse(json);
                    var video = data.RootElement.GetProperty("streams").EnumerateArray().First(stream => stream.GetProperty("codec_type").GetString() == "video");
                    Check(video.GetProperty("codec_name").GetString() == "hevc" && video.GetProperty("profile").GetString() == "Main 10", "GPU seek must remain HEVC Main 10");
                    Check(video.GetProperty("color_space").GetString() == (advanced ? "bt2020nc" : "bt709"), "Seek color VUI changed");
                    if (cropped)
                    {
                        Check(video.GetProperty("width").GetInt32() == crop!.StreamWidth && video.GetProperty("height").GetInt32() == crop.StreamHeight,
                            "Startup, enhancement and seeks must retain the fitted crop dimensions");
                        Check(data.RootElement.GetProperty("streams").EnumerateArray().Any(stream => stream.GetProperty("codec_type").GetString() == "audio"
                            && stream.GetProperty("codec_name").GetString() == "aac"), "Cropped playback lost AAC audio");
                    }
                    if (advanced) Check(video.GetProperty("color_transfer").GetString() == "smpte2084", "HDR seek lost PQ signaling");
                    if (advanced || cropped)
                    {
                        Check(video.GetProperty("r_frame_rate").GetString() == sourceRate, "Output frame rate differs from source");
                        var timestamps = data.RootElement.GetProperty("frames").EnumerateArray()
                            .Where(frame => frame.GetProperty("media_type").GetString() == "video")
                            .Select(frame => double.Parse(frame.GetProperty("best_effort_timestamp_time").GetString()!, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
                        Check(timestamps.Length > 1 && timestamps.Zip(timestamps.Skip(1), (a, b) => Math.Abs(b - a - 1.0 / sourceFps) < .0001).All(value => value), "Interpolated seek pacing is uneven");
                    }
                    var pts = double.Parse(video.GetProperty("start_time").GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                    Check(Math.Abs(pts - sampleIndices[0] * segmentSeconds) < segmentSeconds / 48,
                        "Middle segment PTS must be absolute media time: " + pts);
                }
            }
            Check(!Directory.Exists(cache), "Memory playback created disk cache files");
            worker.StandardInput.Close();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            await worker.WaitForExitAsync(timeout.Token);
            Check(worker.ExitCode == 0, "Closing stdin must stop the worker cleanly");
            session.Dispose();
            Console.WriteLine("PASS: " + kind + " GPU memory playback, forward/backward seeks, buffered reuse, absolute PTS, and clean EOF exit.");
        }
        await app.StopAsync();
    }
}
