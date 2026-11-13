using System.Buffers;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

[assembly: InternalsVisibleTo("PluginChecks")]

namespace Jellyfin.Plugin.VsrEverywhere;

internal static class VsrMemoryBudget
{
    internal const long MiB = 1024 * 1024;
    internal static long Calculate(int configuredMb, long available)
        => available < 1024 * MiB ? 0 : configuredMb > 0
            ? (long)configuredMb * MiB : Math.Min(7 * 1024 * MiB, available / 5 * 3);

    internal static long Available()
    {
        if (!OperatingSystem.IsWindows()) return 0;
        var status = new MemoryStatus { Length = (uint)Marshal.SizeOf<MemoryStatus>() };
        return GlobalMemoryStatusEx(ref status) ? (long)status.AvailablePhysical : 0;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatus
    {
        public uint Length, Load;
        public ulong TotalPhysical, AvailablePhysical, TotalPageFile, AvailablePageFile;
        public ulong TotalVirtual, AvailableVirtual, AvailableExtendedVirtual;
    }
}

internal sealed class MemoryHlsStore
{
    internal const int MaximumFileBytes = 64 * 1024 * 1024;
    private const int ChunkBytes = 64 * 1024;
    private readonly object _gate = new();
    private readonly Dictionary<(string Token, string Name), Entry> _files = new();
    private readonly Dictionary<string, TaskCompletionSource> _signals = new(StringComparer.Ordinal);
    private readonly Dictionary<string, int> _heads = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _runs = new(StringComparer.Ordinal);
    private readonly HashSet<string> _onDemand = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (int First, int Last)> _windows = new(StringComparer.Ordinal);
    private long _budget, _allocated, _clock;
    private static readonly Regex Segment = new(@"^seg_([0-9]{6})\.(ts|m4s)\z", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    internal long AllocatedBytes { get { lock (_gate) return _allocated; } }
    internal long BudgetBytes { get { lock (_gate) return _budget; } }

    internal bool Register(string token, long budget, bool onDemand = false)
    {
        lock (_gate)
        {
            var previous = _budget;
            _budget = _signals.Count == 0 ? budget : Math.Min(previous, budget);
            if (_budget <= 0 || !MakeSpace(0)) { _budget = previous; return false; }
            _signals.Add(token, NewSignal());
            _heads.Add(token, -1);
            _runs.Add(token, 0);
            if (onDemand) _onDemand.Add(token);
            return true;
        }
    }

    internal Task Changed(string token)
    {
        lock (_gate) return _signals.TryGetValue(token, out var signal) ? signal.Task : Task.CompletedTask;
    }

    internal void Notify(string token)
    {
        lock (_gate) if (_signals.ContainsKey(token)) Pulse(token);
    }

    internal bool Contains(string token, string name)
    {
        lock (_gate) return _files.ContainsKey((token, name));
    }

    internal void BeginRun(string token, long run, int firstSegment)
    {
        lock (_gate)
        {
            if (!_signals.ContainsKey(token)) return;
            _runs[token] = run;
            _heads[token] = firstSegment - 1;
            Pulse(token);
        }
    }

    internal void SetWindow(string token, int first, int last)
    {
        lock (_gate) if (_signals.ContainsKey(token)) _windows[token] = (first, last);
    }

    internal async Task<int> PutAsync(string token, string name, Stream source, long? contentLength, CancellationToken ct,
        long? run = null, bool keepExisting = false)
    {
        var maximum = name == "index.m3u8" ? 1024 * 1024 : MaximumFileBytes;
        if (contentLength is < 0 || contentLength > maximum) return 413;
        var chunks = new List<byte[]>();
        var length = 0;
        var committed = false;
        try
        {
            var eof = false;
            var firstByte = new byte[1];
            while (!eof && length < maximum)
            {
                // Detect EOF before reserving another chunk from a possibly full budget.
                if (await source.ReadAsync(firstByte, ct).ConfigureAwait(false) == 0) { eof = true; break; }
                var buffer = ArrayPool<byte>.Shared.Rent(ChunkBytes);
                lock (_gate)
                {
                    if (!_signals.ContainsKey(token)) { ArrayPool<byte>.Shared.Return(buffer); return 404; }
                    if (run.HasValue && _runs[token] != run.Value) { ArrayPool<byte>.Shared.Return(buffer); return 409; }
                    if (!MakeSpace(buffer.Length)) { ArrayPool<byte>.Shared.Return(buffer); return 507; }
                    _allocated += buffer.Length;
                    chunks.Add(buffer);
                }
                buffer[0] = firstByte[0];
                var used = 1;
                length++;
                while (used < buffer.Length && length < maximum)
                {
                    var read = await source.ReadAsync(buffer.AsMemory(used, Math.Min(buffer.Length - used, maximum - length)), ct).ConfigureAwait(false);
                    if (read == 0) { eof = true; break; }
                    used += read;
                    length += read;
                }
            }
            if (!eof && await source.ReadAsync(new byte[1], ct).ConfigureAwait(false) != 0) return 413;
            if (length == 0 || contentLength.HasValue && length != contentLength.Value) return 400;
            if (name == "index.m3u8" && !Encoding.UTF8.GetString(CopyBytes(chunks, length)).StartsWith("#EXTM3U", StringComparison.Ordinal)) return 400;
            lock (_gate)
            {
                if (!_signals.ContainsKey(token)) return 404;
                if (run.HasValue && _runs[token] != run.Value) return 409;
                if (keepExisting && _files.ContainsKey((token, name))) return 204;
                if (_files.Remove((token, name), out var old)) Retire(old);
                var sequence = Segment.Match(name);
                var number = sequence.Success ? int.Parse(sequence.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture) : -1;
                _files.Add((token, name), new Entry(chunks, length, number, ++_clock));
                if (number >= 0) _heads[token] = Math.Max(_heads[token], number);
                committed = true;
                Pulse(token);
                return 204;
            }
        }
        finally
        {
            if (!committed) lock (_gate) foreach (var chunk in chunks) Return(chunk);
        }
    }

    internal Lease? Open(string token, string name)
    {
        lock (_gate)
        {
            if (!_files.TryGetValue((token, name), out var entry)) return null;
            byte[]? playlist = null;
            var playlistLength = 0;
            if (name == "index.m3u8" && !_onDemand.Contains(token))
            {
                // Account each rendered playlist lease just like a segment or an unfinished PUT.
                playlist = ArrayPool<byte>.Shared.Rent(entry.Length + 64);
                if (!MakeSpace(playlist.Length)) { ArrayPool<byte>.Shared.Return(playlist); return null; }
                var filtered = FilterPlaylist(token, entry);
                if (filtered is null) { ArrayPool<byte>.Shared.Return(playlist); return null; }
                filtered.CopyTo(playlist, 0);
                playlistLength = filtered.Length;
                _allocated += playlist.Length;
            }
            entry.Readers++;
            entry.LastUse = ++_clock;
            return new Lease(this, entry, playlist, playlistLength);
        }
    }

    internal bool Delete(string token, string name, long? run = null)
    {
        lock (_gate)
        {
            if (run.HasValue && (!_runs.TryGetValue(token, out var current) || current != run.Value)) return false;
            if (_files.TryGetValue((token, name), out var entry) && !Protected(token, entry))
            {
                _files.Remove((token, name));
                Retire(entry);
                Pulse(token);
            }
            return true;
        }
    }

    internal void Remove(string token)
    {
        lock (_gate)
        {
            foreach (var pair in _files.Where(pair => pair.Key.Token == token).ToArray())
            {
                _files.Remove(pair.Key);
                Retire(pair.Value);
            }
            if (_signals.Remove(token, out var signal)) signal.TrySetResult();
            _heads.Remove(token);
            _runs.Remove(token);
            _onDemand.Remove(token);
            _windows.Remove(token);
        }
    }

    private bool Protected(string token, Entry entry)
        => entry.Number < 0 || entry.Readers > 0 || entry.Number > _heads[token] - 3 && entry.Number <= _heads[token];

    private bool MakeSpace(long additional)
    {
        while (_allocated + additional > _budget)
        {
            var candidate = _files.Where(pair => !Protected(pair.Key.Token, pair.Value))
                .OrderBy(pair => _windows.TryGetValue(pair.Key.Token, out var window)
                    && pair.Value.Number >= window.First && pair.Value.Number <= window.Last ? 1 : 0)
                .ThenBy(pair => pair.Value.LastUse).FirstOrDefault();
            if (candidate.Value is null) return false;
            _files.Remove(candidate.Key);
            Retire(candidate.Value);
            Pulse(candidate.Key.Token);
        }
        return true;
    }

    private byte[]? FilterPlaylist(string token, Entry entry)
    {
        var lines = Encoding.UTF8.GetString(CopyBytes(entry.Chunks, entry.Length)).Replace("\r", "").Split('\n');
        var headers = new List<string>();
        var segments = new List<(string Duration, string Name, int Number)>();
        string? duration = null;
        foreach (var line in lines)
        {
            if (line.StartsWith("#EXTINF:", StringComparison.Ordinal)) { duration = line; continue; }
            if (duration is not null)
            {
                var match = Segment.Match(line);
                if (!match.Success) return null;
                segments.Add((duration, line, int.Parse(match.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture)));
                duration = null;
            }
            else if (line.StartsWith('#') && !line.StartsWith("#EXT-X-MEDIA-SEQUENCE:", StringComparison.Ordinal)
                     && !line.StartsWith("#EXT-X-PLAYLIST-TYPE:", StringComparison.Ordinal) && line != "#EXT-X-ENDLIST") headers.Add(line);
        }
        var first = segments.Count;
        for (var i = segments.Count - 1; i >= 0; i--)
        {
            if (!_files.ContainsKey((token, segments[i].Name)) || i < segments.Count - 1 && segments[i + 1].Number != segments[i].Number + 1) break;
            first = i;
        }
        if (first == segments.Count) return null;
        var output = new StringBuilder(string.Join('\n', headers)).Append('\n');
        output.Append("#EXT-X-MEDIA-SEQUENCE:").Append(segments[first].Number).Append('\n');
        foreach (var segment in segments.Skip(first)) output.Append(segment.Duration).Append('\n').Append(segment.Name).Append('\n');
        if (lines.Contains("#EXT-X-ENDLIST")) output.Append("#EXT-X-ENDLIST\n");
        return Encoding.UTF8.GetBytes(output.ToString());
    }

    private static byte[] CopyBytes(List<byte[]> chunks, int length)
    {
        var bytes = new byte[length];
        var offset = 0;
        foreach (var chunk in chunks)
        {
            var count = Math.Min(chunk.Length, length - offset);
            chunk.AsSpan(0, count).CopyTo(bytes.AsSpan(offset));
            offset += count;
        }
        return bytes;
    }

    private void Retire(Entry entry)
    {
        entry.Retired = true;
        if (entry.Readers == 0) foreach (var chunk in entry.Chunks) Return(chunk);
    }

    private void Return(byte[] chunk)
    {
        _allocated -= chunk.Length;
        ArrayPool<byte>.Shared.Return(chunk);
    }

    private static TaskCompletionSource NewSignal() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    private void Pulse(string token)
    {
        var signal = _signals[token];
        _signals[token] = NewSignal();
        signal.TrySetResult();
    }

    internal sealed class Entry(List<byte[]> chunks, int length, int number, long lastUse)
    {
        internal List<byte[]> Chunks = chunks;
        internal int Length = length, Number = number, Readers;
        internal long LastUse = lastUse;
        internal bool Retired;
    }

    internal sealed class Lease : IDisposable
    {
        private MemoryHlsStore? _owner;
        private readonly Entry _entry;
        private readonly byte[]? _playlist;
        private readonly int _playlistLength;
        internal long Length => _playlist is null ? _entry.Length : _playlistLength;
        internal Lease(MemoryHlsStore owner, Entry entry, byte[]? playlist, int playlistLength)
            => (_owner, _entry, _playlist, _playlistLength) = (owner, entry, playlist, playlistLength);

        internal async Task CopyToAsync(Stream target, CancellationToken ct)
        {
            if (_playlist is not null) { await target.WriteAsync(_playlist.AsMemory(0, _playlistLength), ct).ConfigureAwait(false); return; }
            var remaining = _entry.Length;
            foreach (var chunk in _entry.Chunks)
            {
                var count = Math.Min(remaining, chunk.Length);
                await target.WriteAsync(chunk.AsMemory(0, count), ct).ConfigureAwait(false);
                remaining -= count;
            }
        }

        public void Dispose()
        {
            var owner = Interlocked.Exchange(ref _owner, null);
            if (owner is null) return;
            lock (owner._gate)
            {
                _entry.Readers--;
                if (_entry.Retired && _entry.Readers == 0) foreach (var chunk in _entry.Chunks) owner.Return(chunk);
                if (_playlist is not null) owner.Return(_playlist);
            }
        }
    }
}
