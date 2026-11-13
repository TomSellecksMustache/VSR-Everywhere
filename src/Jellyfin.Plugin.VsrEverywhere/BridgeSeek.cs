using System.Buffers;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

public sealed partial class BridgeSession
{
    private readonly SemaphoreSlim _commands = new(1, 1);
    private TaskCompletionSource _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly Queue<DateTimeOffset> _restarts = new();
    private string? _memoryOutput;
    private double _segmentSeconds;
    private int _segmentCount, _runStart, _runHead = -1;
    private long _run;
    private bool _runActive;
    internal bool AcceptsRun(long? run) => run.HasValue ? run.Value == Interlocked.Read(ref _run) : !Started;

    private bool AllowRestart()
    {
        var now = DateTimeOffset.UtcNow;
        while (_restarts.TryPeek(out var started) && now - started > TimeSpan.FromMinutes(2)) _restarts.Dequeue();
        if (_restarts.Count >= 3)
        {
            _logger.LogError("VSR Everywhere worker restart limit reached for {Session}", Token);
            return false;
        }
        _restarts.Enqueue(now);
        return true;
    }

    private async Task ReadWorkerOutput(Process worker)
    {
        var completion = _ready;
        try
        {
            while (await worker.StandardOutput.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                _logger.LogDebug("VSR Everywhere worker: {Message}", line);
                var jsonStart = line.IndexOf('{');
                if (!IsMemory || jsonStart < 0) continue;
                try
                {
                    using var message = JsonDocument.Parse(line[jsonStart..]);
                    var root = message.RootElement;
                    if (!root.TryGetProperty("event", out var kind) || kind.GetString() != "ready") continue;
                    var numerator = root.GetProperty("fps_num").GetInt32();
                    var denominator = root.GetProperty("fps_den").GetInt32();
                    var gop = root.GetProperty("gop_frames").GetInt32();
                    var duration = root.GetProperty("duration_s").GetDouble();
                    if (numerator <= 0 || denominator <= 0 || gop <= 0 || !double.IsFinite(duration) || duration <= 0)
                        throw new InvalidDataException("Invalid worker timeline");
                    var fps = (double)numerator / denominator;
                    var seconds = (double)gop * denominator / numerator;
                    var count = Math.Ceiling(duration / seconds - 1e-9);
                    if (fps is < 1 or > 120 || gop != Math.Round(2 * fps) || count is < 1 or > 999999)
                        throw new InvalidDataException("Unsupported worker timeline");
                    _segmentSeconds = seconds; _segmentCount = (int)count;
                    var playlist = new StringBuilder("#EXTM3U\n#EXT-X-VERSION:")
                        .Append(_cfg.SegmentType == "mpegts" ? "3\n" : "7\n#EXT-X-MAP:URI=\"init.mp4\"\n")
                        .Append("#EXT-X-INDEPENDENT-SEGMENTS\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-TARGETDURATION:3\n#EXT-X-MEDIA-SEQUENCE:0\n");
                    for (var index = 0; index < _segmentCount; index++)
                    {
                        playlist.Append("#EXTINF:").Append(Math.Min(seconds, duration - index * seconds).ToString("0.000000000", CultureInfo.InvariantCulture))
                            .Append(",\nseg_").Append(index.ToString("D6", CultureInfo.InvariantCulture))
                            .Append(_cfg.SegmentType == "mpegts" ? ".ts\n" : ".m4s\n");
                    }
                    playlist.Append("#EXT-X-ENDLIST\n");
                    var bytes = Encoding.UTF8.GetBytes(playlist.ToString());
                    var result = await _memory!.PutAsync(Token, "index.m3u8", new MemoryStream(bytes), bytes.Length, CancellationToken.None).ConfigureAwait(false);
                    if (result != 204) throw new IOException("Cannot reserve playlist memory");
                    completion.TrySetResult();
                }
                catch (Exception ex) when (ex is JsonException or KeyNotFoundException or FormatException or InvalidOperationException or IOException)
                {
                    _logger.LogError(ex, "VSR Everywhere could not read worker metadata");
                    completion.TrySetException(ex);
                }
            }
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException) { }
        finally
        {
            completion.TrySetException(new IOException("VSR Everywhere worker exited before reporting its timeline"));
            _memory?.Notify(Token);
        }
    }

    private async Task SendCommand(object command, CancellationToken ct)
    {
        if (_worker is null || HasExited) throw new IOException("VSR Everywhere worker exited");
        await _worker.StandardInput.WriteLineAsync(JsonSerializer.Serialize(command).AsMemory(), ct).ConfigureAwait(false);
        await _worker.StandardInput.FlushAsync(ct).ConfigureAwait(false);
    }

    internal async Task<int> PrepareMemory(string name, string? output, CancellationToken ct)
    {
        if (name == "init.mp4" && _cfg.SegmentType == "mpegts") return 404;
        if (_memory!.Contains(Token, name) && HasExited) return 200;
        if (!await EnsureStarted(ct, output).ConfigureAwait(false)) return 502;
        try { await _ready.Task.WaitAsync(TimeSpan.FromSeconds(25), ct).ConfigureAwait(false); }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogError(ex, "VSR Everywhere worker timeline unavailable");
            return 502;
        }
        if (name == "index.m3u8") return 200;
        if (name == "init.mp4" && _memory.Contains(Token, name)) return 200;
        var isSegment = name.StartsWith("seg_", StringComparison.Ordinal);
        var segment = isSegment ? int.Parse(name.AsSpan(4, 6), CultureInfo.InvariantCulture) : 0;
        if (isSegment && (segment >= _segmentCount || !name.EndsWith(_cfg.SegmentType == "mpegts" ? ".ts" : ".m4s", StringComparison.Ordinal))) return 404;
        await _commands.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (IsDisposed) return 404;
            var cached = _memory.Contains(Token, name);
            var lookahead = Math.Max(1, (int)Math.Ceiling(Math.Clamp(_cfg.SeekLookaheadSeconds, 2, 600) / _segmentSeconds));
            var catchup = (int)Math.Ceiling(Math.Clamp(_cfg.SeekCatchupSeconds, 0, 60) / _segmentSeconds);
            var limit = Math.Min(_segmentCount - 1, segment + lookahead);
            var commandLimit = cached && _runActive && (segment < _runStart || segment > _runHead + catchup)
                ? Math.Max(0, _runHead) : limit;
            if (!cached && (!_runActive || segment < _runStart || segment > _runHead + catchup))
            {
                _run++; _runStart = segment; _runHead = segment - 1; _runActive = true;
                _memory.BeginRun(Token, _run, segment);
                await SendCommand(new { cmd = "seek", segment, run = _run }, ct).ConfigureAwait(false);
            }
            if (_runActive)
            {
                _memory.SetWindow(Token, Math.Max(0, segment - Math.Clamp(_cfg.RollingRewindMinutes, 2, 60) * 30), limit);
                await SendCommand(new { cmd = "limit", segment = commandLimit }, ct).ConfigureAwait(false);
            }
            return 200;
        }
        catch (IOException) { _runActive = false; return 502; }
        catch (OperationCanceledException) { _runActive = false; throw; }
        finally { _commands.Release(); }
    }

    internal async Task<int> PutMemory(string name, Stream source, long? length, CancellationToken ct, long? run = null)
    {
        if (_memory is null) return 404;
        if (run.HasValue && run != Interlocked.Read(ref _run)) return 409;
        if (name == "index.m3u8" && Started)
        {
            if (length > 1024 * 1024) return 413;
            var buffer = ArrayPool<byte>.Shared.Rent(65536);
            try
            {
                var total = 0;
                int count;
                while ((count = await source.ReadAsync(buffer, ct).ConfigureAwait(false)) > 0)
                    if ((total += count) > 1024 * 1024) return 413;
                return 204;
            }
            finally { ArrayPool<byte>.Shared.Return(buffer); }
        }
        var status = await _memory.PutAsync(Token, name, source, length, ct, run, keepExisting: name == "init.mp4").ConfigureAwait(false);
        if (status == 204 && name.StartsWith("seg_", StringComparison.Ordinal))
        {
            await _commands.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                if (!run.HasValue || run == _run) _runHead = Math.Max(_runHead, int.Parse(name.AsSpan(4, 6), CultureInfo.InvariantCulture));
            }
            finally { _commands.Release(); }
        }
        return status;
    }

    internal void DeleteMemory(string name, long? run = null) => _memory?.Delete(Token, name, run);
}
