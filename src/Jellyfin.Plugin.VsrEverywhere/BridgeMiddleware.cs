using System.Text.RegularExpressions;
using System.Net;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Connections.Features;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

public sealed class BridgeStartupFilter : IStartupFilter
{
    public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next)
        => app => { app.UseMiddleware<BridgeMiddleware>(); next(app); };
}

/// <summary>
/// Serve HLS strictly by a cryptographically random playback capability token.
/// No user-supplied filesystem paths; source selection must happen in Jellyfin's authorized
/// PlaybackInfo action. This is NOT intended to be an exposed external media server.
/// </summary>
public sealed class BridgeMiddleware
{
    private readonly RequestDelegate _next;
    private readonly ILogger<BridgeMiddleware> _logger;
    private static readonly Regex FileName = new(@"^(index\.m3u8|init\.mp4|seg_[0-9]{6}\.(ts|m4s))\z", RegexOptions.Compiled | RegexOptions.CultureInvariant);
    public BridgeMiddleware(RequestDelegate next, ILogger<BridgeMiddleware> logger)
        => (_next, _logger) = (next, logger);

    public async Task InvokeAsync(HttpContext http, BridgeSessions sessions)
    {
        if (http.Request.Path.StartsWithSegments("/VsrEverywhere/ingest", out var ingest))
        {
            await Ingest(http, sessions, ingest).ConfigureAwait(false);
            return;
        }
        if (!http.Request.Path.StartsWithSegments("/VsrEverywhere/hls", out var rest))
        {
            await _next(http).ConfigureAwait(false);
            return;
        }
        if (http.Request.Method != "GET" && http.Request.Method != "HEAD")
        {
            http.Response.StatusCode = 405; return;
        }
        var parts = rest.Value?.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (parts is not { Length: 2 } || !FileName.IsMatch(parts[1]) ||
            parts[0].Length != 48 || !parts[0].All(Uri.IsHexDigit))
        {
            http.Response.StatusCode = 404; return;
        }
        var session = sessions.Get(parts[0]);
        if (session is null) { http.Response.StatusCode = 404; return; }
        var scheme = http.Features.Get<ITlsHandshakeFeature>() is null ? "http" : "https";
        var memoryOutput = session.IsMemory && http.Connection.LocalPort > 0
            ? new UriBuilder(scheme, "127.0.0.1", http.Connection.LocalPort,
                http.Request.PathBase.ToUriComponent() + "/VsrEverywhere/ingest/" + session.Token + "/0/index.m3u8").Uri.AbsoluteUri : null;
        if (session.IsMemory)
        {
            var status = await session.PrepareMemory(parts[1], memoryOutput, http.RequestAborted).ConfigureAwait(false);
            if (status != 200) { http.Response.StatusCode = status; return; }
            await ServeMemory(http, session, parts[1]).ConfigureAwait(false);
            return;
        }
        if (!await session.EnsureStarted(http.RequestAborted).ConfigureAwait(false))
        { http.Response.StatusCode = 503; return; }
        var file = Path.Combine(session.Root, parts[1]);
        // Wait for the encoder to create the requested segment. Do not hang indefinitely.
        var waitLimit = DateTimeOffset.UtcNow.AddSeconds(parts[1] == "index.m3u8" ? 25 : 35);
        while (DateTimeOffset.UtcNow < waitLimit && !http.RequestAborted.IsCancellationRequested)
        {
            try
            {
                if (File.Exists(file) && new FileInfo(file).Length > 0 && !File.Exists(file + ".tmp"))
                {
                    http.Response.ContentType = parts[1].EndsWith(".m3u8", StringComparison.Ordinal) ? "application/vnd.apple.mpegurl"
                        : parts[1].EndsWith(".ts", StringComparison.Ordinal) ? "video/mp2t" : "video/mp4";
                    http.Response.Headers.CacheControl = "no-store";
                    using var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite, 65536, true);
                    if (http.Request.Method == "HEAD") { http.Response.ContentLength = stream.Length; return; }
                    await stream.CopyToAsync(http.Response.Body, http.RequestAborted).ConfigureAwait(false);
                    return;
                }
            }
            catch (IOException) { /* FFmpeg may be replacing the playlist atomically */ }
            if (session.HasExited) break;
            await Task.Delay(200, http.RequestAborted).ConfigureAwait(false);
        }
        _logger.LogWarning("VSR Everywhere segment unavailable for session {Session}: {File}", session.Token, parts[1]);
        http.Response.StatusCode = session.HasExited ? 502 : 503;
        await http.Response.WriteAsync("VSR Everywhere encoder failed or segment not ready", http.RequestAborted).ConfigureAwait(false);
    }

    private static async Task Ingest(HttpContext http, BridgeSessions sessions, PathString rest)
    {
        var address = http.Connection.RemoteIpAddress;
        if (address is null || !(IPAddress.IsLoopback(address) || address.IsIPv4MappedToIPv6 && IPAddress.IsLoopback(address.MapToIPv4())))
        { http.Response.StatusCode = 403; return; }
        if (http.Request.Method is not ("PUT" or "DELETE")) { http.Response.StatusCode = 405; return; }
        var parts = rest.Value?.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (parts is null || parts.Length is not (2 or 3) || !FileName.IsMatch(parts[^1]) || parts[0].Length != 48 || !parts[0].All(Uri.IsHexDigit))
        { http.Response.StatusCode = 404; return; }
        long? run = null;
        if (parts.Length == 3)
        {
            if (!long.TryParse(parts[1], System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out var parsed) || parsed > int.MaxValue)
            { http.Response.StatusCode = 404; return; }
            run = parsed;
        }
        var session = sessions.Find(parts[0]);
        if (session is null || !session.IsMemory) { http.Response.StatusCode = 404; return; }
        var key = http.Request.Headers["X-Vsr-Key"];
        if (key.Count != 1 || !session.AcceptsKey(key.ToString())) { http.Response.StatusCode = 403; return; }
        if (!session.AcceptsRun(run)) { http.Response.StatusCode = 409; return; }
        if (http.Request.Method == "DELETE")
        {
            session.DeleteMemory(parts[^1], run);
            http.Response.StatusCode = 204;
            return;
        }
        var bodyLimit = http.Features.Get<IHttpMaxRequestBodySizeFeature>();
        if (bodyLimit is { IsReadOnly: false }) bodyLimit.MaxRequestBodySize = MemoryHlsStore.MaximumFileBytes;
        http.Response.StatusCode = await session.PutMemory(parts[^1], http.Request.Body, http.Request.ContentLength, http.RequestAborted, run).ConfigureAwait(false);
    }

    private static async Task ServeMemory(HttpContext http, BridgeSession session, string name)
    {
        var deadline = DateTimeOffset.UtcNow.AddSeconds(name == "index.m3u8" ? 25 : 35);
        while (!http.RequestAborted.IsCancellationRequested)
        {
            var changed = session.MemoryChanged;
            using var body = session.MemoryReady(name) ? session.OpenMemory(name) : null;
            if (body is not null)
            {
                http.Response.ContentType = name.EndsWith(".m3u8", StringComparison.Ordinal) ? "application/vnd.apple.mpegurl"
                    : name.EndsWith(".ts", StringComparison.Ordinal) ? "video/mp2t" : "video/mp4";
                http.Response.Headers.CacheControl = "no-store";
                if (http.Request.Method == "HEAD") { http.Response.ContentLength = body.Length; return; }
                await body.CopyToAsync(http.Response.Body, http.RequestAborted).ConfigureAwait(false);
                return;
            }
            var remaining = deadline - DateTimeOffset.UtcNow;
            if (session.HasExited || session.IsDisposed || remaining <= TimeSpan.Zero) break;
            try { await changed.WaitAsync(remaining, http.RequestAborted).ConfigureAwait(false); }
            catch (TimeoutException) { break; }
        }
        http.Response.StatusCode = session.HasExited || session.IsDisposed ? 502 : 503;
        await http.Response.WriteAsync("VSR Everywhere encoder failed or segment not ready", http.RequestAborted).ConfigureAwait(false);
    }
}
