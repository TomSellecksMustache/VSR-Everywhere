using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Text.Json;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using MediaBrowser.Model.Entities;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

internal sealed record StreamingCrop(int X, int Y, int Width, int Height, int StreamWidth, int StreamHeight)
{
    private static readonly ConcurrentDictionary<(string Path, long Length, long Modified, int W, int H), StreamingCrop> Cache = new();

    internal static (int Width, int Height) Fit(double aspect, int maxWidth, int maxHeight)
    {
        if (!double.IsFinite(aspect) || aspect <= 0) throw new ArgumentOutOfRangeException(nameof(aspect));
        var height = Math.Min(maxHeight, maxWidth / aspect);
        return (Math.Clamp((int)Math.Round(height * aspect / 2, MidpointRounding.AwayFromZero) * 2, 16, maxWidth),
            Math.Clamp((int)Math.Round(height / 2, MidpointRounding.AwayFromZero) * 2, 16, maxHeight));
    }

    internal static StreamingCrop Uncropped(MediaStream video, PluginConfiguration cfg)
    {
        var width = video.Width!.Value;
        var height = video.Height!.Value;
        var aspect = (double)width / height;
        var parts = (video.AspectRatio ?? "").Split(':', '/');
        if (parts.Length == 2 && double.TryParse(parts[0], NumberStyles.Float, CultureInfo.InvariantCulture, out var n)
            && double.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out var d)
            && double.IsFinite(n / d) && n > 0 && d > 0) aspect = n / d;
        var size = Fit(aspect, Math.Clamp(cfg.StreamWidth, 640, 7680) / 2 * 2, Math.Clamp(cfg.StreamHeight, 360, 4320) / 2 * 2);
        return new(0, 0, width, height, size.Width, size.Height);
    }

    internal static async Task<StreamingCrop> Detect(string source, MediaStream video, PluginConfiguration cfg, ILogger logger, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var fallback = Uncropped(video, cfg);
        var maxWidth = Math.Clamp(cfg.StreamWidth, 640, 7680) / 2 * 2;
        var maxHeight = Math.Clamp(cfg.StreamHeight, 360, 4320) / 2 * 2;
        Process? process = null;
        try
        {
            var file = new FileInfo(source);
            var key = (source, file.Length, file.LastWriteTimeUtc.Ticks, maxWidth, maxHeight);
            if (Cache.TryGetValue(key, out var cached)) return cached;
            var script = Path.Combine(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)!, "worker", "vsr_crop.py");
            var info = new ProcessStartInfo(cfg.PythonPath) { UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true };
            foreach (var value in new[] { script, source, "--ffmpeg", cfg.FfmpegPath, "--ffprobe", cfg.FfprobePath,
                "--max-width", maxWidth.ToString(CultureInfo.InvariantCulture), "--max-height", maxHeight.ToString(CultureInfo.InvariantCulture) })
                info.ArgumentList.Add(value);
            process = Process.Start(info) ?? throw new IOException("Crop probe could not start");
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromSeconds(7));
            var stdout = process.StandardOutput.ReadToEndAsync(timeout.Token);
            var stderr = process.StandardError.ReadToEndAsync(timeout.Token);
            await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
            await stderr.ConfigureAwait(false);
            if (process.ExitCode != 0) throw new IOException("Crop probe did not complete");
            using var document = JsonDocument.Parse(await stdout.ConfigureAwait(false));
            var json = document.RootElement;
            int Read(string name) => json.GetProperty(name).GetInt32();
            var plan = new StreamingCrop(Read("x"), Read("y"), Read("width"), Read("height"), Read("stream_width"), Read("stream_height"));
            if (Read("source_width") != video.Width || Read("source_height") != video.Height
                || plan.X < 0 || plan.Y < 0 || plan.Width < 16 || plan.Height < 16
                || plan.X + (long)plan.Width > video.Width || plan.Y + (long)plan.Height > video.Height
                || new[] { plan.X, plan.Y, plan.Width, plan.Height, plan.StreamWidth, plan.StreamHeight }.Any(value => value % 2 != 0)
                || plan.StreamWidth < 16 || plan.StreamHeight < 16 || plan.StreamWidth > maxWidth || plan.StreamHeight > maxHeight)
                throw new IOException("Crop probe returned invalid dimensions");
            if (Cache.Count >= 128) Cache.Clear();
            Cache[key] = plan;
            logger.LogInformation("VSR Everywhere picture={Width}x{Height} crop={X},{Y} stream={StreamWidth}x{StreamHeight}",
                plan.Width, plan.Height, plan.X, plan.Y, plan.StreamWidth, plan.StreamHeight);
            return plan;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception ex)
        {
            logger.LogWarning("VSR Everywhere crop probe unavailable ({Reason}); keeping the full source picture", ex.GetType().Name);
            return fallback;
        }
        finally
        {
            if (process is not null)
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { }
                process.Dispose();
            }
        }
    }

    internal void Describe(MediaStream video, bool hdr)
    {
        video.Width = StreamWidth;
        video.Height = StreamHeight;
        video.AspectRatio = $"{StreamWidth}:{StreamHeight}";
        video.IsAnamorphic = false;
        video.Codec = "hevc";
        video.CodecTag = null;
        video.Level = null;
        video.Profile = hdr ? "Main 10" : "Main";
        video.PixelFormat = hdr ? "yuv420p10le" : "yuv420p";
        video.BitDepth = hdr ? 10 : 8;
        video.ColorPrimaries = hdr ? "bt2020" : "bt709";
        video.ColorTransfer = hdr ? "smpte2084" : "bt709";
        video.ColorSpace = hdr ? "bt2020nc" : "bt709";
        video.ColorRange = "tv";
    }
}
