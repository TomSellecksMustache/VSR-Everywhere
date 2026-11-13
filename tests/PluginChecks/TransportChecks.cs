using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using Jellyfin.Plugin.VsrEverywhere;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

internal static class TransportChecks
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    internal static async Task Run(string python, string ffmpeg)
    {
        using var sessions = new BridgeSessions(NullLogger<BridgeSessions>.Instance);
        var middleware = new BridgeMiddleware(_ => throw new Exception("Unexpected transport route"), NullLogger<BridgeMiddleware>.Instance);
        var requests = new ConcurrentBag<(string Method, string Path, int Status)>();
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options => options.Listen(IPAddress.Loopback, 0));
        await using var app = builder.Build();
        app.UsePathBase("/jellyfin");
        app.Run(async context =>
        {
            await middleware.InvokeAsync(context, sessions);
            requests.Add((context.Request.Method, context.Request.Path.Value!, context.Response.StatusCode));
        });
        await app.StartAsync();
        var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        foreach (var kind in new[] { "mpegts", "fmp4" })
        {
            var directory = Path.Combine(AppContext.BaseDirectory, "checks", "http-" + Guid.NewGuid().ToString("N"));
            var cacheDirectory = Path.Combine(directory, "media-cache");
            Directory.CreateDirectory(directory);
            var configuration = new PluginConfiguration
            {
                CacheMode = "memory", MemoryBudgetMb = 512, PythonPath = python,
                CacheDirectory = cacheDirectory, SegmentType = kind
            };
            var token = sessions.Register("synthetic-transport.mp4", configuration)!;
            var session = sessions.Find(token)!;
            Check(session.IsMemory && session.Root.Length == 0, "Wire check must use memory without a session directory");
            var ingest = address + "/jellyfin/VsrEverywhere/ingest/" + token + "/0/";
            var playback = address + "/jellyfin/VsrEverywhere/hls/" + token + "/";
            Check(await session.EnsureStarted(CancellationToken.None, ingest + "index.m3u8"), "Fake transport worker launch");
            using (var rejected = await client.PutAsync(ingest + "init.mp4", new ByteArrayContent([1])))
                Check(rejected.StatusCode == HttpStatusCode.Forbidden, "Real HTTP ingest rejects a missing key");

            var start = new ProcessStartInfo(ffmpeg)
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true,
                RedirectStandardOutput = true, WorkingDirectory = directory
            };
            start.Environment["TEMP"] = directory;
            start.Environment["TMP"] = directory;
            var suffix = kind == "fmp4" ? "m4s" : "ts";
            foreach (var argument in new[]
            {
                "-hide_banner", "-nostdin", "-loglevel", "warning",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=12",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-sc_threshold", "0",
                "-c:a", "aac", "-shortest", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
                "-hls_flags", "independent_segments+delete_segments", "-hls_segment_type", kind,
                "-method", "PUT", "-http_persistent", "1", "-headers", "X-Vsr-Key: " + session.IngestKey + "\r\n",
                "-hls_segment_filename", ingest + "seg_%06d." + suffix
            }) start.ArgumentList.Add(argument);
            if (kind == "fmp4")
            {
                start.ArgumentList.Add("-hls_fmp4_init_filename");
                start.ArgumentList.Add("init.mp4");
            }
            start.ArgumentList.Add(ingest + "index.m3u8");
            using (var process = Process.Start(start)!)
            using (var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(45)))
            {
                var stderr = process.StandardError.ReadToEndAsync();
                var stdout = process.StandardOutput.ReadToEndAsync();
                try { await process.WaitForExitAsync(timeout.Token); }
                finally { if (!process.HasExited) { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); } }
                Check(process.ExitCode == 0, kind + " HTTP muxing failed: " + await stderr);
                await stdout;
            }
            using var response = await client.GetAsync(playback + "index.m3u8");
            Check(response.StatusCode == HttpStatusCode.OK && response.Headers.CacheControl?.NoStore == true,
                kind + " real playlist GET headers");
            Check(response.Content.Headers.ContentType?.MediaType == "application/vnd.apple.mpegurl", "Playlist MIME type");
            var playlist = await response.Content.ReadAsStringAsync();
            var segments = playlist.Split('\n').Select(line => line.Trim())
                .Where(line => line.StartsWith("seg_", StringComparison.Ordinal)).ToArray();
            Check(segments.Length == 6 && playlist.Contains("#EXT-X-ENDLIST", StringComparison.Ordinal)
                && playlist.Contains("#EXT-X-PLAYLIST-TYPE:VOD", StringComparison.Ordinal), kind + " complete on-demand playlist");
            var names = kind == "fmp4" ? segments.Append("init.mp4") : segments.AsEnumerable();
            foreach (var name in names)
            {
                using var body = await client.GetAsync(playback + name);
                var bytes = await body.Content.ReadAsByteArrayAsync();
                Check(body.StatusCode == HttpStatusCode.OK && bytes.Length > 0 && body.Headers.CacheControl?.NoStore == true,
                    kind + " advertised object must be available over HTTP: " + name);
                using var headRequest = new HttpRequestMessage(HttpMethod.Head, playback + name);
                using var head = await client.SendAsync(headRequest);
                Check(head.StatusCode == HttpStatusCode.OK && head.Content.Headers.ContentLength == bytes.Length
                    && (await head.Content.ReadAsByteArrayAsync()).Length == 0, kind + " HEAD length/body contract: " + name);
            }
            using (var deletion = new HttpRequestMessage(HttpMethod.Delete, ingest + "seg_999999." + suffix))
            {
                deletion.Headers.Add("X-Vsr-Key", session.IngestKey);
                using var deleted = await client.SendAsync(deletion);
                Check(deleted.StatusCode == HttpStatusCode.NoContent, "Authenticated HTTP DELETE");
            }
            var uploads = requests.Where(request => request.Path.Contains(token, StringComparison.Ordinal) && request.Method == "PUT").ToArray();
            var deletions = requests.Count(request => request.Path.Contains(token, StringComparison.Ordinal)
                && request.Method == "DELETE" && request.Status == 204);
            Check(uploads.Count(request => request.Status == 204) >= 12 && uploads.Count(request => request.Status == 403) == 1
                && deletions > 0, kind + " authenticated PUT/DELETE must reach the actual middleware");
            Check(!Directory.Exists(cacheDirectory) && !Directory.EnumerateFileSystemEntries(directory).Any(),
                kind + " memory transport must leave its isolated working/temp/cache directory empty");
            Console.WriteLine($"PASS: {kind} real Kestrel HTTP ingest/playback, {uploads.Count(request => request.Status == 204)} PUTs, {deletions} DELETEs, GET/HEAD and empty working/temp/cache directories.");
        }
        await app.StopAsync();
    }
}
