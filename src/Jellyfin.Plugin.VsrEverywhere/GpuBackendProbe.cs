using System.Diagnostics;
using System.Text.Json;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

internal sealed class GpuBackendProbe
{
    private readonly SemaphoreSlim _gate = new(1, 1);
    private (string Python, string Ffmpeg, string Backend) _key;
    private DateTimeOffset _expires;
    private bool _available;

    internal async Task<bool> IsAvailable(PluginConfiguration cfg, ILogger logger, CancellationToken cancellationToken)
    {
        await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            var key = (cfg.PythonPath, cfg.FfmpegPath, cfg.Backend);
            if (key == _key && DateTimeOffset.UtcNow < _expires) return _available;
            _key = key;
            _available = false;
            _expires = DateTimeOffset.UtcNow.AddMinutes(1);
            if (cfg.Backend is not ("auto" or "nvidia" or "intel" or "vsr")) return false;
            var script = Path.Combine(Path.GetDirectoryName(typeof(Plugin).Assembly.Location)!, "worker", "vsr_backends.py");
            var info = new ProcessStartInfo(cfg.PythonPath)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            };
            foreach (var argument in new[] { script, "--backend", cfg.Backend == "vsr" ? "nvidia" : cfg.Backend, "--ffmpeg", cfg.FfmpegPath })
                info.ArgumentList.Add(argument);
            using var process = Process.Start(info) ?? throw new IOException("GPU probe could not start");
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));
            var output = process.StandardOutput.ReadToEndAsync(timeout.Token);
            var errors = process.StandardError.ReadToEndAsync(timeout.Token);
            try
            {
                await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
                var text = await output.ConfigureAwait(false);
                await errors.ConfigureAwait(false);
                using var report = JsonDocument.Parse(text);
                _available = process.ExitCode == 0 && report.RootElement.GetProperty("available").GetBoolean();
                if (!_available)
                {
                    var reason = report.RootElement.TryGetProperty("reason", out var detail) ? detail.GetString() : "GPU is not ready";
                    logger.LogWarning("VSR Everywhere GPU backend {Backend} is unavailable: {Reason}; using standard Jellyfin playback", cfg.Backend, reason);
                }
            }
            finally
            {
                if (!process.HasExited) process.Kill(entireProcessTree: true);
            }
        }
        catch (Exception error) when (error is IOException or System.ComponentModel.Win32Exception or JsonException
                                      or InvalidOperationException or KeyNotFoundException or OperationCanceledException or ArgumentException)
        {
            if (cancellationToken.IsCancellationRequested) throw;
            logger.LogWarning(error, "VSR Everywhere GPU probe failed; using standard Jellyfin playback");
        }
        finally { _gate.Release(); }
        return _available;
    }
}
