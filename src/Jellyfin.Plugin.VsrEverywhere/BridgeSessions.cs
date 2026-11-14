using System.Collections.Concurrent;
using System.Diagnostics;
using System.Reflection;
using System.Security.Cryptography;
using Jellyfin.Plugin.VsrEverywhere.Configuration;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

public sealed class BridgeSessions : IDisposable
{
    private readonly ConcurrentDictionary<string, BridgeSession> _active = new(StringComparer.Ordinal);
    private readonly ILogger<BridgeSessions> _logger;
    private readonly System.Threading.Timer _sweepTimer;
    private readonly ConcurrentDictionary<string, byte> _cacheRoots = new(StringComparer.OrdinalIgnoreCase);
    private readonly MemoryHlsStore _memory = new();
    private int _sweeping;
    public BridgeSessions(ILogger<BridgeSessions> logger)
    {
        _logger = logger;
        _sweepTimer = new System.Threading.Timer(_ => Sweep(), null, TimeSpan.FromMinutes(1), TimeSpan.FromMinutes(1));
    }
    private void Sweep()
    {
        if (Interlocked.Exchange(ref _sweeping, 1) != 0) return;
        try
        {
            var now = DateTimeOffset.UtcNow;
            foreach (var (token, session) in _active)
            {
                var idle = TimeSpan.FromMinutes(Math.Clamp(session.IdleMinutes, 2, 240));
                if (session.Expires > now && now - session.LastAccess < idle) continue;
                if (_active.TryRemove(token, out var removed))
                {
                    removed.Dispose();
                    VsrCacheSafety.RemoveOwned(removed.Root, _logger);
                }
            }
            foreach (var root in _cacheRoots.Keys)
            {
                if (!Directory.Exists(root)) continue;
                foreach (var dir in Directory.EnumerateDirectories(root))
                {
                    if (!VsrCacheSafety.Owned(dir)) continue;
                    var token = Path.GetFileName(dir);
                    if (_active.ContainsKey(token)) continue;
                    try
                    {
                        var age = now - new DateTimeOffset(Directory.GetLastWriteTimeUtc(dir), TimeSpan.Zero);
                        if (age > TimeSpan.FromMinutes(30)) VsrCacheSafety.RemoveOwned(dir, _logger);
                    }
                    catch (IOException) { /* file is being replaced */ }
                }
            }
        }
        catch (Exception ex) { _logger.LogWarning(ex, "VSR Everywhere cache sweep could not complete"); }
        finally { Interlocked.Exchange(ref _sweeping, 0); }
    }

    public BridgeSession? Get(string token)
    {
        var session = Find(token);
        if (session is null) return null;
        session.LastAccess = DateTimeOffset.UtcNow;
        return session;
    }
    internal BridgeSession? Find(string token)
        => _active.TryGetValue(token, out var session) && session.Expires > DateTimeOffset.UtcNow && !session.IsDisposed ? session : null;
    public void Dispose()
    {
        _sweepTimer.Dispose();
        foreach (var session in _active.Values) session.Dispose();
        _active.Clear();
    }
    public string? Register(string source, PluginConfiguration cfg, string contentProfile = "unknown", bool hdr = false, int? maxVideoMbps = null)
    {
        foreach (var (key, old) in _active)
        {
            if (old.Expires < DateTimeOffset.UtcNow)
            {
                if (_active.TryRemove(key, out var expired))
                {
                    expired.Dispose();
                    VsrCacheSafety.RemoveOwned(expired.Root, _logger);
                }
            }
        }
        // A new playback supersedes the oldest session if the configured limit is reached.
        // With a one-stream GPU budget, switching films closes the previous transcode.
        while (_active.Count >= Math.Clamp(cfg.MaxSessions, 1, 16))
        {
            var oldest = _active.OrderBy(x => x.Value.LastAccess).FirstOrDefault();
            if (oldest.Value is null) break;
            if (_active.TryRemove(oldest.Key, out var evicted))
            {
                evicted.Dispose();
                VsrCacheSafety.RemoveOwned(evicted.Root, _logger);
            }
        }
        var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
        var memoryBudget = cfg.CacheMode == "disk" ? 0 : VsrMemoryBudget.Calculate(cfg.MemoryBudgetMb, VsrMemoryBudget.Available());
        if (memoryBudget > 0 && _memory.Register(token, memoryBudget, onDemand: true))
        {
            var memorySession = new BridgeSession(token, source, "", cfg, _logger, _memory, contentProfile, hdr, maxVideoMbps);
            if (_active.TryAdd(token, memorySession))
            {
                _logger.LogInformation("VSR Everywhere cache mode=memory global budget={BudgetMb} MB", _memory.BudgetBytes / VsrMemoryBudget.MiB);
                return token;
            }
            memorySession.Dispose();
            return null;
        }
        if (cfg.CacheMode != "disk") _logger.LogWarning("VSR Everywhere cache falling back to disk: less than 1 GB RAM available or the global memory budget is occupied");
        // Preflight before rewriting PlaybackInfo; leave Jellyfin normal playback if cache is unsuitable.
        var cacheRoot = string.IsNullOrWhiteSpace(cfg.CacheDirectory)
            ? Path.Combine(Path.GetTempPath(), "vsr-everywhere") : cfg.CacheDirectory;
        try
        {
            Directory.CreateDirectory(cacheRoot);
            _cacheRoots.TryAdd(Path.GetFullPath(cacheRoot), 0);
            Sweep();
            var rootFull = Path.GetFullPath(cacheRoot);
            var driveRoot = Path.GetPathRoot(rootFull)!;
            var drive = new DriveInfo(driveRoot);
            var reserve = (long)Math.Clamp(cfg.CacheMinFreeGb, 2, 200) * 1024 * 1024 * 1024;
            var used = Directory.EnumerateDirectories(rootFull).Sum(VsrCacheSafety.SizeOf);
            var budget = (long)Math.Clamp(cfg.CacheMaxGb, 2, 500) * 1024 * 1024 * 1024;
            if (drive.AvailableFreeSpace < reserve || used >= budget)
            {
                _logger.LogWarning("VSR Everywhere skipped: insufficient cache space or cache budget reached");
                return null;
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException)
        {
            _logger.LogWarning(ex, "VSR Everywhere skipped: cache directory is unavailable");
            return null;
        }
        // Cache root was already validated above.
        var root = Path.Combine(Path.GetFullPath(cacheRoot), token);
        // All worker outputs are generated in the token-specific cache, never beside the media file.
        var session = new BridgeSession(token, source, root, cfg, _logger, null, contentProfile, hdr, maxVideoMbps);
        _logger.LogInformation("VSR Everywhere cache mode=disk");
        return _active.TryAdd(token, session) ? token : null;
    }
}

public sealed partial class BridgeSession : IDisposable
{
    private readonly PluginConfiguration _cfg;
    private readonly string _contentProfile;
    private readonly bool _hdr;
    private readonly bool _neuralRendering;
    private readonly int _videoMbps;
    private readonly ILogger _logger;
    private readonly SemaphoreSlim _startLock = new(1, 1);
    private Process? _worker;
    private readonly MemoryHlsStore? _memory;
    private readonly byte[] _ingestKey = RandomNumberGenerator.GetBytes(32);
    private int _disposed;
    public string Token { get; }
    public string Source { get; }
    public string Root { get; }
    public DateTimeOffset Expires { get; }
    public DateTimeOffset LastAccess { get; set; } = DateTimeOffset.UtcNow;
    public bool Started { get; private set; }
    public bool IsMemory => _memory is not null;
    internal bool IsDisposed => Volatile.Read(ref _disposed) != 0;
    internal Task MemoryChanged => _memory?.Changed(Token) ?? Task.CompletedTask;
    internal MemoryHlsStore.Lease? OpenMemory(string name) => _memory?.Open(Token, name);
    internal bool AcceptsKey(string key)
        => key.Length == 64 && key.All(Uri.IsHexDigit) && CryptographicOperations.FixedTimeEquals(Convert.FromHexString(key), _ingestKey);
    internal string IngestKey => Convert.ToHexString(_ingestKey).ToLowerInvariant();
    public int IdleMinutes => Math.Clamp(_cfg.CacheIdleMinutes, 2, 240);
    public BridgeSession(string token, string source, string root, PluginConfiguration cfg, ILogger logger)
        : this(token, source, root, cfg, logger, null) { }
    internal BridgeSession(string token, string source, string root, PluginConfiguration cfg, ILogger logger, MemoryHlsStore? memory,
        string contentProfile = "unknown", bool hdr = false, int? maxVideoMbps = null)
    {
        Token = token;
        Source = source;
        Root = root;
        _cfg = cfg;
        _contentProfile = contentProfile is "animation" or "live_action" ? contentProfile : "unknown";
        _neuralRendering = OperatingSystem.IsWindows() && EnhancementProfiles.UseNeuralRendering(cfg.EnableNeuralRendering, cfg.ContentProfile, _contentProfile);
        _hdr = hdr && cfg.EnableAutoHdr;
        _videoMbps = Math.Min(Math.Clamp(cfg.VideoMbps, 4, 60), Math.Clamp(maxVideoMbps ?? 60, 4, 60));
        _logger = logger;
        _memory = memory;
        Expires = DateTimeOffset.UtcNow.AddMinutes(Math.Clamp(cfg.SessionMinutes, 10, 1440));
    }
    public async Task<bool> EnsureStarted(CancellationToken ct, string? memoryOutput = null)
    {
        if (IsDisposed) return false;
        if (Started && (!IsMemory || !HasExited)) return true;
        await _startLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (IsDisposed) return false;
            if (Started && (!IsMemory || !HasExited)) return true;
            if (memoryOutput is not null) _memoryOutput = memoryOutput;
            if (IsMemory && _memoryOutput is null) return false;
            if (Started)
            {
                if (!AllowRestart()) return false;
                _worker?.Dispose();
                _worker = null;
                Started = false;
                _runActive = false;
                if (!_ready.Task.IsCompletedSuccessfully) _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
            }
            try
            {
                if (!IsMemory)
                {
                    Directory.CreateDirectory(Root);
                    File.WriteAllText(Path.Combine(Root, VsrCacheSafety.Marker), "VSREverywhere-owned-v1.0.0");
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                _logger.LogError(ex, "VSR Everywhere cannot create its cache session; choose a writable drive with free space");
                return false;
            }
            var pluginDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)!;
            var workerPath = Path.Combine(pluginDir, "worker", "vsr_everywhere.py");
            if (!File.Exists(workerPath))
            {
                _logger.LogError("VSR Everywhere worker missing: {Path}", workerPath);
                return false;
            }
            var psi = new ProcessStartInfo { FileName = _cfg.PythonPath, UseShellExecute = false,
                CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true, RedirectStandardInput = IsMemory,
                WorkingDirectory = pluginDir };
            foreach (var a in new [] { workerPath, Source, "--output", IsMemory ? _memoryOutput! : Path.Combine(Root, "index.m3u8"),
                "--hls", "--seconds", "0", "--backend", "nvidia", "--ffmpeg", _cfg.FfmpegPath,
                "--ffprobe", _cfg.FfprobePath, "--video-mbps", _videoMbps.ToString(),
                "--segment-type", _cfg.SegmentType == "mpegts" ? "mpegts" : "fmp4" })
                psi.ArgumentList.Add(a);
            if (IsMemory)
            {
                psi.ArgumentList.Add("--memory-output");
                psi.ArgumentList.Add("--serve");
                psi.Environment["VSR_INGEST_KEY"] = IngestKey;
            }
            // Supported quality options validated again in the Python worker.
            var qualityOptions = new[] { "LOW", "MEDIUM", "HIGH", "ULTRA",
                "HIGHBITRATE_MEDIUM", "HIGHBITRATE_HIGH", "HIGHBITRATE_ULTRA",
                "STREAMING_MEDIUM", "STREAMING_ULTRA" };
            var vsrQuality = qualityOptions.Contains(_cfg.VsrQuality, StringComparer.Ordinal)
                ? _cfg.VsrQuality : "STREAMING_MEDIUM";
            var vsrStrength = double.IsFinite(_cfg.VsrStrength)
                ? Math.Clamp(_cfg.VsrStrength, 0.0, 1.0) : 0.75;
            var preset = new[] { "p4", "p5", "p6", "p7" }.Contains(_cfg.NvencPreset, StringComparer.Ordinal)
                ? _cfg.NvencPreset : "p4";
            var tune = _cfg.NvencTune == "hq" ? "hq" : "default";
            var lookahead = Math.Clamp(_cfg.NvencLookahead, 0, 32);
            var cq = Math.Clamp(_cfg.NvencCq, 0, 35);
            var rewindMinutes = IsMemory
                ? Math.Min(Math.Clamp(_cfg.RollingRewindMinutes, 2, 60), Math.Max(1, (int)(_memory!.BudgetBytes * 8 / (_videoMbps * 1_000_000L) / 60)))
                : _cfg.EnableRollingCache ? Math.Clamp(_cfg.RollingRewindMinutes, 2, 60) : 0;
            foreach (var a in new[] {
                "--quality", vsrQuality,
                "--strength", vsrStrength.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture),
                "--nvenc-preset", preset,
                "--nvenc-tune", tune,
                "--nvenc-lookahead", lookahead.ToString(System.Globalization.CultureInfo.InvariantCulture),
                "--nvenc-cq", cq.ToString(System.Globalization.CultureInfo.InvariantCulture)
            }) psi.ArgumentList.Add(a);
            foreach (var a in new[] {
                "--processing-width", Math.Clamp(_cfg.ProcessingWidth, 1280, 7680).ToString(),
                "--processing-height", Math.Clamp(_cfg.ProcessingHeight, 720, 4320).ToString(),
                "--stream-width", Math.Clamp(_cfg.StreamWidth, 640, 7680).ToString(),
                "--stream-height", Math.Clamp(_cfg.StreamHeight, 360, 4320).ToString(),
                "--rolling-rewind-minutes", rewindMinutes.ToString()
            }) psi.ArgumentList.Add(a);
            if (_cfg.NvencSpatialAq) psi.ArgumentList.Add("--nvenc-spatial-aq");
            if (!_cfg.EnableVsr) psi.ArgumentList.Add("--no-vsr");
            if (_cfg.NvencTemporalAq) psi.ArgumentList.Add("--nvenc-temporal-aq");
            if (_neuralRendering) psi.ArgumentList.Add("--neural-rendering");
            psi.ArgumentList.Add("--content-profile");
            psi.ArgumentList.Add(_contentProfile);
            if (_cfg.EnableFrameInterpolation) psi.ArgumentList.Add("--frame-interpolation");
            if (_hdr) psi.ArgumentList.Add("--auto-hdr");
            _logger.LogInformation("VSR Everywhere profile quality={Quality} strength={Strength} preset={Preset} tune={Tune} " +
                "lookahead={Lookahead} spatialAQ={SpatialAq} temporalAQ={TemporalAq} cq={CQ} bitrate={Mbps} nr={NeuralRendering} content={ContentProfile}",
                vsrQuality, vsrStrength, preset, tune, lookahead, _cfg.NvencSpatialAq,
                _cfg.NvencTemporalAq, cq, _videoMbps, _neuralRendering, _contentProfile);

            try
            {
                _worker = Process.Start(psi);
                if (_worker is null) return false;
                var worker = _worker;
                if (IsMemory)
                {
                    _worker.Exited += (_, _) => _memory!.Notify(Token);
                    _worker.EnableRaisingEvents = true;
                }
                // Asynchronous draining prevents FFmpeg progress output deadlocking the worker.
                _ = Task.Run(async () => {
                    try { while (await worker.StandardError.ReadLineAsync() is { } line)
                        _logger.LogDebug("VSR Everywhere: {Log}", line); }
                    catch (Exception) { /* worker shutdown */ }
                });
                _ = Task.Run(() => ReadWorkerOutput(worker));
                Started = true;
                return true;
            }
            catch (Exception ex) { _logger.LogError(ex, "Unable to launch VSR Everywhere worker"); return false; }
        }
        finally { _startLock.Release(); }
    }
    public bool HasExited
    {
        get
        {
            try { return IsDisposed || (_worker?.HasExited ?? false); }
            catch (InvalidOperationException) { return true; }
        }
    }
    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _memory?.Remove(Token);
        _startLock.Wait();
        try
        {
            try { if (_worker is { HasExited: false }) _worker.Kill(entireProcessTree: true); }
            catch (Exception) { /* best effort */ }
            _worker?.Dispose();
        }
        finally { _startLock.Release(); }
        // Disk files are removed separately using the plugin-owned directory marker.
    }
}

// Deliberate type-metadata build marker for verified packaging.
internal sealed class VsrEverywhereQualityControls { }

// All cache deletions are constrained to random session directories containing our marker.
internal static class VsrCacheSafety
{
    internal const string Marker = ".vsr-everywhere-session";
    internal static bool Owned(string root)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root)) return false;
        var name = Path.GetFileName(Path.TrimEndingDirectorySeparator(root));
        return name.Length == 48 && name.All(Uri.IsHexDigit) && File.Exists(Path.Combine(root, Marker));
    }
    internal static void RemoveOwned(string root, ILogger log)
    {
        if (!Owned(root)) return;
        try { Directory.Delete(root, true); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        { log.LogWarning("VSR Everywhere cache cleanup deferred: {Reason}", ex.GetType().Name); }
    }
    internal static long SizeOf(string root)
    {
        if (!Owned(root)) return 0;
        try { return Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
            .Sum(p => { try { return new FileInfo(p).Length; } catch (IOException) { return 0L; } }); }
        catch (IOException) { return 0; }
    }
}
