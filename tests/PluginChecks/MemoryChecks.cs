using System.Net;
using System.Text;
using System.Text.Json;
using System.Xml.Serialization;
using Jellyfin.Plugin.VsrEverywhere;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging.Abstractions;

internal static class MemoryChecks
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    internal static async Task Run(string python)
    {
        const long mb = 1024 * 1024;
        Check(VsrMemoryBudget.Calculate(0, 32 * 1024 * mb) == 7 * 1024 * mb, "Automatic memory cap");
        Check(VsrMemoryBudget.Calculate(0, 4 * 1024 * mb) == 4 * 1024 * mb / 5 * 3, "Automatic 60% memory budget");
        Check(VsrMemoryBudget.Calculate(512, 32 * 1024 * mb) == 512 * mb, "Explicit memory budget");
        Check(VsrMemoryBudget.Calculate(512, 1024 * mb - 1) == 0, "Low-memory disk fallback");
        var serializer = new XmlSerializer(typeof(PluginConfiguration));
        using (var oldConfiguration = new StringReader("<PluginConfiguration><CacheDirectory>retained-cache</CacheDirectory><CacheMaxGb>42</CacheMaxGb></PluginConfiguration>"))
        {
            var restored = (PluginConfiguration)serializer.Deserialize(oldConfiguration)!;
            Check(restored.CacheMode == "memory" && restored.MemoryBudgetMb == 0 && restored.CacheDirectory == "retained-cache" && restored.CacheMaxGb == 42, "Old configuration keeps disk settings and gains memory defaults");
            using var json = JsonDocument.Parse(JsonSerializer.Serialize(restored));
            Check(json.RootElement.TryGetProperty("EffectiveMemoryBudgetMb", out _), "Administrator configuration includes effective budget");
        }
        var store = new MemoryHlsStore();
        Check(store.Register("a", 512 * 1024), "Register first memory session");
        Check(store.Register("b", 512 * 1024), "Register second session under same global budget");
        var pending = store.Changed("a");
        Check(await Put(store, "a", "init.mp4", [1, 2, 3]) == 204, "Store init");
        Check(pending.IsCompleted, "Completed PUT signals readers");
        for (var i = 0; i < 12; i++) Check(await Put(store, "a", $"seg_{i:000000}.m4s", [(byte)i]) == 204, "Store segment under eviction pressure");
        var playlist = "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-INDEPENDENT-SEGMENTS\n#EXT-X-MAP:URI=\"init.mp4\"\n";
        for (var i = 0; i < 12; i++) playlist += $"#EXTINF:2.000000,\nseg_{i:000000}.m4s\n";
        Check(await Put(store, "a", "index.m3u8", Encoding.UTF8.GetBytes(playlist)) == 204, "Store playlist");
        using (var body = store.Open("a", "index.m3u8"))
        {
            Check(body is not null, "Retained playlist exists");
            using var bytes = new MemoryStream();
            await body!.CopyToAsync(bytes, CancellationToken.None);
            var rendered = Encoding.UTF8.GetString(bytes.ToArray());
            Check(!rendered.Contains("PLAYLIST-TYPE:EVENT"), "Memory playlist is sliding");
            Check(!rendered.Contains("seg_000000.m4s"), "Evicted segment omitted");
            Check(rendered.Contains("seg_000011.m4s") && rendered.Contains("#EXT-X-MAP:URI=\"init.mp4\""), "Head and init retained");
            foreach (var name in rendered.Split('\n').Where(line => line.StartsWith("seg_", StringComparison.Ordinal)))
                using (var segment = store.Open("a", name)) Check(segment is not null, "Playlist must advertise only retained segments");
        }
        Check(store.AllocatedBytes <= store.BudgetBytes, "Global allocated-buffer budget");
        Check(await Put(store, "b", "seg_000000.ts", [7]) == 204, "Second session shares global budget");
        Check(store.AllocatedBytes <= 512 * 1024, "Budgets are not added per session");
        using (var init = store.Open("a", "init.mp4")) Check(init is not null, "Pinned init survived eviction");
        store.Delete("a", "seg_000011.m4s");
        using (var newest = store.Open("a", "seg_000011.m4s")) Check(newest is not null, "Newest segments protected from DELETE");
        store.Remove("a");
        store.Remove("b");
        Check(store.AllocatedBytes == 0, "Session removal frees all buffers");

        Check(store.Register("lease", mb), "Register lease session");
        await Put(store, "lease", "seg_000000.ts", [42]);
        var held = store.Open("lease", "seg_000000.ts")!;
        await Put(store, "lease", "seg_000000.ts", [99]);
        Check(store.AllocatedBytes == 128 * 1024, "Replaced body remains accounted while leased");
        store.Remove("lease");
        Check(store.AllocatedBytes == 64 * 1024, "Retired read stays accounted after disposal");
        using (var bytes = new MemoryStream())
        {
            await held.CopyToAsync(bytes, CancellationToken.None);
            Check(bytes.ToArray().SequenceEqual(new byte[] { 42 }), "Active GET retains its original bytes");
        }
        held.Dispose();
        Check(store.AllocatedBytes == 0, "Last reader releases retired buffers");

        Check(store.Register("partial", 128 * mb), "Register upload session");
        var tail = new DelayedEofStream();
        var upload = store.PutAsync("partial", "seg_000000.ts", tail, null, CancellationToken.None);
        await tail.ReadStarted.Task;
        using (var body = store.Open("partial", "seg_000000.ts")) Check(body is null, "Partial PUT must remain invisible");
        Check(store.AllocatedBytes == 64 * 1024, "In-flight upload counts against budget");
        tail.Finish.TrySetResult();
        Check(await upload == 204, "Complete upload atomically committed");
        Check(await store.PutAsync("partial", "seg_000001.ts", Stream.Null, MemoryHlsStore.MaximumFileBytes + 1L, CancellationToken.None) == 413, "Oversized declared body rejected");
        Check(await store.PutAsync("partial", "seg_000001.ts", new GeneratedStream(MemoryHlsStore.MaximumFileBytes + 1L), null, CancellationToken.None) == 413, "Oversized chunked body rejected");
        Check(await store.PutAsync("partial", "seg_000001.ts", new MemoryStream([1, 2]), 1, CancellationToken.None) == 400, "Mismatched body length rejected");
        store.Remove("partial");
        Check(store.AllocatedBytes == 0, "Failed uploads return every reserved buffer");

        Check(store.Register("cancel", mb), "Register aborted session");
        tail = new DelayedEofStream();
        upload = store.PutAsync("cancel", "seg_000000.ts", tail, null, CancellationToken.None);
        await tail.ReadStarted.Task;
        store.Remove("cancel");
        tail.Finish.TrySetResult();
        Check(await upload == 404 && store.AllocatedBytes == 0, "Disposal rejects outstanding PUT and frees its buffers");
        foreach (var declaredLength in new long?[] { 128 * 1024, null })
        {
            Check(store.Register("exact", 128 * 1024), "Register exact-budget upload session");
            Check(await store.PutAsync("exact", "seg_000000.ts", new MemoryStream(new byte[128 * 1024]), declaredLength, CancellationToken.None) == 204,
                "Known-length and chunked uploads filling the budget must not need a spare chunk merely to detect EOF");
            store.Remove("exact");
            Check(store.AllocatedBytes == 0, "Exact-budget upload releases all buffers");
        }
        Check(store.Register("protected", 64 * 1024), "Register protected-head budget session");
        Check(await Put(store, "protected", "seg_000000.ts", [1]) == 204, "Fill budget with a protected segment");
        Check(await Put(store, "protected", "seg_000001.ts", [2]) == 507,
            "A full protected budget rejects the upload without throwing or evicting the encoder head");
        store.Remove("protected");
        Check(store.AllocatedBytes == 0, "Rejected upload retains no reserved buffers");
        await EndpointChecks(python);
        Console.WriteLine("PASS: memory budget/fallback, shared eviction, retained playlist, atomic uploads, read leases, lifecycle, size caps and ingest security.");
    }

    private static Task<int> Put(MemoryHlsStore store, string token, string name, byte[] bytes)
        => store.PutAsync(token, name, new MemoryStream(bytes), bytes.Length, CancellationToken.None);

    private static async Task EndpointChecks(string python)
    {
        using var sessions = new BridgeSessions(NullLogger<BridgeSessions>.Instance);
        var cfg = new PluginConfiguration { CacheMode = "memory", MemoryBudgetMb = 8, PythonPath = python, SegmentType = "mpegts" };
        var token = sessions.Register("test-source.mp4", cfg)!;
        var session = sessions.Find(token)!;
        Check(session.IsMemory && session.Root == "", "Memory registration creates no cache directory");
        var middleware = new BridgeMiddleware(_ => throw new Exception("Unexpected route"), NullLogger<BridgeMiddleware>.Instance);
        async Task<int> Request(string method, string name = "seg_000000.ts", string? key = null, IPAddress? address = null, string? testToken = null, long? size = 1)
        {
            var context = new DefaultHttpContext();
            context.Connection.RemoteIpAddress = address ?? IPAddress.Loopback;
            context.Request.Method = method;
            context.Request.Path = $"/VsrEverywhere/ingest/{testToken ?? token}/{name}";
            context.Request.Body = new MemoryStream([5]);
            context.Request.ContentLength = size;
            if (key is not null) context.Request.Headers["X-Vsr-Key"] = key;
            await middleware.InvokeAsync(context, sessions);
            return context.Response.StatusCode;
        }
        Check(await Request("PUT", key: session.IngestKey, address: IPAddress.Parse("192.0.2.8")) == 403, "Non-loopback caller rejected");
        Check(await Request("POST", key: session.IngestKey) == 405, "Only PUT/DELETE ingest methods accepted");
        Check(await Request("PUT") == 403, "Missing key rejected");
        Check(await Request("PUT", key: new string('0', 64)) == 403, "Wrong key rejected");
        Check(await Request("PUT", key: new string('z', 64)) == 403, "Malformed key rejected");
        Check(await Request("PUT", key: session.IngestKey, testToken: new string('a', 48)) == 404, "Unknown token rejected");
        Check(await Request("PUT", key: session.IngestKey, testToken: "bad") == 404, "Malformed token rejected");
        Check(await Request("PUT", "ffmpeg-vsr.log", session.IngestKey) == 404, "Non-media filename rejected");
        Check(await Request("PUT", "seg_000000.ts\n", session.IngestKey) == 404, "Trailing newline rejected");
        Check(await Request("PUT", key: session.IngestKey, size: MemoryHlsStore.MaximumFileBytes + 1L) == 413, "Endpoint size cap");
        Check(await Request("PUT", key: session.IngestKey, address: IPAddress.IPv6Loopback) == 204, "IPv6 loopback accepted");
        Check(await Request("DELETE", key: session.IngestKey) == 204, "Authenticated DELETE accepted");
        var captured = Path.Combine(AppContext.BaseDirectory, "worker", "memory-arguments.json");
        if (File.Exists(captured)) File.Delete(captured);
        var get = new DefaultHttpContext();
        get.Connection.LocalPort = 8096;
        get.Request.Method = "GET";
        get.Request.PathBase = "/jellyfin";
        get.Request.Path = $"/VsrEverywhere/hls/{token}/seg_000000.ts";
        get.Response.Body = new MemoryStream();
        var workerExit = session.MemoryChanged;
        await middleware.InvokeAsync(get, sessions);
        Check(get.Response.StatusCode == 200 && get.Response.ContentType == "video/mp2t" && get.Response.Headers.CacheControl == "no-store", "Memory GET headers match disk");
        Check(((MemoryStream)get.Response.Body).ToArray().SequenceEqual(new byte[] { 5 }), "Memory GET returns complete body");
        var process = (System.Diagnostics.Process)typeof(BridgeSession).GetField("_worker", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.GetValue(session)!;
        process.StandardInput.Close();
        var timeout = DateTime.UtcNow.AddSeconds(10);
        while ((!File.Exists(captured) || new FileInfo(captured).Length == 0 || !session.HasExited) && DateTime.UtcNow < timeout) await Task.Delay(25);
        await workerExit.WaitAsync(TimeSpan.FromSeconds(5));
        using (var result = JsonDocument.Parse(await File.ReadAllTextAsync(captured)))
        {
            var command = result.RootElement.GetProperty("args").EnumerateArray().Select(value => value.GetString()!).ToArray();
            Check(command.Contains("--memory-output"), "Memory launch flag");
            Check(command.Contains("--serve"), "Memory worker persists across seeks");
            Check(command[Array.IndexOf(command, "--output") + 1] == $"http://127.0.0.1:8096/jellyfin/VsrEverywhere/ingest/{token}/0/index.m3u8", "Ingest output uses actual loopback port and path base");
            Check(result.RootElement.GetProperty("key").GetString() == session.IngestKey, "Secret only passed via worker environment");
            Check(!command.Contains(session.IngestKey), "Secret absent from command line");
        }
        var head = new DefaultHttpContext();
        head.Connection.LocalPort = 8096;
        head.Request.Method = "HEAD";
        head.Request.Path = get.Request.Path;
        head.Response.Body = new MemoryStream();
        await middleware.InvokeAsync(head, sessions);
        Check(head.Response.StatusCode == 200 && head.Response.ContentLength == 1 && head.Response.Body.Length == 0, "HEAD serves retained tail after worker exits");
    }

    private sealed class DelayedEofStream : MemoryStream
    {
        internal readonly TaskCompletionSource ReadStarted = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal readonly TaskCompletionSource Finish = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private bool _first = true;
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            if (_first) { _first = false; buffer.Span[0] = 23; ReadStarted.TrySetResult(); return 1; }
            await Finish.Task.WaitAsync(cancellationToken);
            return 0;
        }
    }

    private sealed class GeneratedStream(long remaining) : MemoryStream
    {
        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            var count = (int)Math.Min(remaining, buffer.Length);
            buffer.Span[..count].Fill(42);
            remaining -= count;
            return ValueTask.FromResult(count);
        }
    }
}
