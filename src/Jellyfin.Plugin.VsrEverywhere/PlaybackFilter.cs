using Microsoft.Extensions.DependencyInjection;
using MediaBrowser.Controller.Net;
using Jellyfin.Data.Enums;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.MediaInfo;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

/// <summary>Playback authorization and eligibility logging.
/// Do not log API keys, source paths, user tokens, or full playback URLs.</summary>
public sealed class PlaybackFilter : IAsyncActionFilter
{
    private readonly BridgeSessions _sessions;
    private readonly ILogger<PlaybackFilter> _logger;
    public PlaybackFilter(BridgeSessions sessions, ILogger<PlaybackFilter> logger)
        => (_sessions, _logger) = (sessions, logger);

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        if (context.HttpContext.Request.Path.Value?.EndsWith("/PlaybackInfo", StringComparison.OrdinalIgnoreCase) == true)
            _logger.LogWarning("VsrEverywhere HOOKFIX3 MVC filter invoked; method={Method}", context.HttpContext.Request.Method);
        var request = context.HttpContext.Request;
        // We log only PlaybackInfo calls, so ordinary API traffic stays quiet.
        var playbackRequest = request.Path.Value?.EndsWith("/PlaybackInfo", StringComparison.OrdinalIgnoreCase) == true;
        if (playbackRequest)
            _logger.LogWarning("VsrEverywhere TRACE entered PlaybackInfo filter; method={Method}", request.Method);

        var result = await next().ConfigureAwait(false);
        if (!playbackRequest) return;
        var cfg = Plugin.Instance?.Configuration;
        if (cfg?.Enabled != true)
        {
            _logger.LogWarning("VsrEverywhere TRACE skipped: enhancement disabled");
            return;
        }
        if (!request.Method.Equals("POST", StringComparison.OrdinalIgnoreCase))
        {
            _logger.LogWarning("VsrEverywhere TRACE skipped: not a POST PlaybackInfo request");
            return;
        }
        if (result.Exception is not null || result.Canceled)
        {
            _logger.LogWarning("VsrEverywhere TRACE skipped: action failed or canceled; exceptionType={ExceptionType}", result.Exception?.GetType().Name ?? "none");
            return;
        }
        if (result.Result is not ObjectResult obj || obj.Value is not PlaybackInfoResponse response)
        {
            _logger.LogWarning("VsrEverywhere TRACE skipped: action result type={ResultType}; value type={ValueType}",
                result.Result?.GetType().Name ?? "null", (result.Result as ObjectResult)?.Value?.GetType().Name ?? "null");
            return;
        }
        // Resolve authenticated client identity through Jellyfin 12.1.
        // Roku may not send the legacy X-Emby-Client headers.
        // DEVICESELECT: Device authorization is established by the *registered access token*,
        // not client-name/DeviceId headers, which are missing on some Roku requests and spoofable.
        var approvedDeviceIds = cfg.EnabledDeviceIds ?? Array.Empty<string>();
        if (!cfg.EnableAllDevices && approvedDeviceIds.Length == 0)
        {
            _logger.LogInformation("VsrEverywhere DEVICESELECT skipped: no devices enabled");
            return;
        }

        MediaBrowser.Controller.Net.AuthorizationInfo authorization;
        Jellyfin.Database.Implementations.Entities.Security.Device? registeredDevice;
        try
        {
            var authorizationContext = context.HttpContext.RequestServices
                .GetRequiredService<MediaBrowser.Controller.Net.IAuthorizationContext>();
            authorization = await authorizationContext.GetAuthorizationInfo(context.HttpContext).ConfigureAwait(false);
            if (!authorization.IsAuthenticated || authorization.IsApiKey || authorization.User is null ||
                string.IsNullOrWhiteSpace(authorization.Token))
            {
                _logger.LogInformation("VsrEverywhere DEVICESELECT skipped: no authenticated device user");
                return;
            }

            var manager = context.HttpContext.RequestServices.GetRequiredService<MediaBrowser.Controller.Devices.IDeviceManager>();
            registeredDevice = manager.GetDevices(new Jellyfin.Data.Queries.DeviceQuery
            {
                AccessToken = authorization.Token,
                UserId = authorization.UserId
            }).Items.FirstOrDefault();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("VsrEverywhere DEVICESELECT authorization failed: {ExceptionType}", ex.GetType().Name);
            return;
        }

        if (registeredDevice is null || string.IsNullOrWhiteSpace(registeredDevice.DeviceId) ||
            (!cfg.EnableAllDevices && !approvedDeviceIds.Contains(registeredDevice.DeviceId, StringComparer.Ordinal)))
        {
            _logger.LogInformation("VsrEverywhere DEVICESELECT skipped: device not approved");
            return;
        }

        var client = registeredDevice.AppName + " / " + registeredDevice.DeviceName;
        _logger.LogInformation("VsrEverywhere DEVICESELECT approved device for VSR");

        if (!IsSafePlaybackRequest(context.ActionArguments))
        {
            _logger.LogWarning("VsrEverywhere TRACE skipped: requested audio/subtitle selection unsupported");
            return;
        }
        var sources = response.MediaSources;
        if (sources is null || sources.Count == 0)
        {
            _logger.LogWarning("VsrEverywhere TRACE skipped: PlaybackInfo has no media sources");
            return;
        }
        _logger.LogWarning("VsrEverywhere TRACE playback candidate count={Count}", sources.Count);
        foreach (var source in sources)
        {
            if (!source.SupportsTranscoding || source.IsRemote || source.IsInfiniteStream || source.RequiresOpening)
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: transcoding={Transcoding}, remote={Remote}, infinite={Infinite}, opening={Opening}",
                    source.SupportsTranscoding, source.IsRemote, source.IsInfiniteStream, source.RequiresOpening);
                continue;
            }
            if (string.IsNullOrWhiteSpace(source.Path) || !File.Exists(source.Path))
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: source file inaccessible to Jellyfin process");
                continue;
            }
            if (source.VideoStream is null || source.VideoStream.Width < 1 || source.VideoStream.Height < 1)
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: missing dimensions/video stream");
                continue;
            }
            if (source.VideoStream.Width > 1920 || source.VideoStream.Height > 1080)
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: dimensions above 1920x1080");
                continue;
            }
            var v = source.VideoStream;
            var transfer = v.GetType().GetProperty("ColorTransfer")?.GetValue(v)?.ToString()?.ToLowerInvariant();
            if (transfer is "smpte2084" or "arib-std-b67" or "pq" or "hlg")
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: HDR input excluded");
                continue;
            }
            var bitDepth = v.GetType().GetProperty("BitDepth")?.GetValue(v);
            if (bitDepth is int depth && depth > 8)
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: >8-bit video excluded");
                continue;
            }
            if (!await _sessions.BackendAvailable(cfg, context.HttpContext.RequestAborted).ConfigureAwait(false)) return;
            var token = _sessions.Register(source.Path, cfg);
            if (token is null)
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: session registry unavailable");
                continue;
            }
            source.SupportsDirectPlay = false;
            source.SupportsDirectStream = false;
            source.SupportsTranscoding = true;
            source.TranscodingContainer = cfg.SegmentType == "mpegts" ? "ts" : "mp4";
            source.TranscodingSubProtocol = MediaStreamProtocol.hls;
            source.TranscodingUrl = $"/VsrEverywhere/hls/{token}/index.m3u8";
            _logger.LogWarning("VsrEverywhere TRACE SUCCESS: VSR HLS source created for matching client");
            return;
        }
        _logger.LogWarning("VsrEverywhere TRACE finished: no eligible media sources");
    }

    private static bool IsSafePlaybackRequest(IDictionary<string, object?> args)
    {
        foreach (var value in args.Values)
        {
            if (value is null) continue;
            var type = value.GetType();
            foreach (var propName in new[] { "SubtitleStreamIndex", "AudioStreamIndex" })
            {
                var prop = type.GetProperty(propName);
                if (prop is null) continue;
                var selected = prop.GetValue(value);
                if (selected is null) continue;
                if (selected is int i && (propName == "SubtitleStreamIndex" ? i >= 0 : i > 0)) return false;
            }
        }
        return true;
    }
}
internal sealed class VsrEverywhereDeviceSelection { }
