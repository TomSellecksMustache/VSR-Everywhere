using Microsoft.Extensions.DependencyInjection;
using MediaBrowser.Controller.Net;
using MediaBrowser.Model.Dlna;
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
        // Resolve authenticated client identity through the registered access token.
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
        string? registeredDeviceId = null, registeredAppName = null;
        try
        {
            var authorizationContext = context.HttpContext.RequestServices
                .GetRequiredService<MediaBrowser.Controller.Net.IAuthorizationContext>();
            authorization = await authorizationContext.GetAuthorizationInfo(context.HttpContext).ConfigureAwait(false);
            if (!authorization.IsAuthenticated || authorization.IsApiKey || authorization.GetType().GetProperty("User")?.GetValue(authorization) is null ||
                string.IsNullOrWhiteSpace(authorization.Token))
            {
                _logger.LogInformation("VsrEverywhere DEVICESELECT skipped: no authenticated device user");
                return;
            }

            var manager = context.HttpContext.RequestServices.GetRequiredService<MediaBrowser.Controller.Devices.IDeviceManager>();
            (registeredDeviceId, registeredAppName) = RegisteredDevice(manager, new Jellyfin.Data.Queries.DeviceQuery
            {
                AccessToken = authorization.Token,
                UserId = authorization.UserId
            });
        }
        catch (Exception ex)
        {
            _logger.LogWarning("VsrEverywhere DEVICESELECT authorization failed: {ExceptionType}", ex.GetType().Name);
            return;
        }

        if (string.IsNullOrWhiteSpace(registeredDeviceId) ||
            (!cfg.EnableAllDevices && !approvedDeviceIds.Contains(registeredDeviceId, StringComparer.Ordinal)))
        {
            _logger.LogInformation("VsrEverywhere DEVICESELECT skipped: device not approved");
            return;
        }

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
            var v = source.VideoStream;
            if (!SupportsInput(v, cfg.ProcessAbove800p))
            {
                _logger.LogWarning("VsrEverywhere TRACE source rejected: unsupported dimensions, bit depth, or HDR input");
                continue;
            }
            var deviceProfile = context.ActionArguments.Values.Where(value => value is not null)
                .Select(value => value!.GetType().GetProperty("DeviceProfile")?.GetValue(value)).OfType<DeviceProfile>().FirstOrDefault();
            var profileSource = deviceProfile is null ? "registered device" : "playback request";
            deviceProfile ??= context.HttpContext.RequestServices.GetRequiredService<MediaBrowser.Controller.Devices.IDeviceManager>()
                .GetCapabilities(registeredDeviceId)?.DeviceProfile;
            var supportsHdr = EnhancementProfiles.SupportsHdr10(deviceProfile);
            var hdr = cfg.EnableAutoHdr && supportsHdr;
            if (!cfg.EnableVsr && !cfg.EnableFrameInterpolation && !hdr) continue;
            _logger.LogInformation("VSR Everywhere HDR requested={Requested} clientHdr10={ClientHdr10} profile={ProfileSource} output={Range}",
                cfg.EnableAutoHdr, supportsHdr, deviceProfile is null ? "missing" : profileSource, hdr ? "HDR10" : "SDR");
            var videoMbps = EnhancementProfiles.DeliveryMbps(cfg.VideoMbps, registeredAppName);
            if (videoMbps != cfg.VideoMbps)
                _logger.LogInformation("VSR Everywhere delivery bitrate limited to {Mbps} Mbps for this client", videoMbps);
            var crop = await StreamingCrop.Detect(source.Path, v, cfg, _logger, context.HttpContext.RequestAborted).ConfigureAwait(false);
            var token = _sessions.Register(source.Path, cfg, hdr, videoMbps, crop);
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
            crop.Describe(v, hdr);
            _logger.LogWarning("VsrEverywhere TRACE SUCCESS: VSR HLS source created for matching client");
            return;
        }
        _logger.LogWarning("VsrEverywhere TRACE finished: no eligible media sources");
    }

    internal static bool SupportsInput(MediaBrowser.Model.Entities.MediaStream video, bool processAbove800p)
    {
        var transfer = video.GetType().GetProperty("ColorTransfer")?.GetValue(video)?.ToString()?.ToLowerInvariant();
        var range = video.GetType().GetProperty("VideoRangeType")?.GetValue(video)?.ToString()?.ToLowerInvariant() ?? "";
        return video.Width is > 0 and <= 1920 && video.Height is > 0 and <= 1080
            && (processAbove800p || video.Height <= 800)
            && video.BitDepth is not > 10 && transfer is not ("smpte2084" or "arib-std-b67" or "pq" or "hlg")
            && !range.Contains("hdr") && !range.Contains("hlg") && !range.Contains("dovi") && !range.Contains("dolby");
    }

    internal static (string? DeviceId, string? AppName) RegisteredDevice(
        MediaBrowser.Controller.Devices.IDeviceManager manager, Jellyfin.Data.Queries.DeviceQuery query)
    {
        // The database entity returned by GetDevices moved namespaces between Jellyfin versions.
        var result = typeof(MediaBrowser.Controller.Devices.IDeviceManager).GetMethod("GetDevices", [typeof(Jellyfin.Data.Queries.DeviceQuery)])?.Invoke(manager, [query]);
        var items = result?.GetType().GetProperty("Items")?.GetValue(result) as System.Collections.IEnumerable;
        var device = items?.Cast<object>().FirstOrDefault();
        return (device?.GetType().GetProperty("DeviceId")?.GetValue(device) as string,
            device?.GetType().GetProperty("AppName")?.GetValue(device) as string);
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
