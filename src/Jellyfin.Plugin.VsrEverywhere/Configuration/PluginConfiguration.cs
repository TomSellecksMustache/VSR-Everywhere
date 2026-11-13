using MediaBrowser.Model.Plugins;
namespace Jellyfin.Plugin.VsrEverywhere.Configuration;
public sealed class PluginConfiguration : BasePluginConfiguration
{
    public string CacheMode { get; set; } = "memory";
    public int MemoryBudgetMb { get; set; } = 0;
    public int SeekLookaheadSeconds { get; set; } = 90;
    public int SeekCatchupSeconds { get; set; } = 20;
    [System.Xml.Serialization.XmlIgnore]
    public long EffectiveMemoryBudgetMb => VsrMemoryBudget.Calculate(MemoryBudgetMb, VsrMemoryBudget.Available()) / VsrMemoryBudget.MiB;
    // VSR cache policy. Only plugin-owned session folders are cleaned.
    public int CacheMaxGb { get; set; } = 10;
    public int CacheMinFreeGb { get; set; } = 5;
    public int CacheIdleMinutes { get; set; } = 10;
    public bool EnableRollingCache { get; set; } = false;
    public int RollingRewindMinutes { get; set; } = 10;
    // AI processing size is independent of the HEVC delivery size.
    public int ProcessingWidth { get; set; } = 3840;
    public int ProcessingHeight { get; set; } = 2160;
    public int StreamWidth { get; set; } = 3840;
    public int StreamHeight { get; set; } = 2160;

    public bool Enabled { get; set; } = false;
    public bool EnableAllDevices { get; set; } = false;
    public string Backend { get; set; } = "nvidia";
    public bool EnableNeuralRendering { get; set; } = OperatingSystem.IsWindows();
    public string ContentProfile { get; set; } = "auto";
    public bool EnableAutoHdr { get; set; } = false;
    public bool EnableFrameInterpolation { get; set; } = OperatingSystem.IsWindows();
    public string ClientFilter { get; set; } = "Roku";
    // Explicit device selection applies when EnableAllDevices is false.
    public string[] EnabledDeviceIds { get; set; } = Array.Empty<string>();
    public string PythonPath { get; set; } = OperatingSystem.IsWindows() ? "python" : "python3";
    public string FfmpegPath { get; set; } = "ffmpeg";
    public string FfprobePath { get; set; } = "ffprobe";
    public string CacheDirectory { get; set; } = "";
    public int VideoMbps { get; set; } = 20;
    // New playback sessions read these settings; old settings and device approvals are retained.
    public string VsrQuality { get; set; } = "STREAMING_MEDIUM";
    public double VsrStrength { get; set; } = 0.75;
    public string NvencPreset { get; set; } = "p4";
    public string NvencTune { get; set; } = "default";
    public int NvencLookahead { get; set; } = 0;
    public bool NvencSpatialAq { get; set; } = false;
    public bool NvencTemporalAq { get; set; } = false;
    public int NvencCq { get; set; } = 0;

    public string SegmentType { get; set; } = "fmp4";
    public int MaxSessions { get; set; } = 1;
    public int SessionMinutes { get; set; } = 180;
}
