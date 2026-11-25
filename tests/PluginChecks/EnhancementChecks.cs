using MediaBrowser.Model.Dlna;
using Jellyfin.Plugin.VsrEverywhere;

internal static class EnhancementChecks
{
    internal static void Run()
    {
        static void Check(bool value) { if (!value) throw new Exception("Enhancement profile check failed"); }
        Check(!EnhancementProfiles.SupportsHdr10(null));
        Check(EnhancementProfiles.DeliveryMbps(60, "Jellyfin Roku") == 40);
        Check(EnhancementProfiles.DeliveryMbps(18, "Roku") == 18);
        Check(EnhancementProfiles.DeliveryMbps(60, "Jellyfin Web") == 60);
        Check(EnhancementProfiles.DeliveryMbps(1, null) == 4);
        var range = new ProfileCondition { Property = ProfileConditionValue.VideoRangeType, Condition = ProfileConditionType.EqualsAny, Value = "SDR|HDR10" };
        var profile = new DeviceProfile { CodecProfiles = [new CodecProfile { Type = CodecType.Video, Codec = "hevc", Conditions = [range] }] };
        Check(EnhancementProfiles.SupportsHdr10(profile));
        range.Value = "SDR"; Check(!EnhancementProfiles.SupportsHdr10(profile));
        range.Value = "HDR10"; range.Condition = ProfileConditionType.NotEquals;
        Check(!EnhancementProfiles.SupportsHdr10(profile));
        range.Condition = ProfileConditionType.Equals;
        profile.CodecProfiles[0].Codec = "h264";
        Check(!EnhancementProfiles.SupportsHdr10(profile));

        static CodecProfile Video(string codec, string ranges, ProfileConditionType condition = ProfileConditionType.EqualsAny)
            => new() { Type = CodecType.Video, Codec = codec, Conditions = [new ProfileCondition {
                Property = ProfileConditionValue.VideoRangeType, Condition = condition, Value = ranges }] };
        // Captured from Jellyfin Roku 3.2.4: no HEVC profile; HDR10 is declared under AV1 and VP9.
        var roku = new DeviceProfile { CodecProfiles = [
            Video("h264", "SDR|DOVIWithSDR"),
            Video("av1", "SDR|DOVIWithSDR|HDR10|DOVIWithHDR10|HLG|DOVIWithHLG"),
            Video("vp9", "SDR|DOVIWithSDR|HDR10|DOVIWithHDR10|HLG|DOVIWithHLG")] };
        Check(EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Roku"));
        Check(!EnhancementProfiles.SupportsHdr10(roku));
        Check(!EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Web"));
        Check(!EnhancementProfiles.SupportsHdr10(null, "Jellyfin Roku"));
        roku.CodecProfiles = [Video("vp9", "SDR")];
        Check(!EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Roku"));
        roku.CodecProfiles = [Video(" AV1, VP9 ", " HDR10 ")];
        Check(EnhancementProfiles.SupportsHdr10(roku, "JELLYFIN ROKU"));
        roku.CodecProfiles = [Video("vp9", "HLG|DOVIWithHDR10|HDR10Plus")];
        Check(!EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Roku"));
        roku.CodecProfiles = [Video("av1", "HDR10", ProfileConditionType.NotEquals)];
        Check(!EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Roku"));
        roku.CodecProfiles = [Video("hevc", "SDR"), Video("vp9", "SDR|HDR10")];
        Check(!EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Roku"));
        roku.CodecProfiles = [Video("hevc", "SDR|HDR10"), Video("h265", "HDR10", ProfileConditionType.NotEquals)];
        Check(!EnhancementProfiles.SupportsHdr10(roku, "Jellyfin Roku"));
        roku.CodecProfiles = [Video("h265", "HDR10")];
        Check(EnhancementProfiles.SupportsHdr10(roku));
        Console.WriteLine("PASS: delivery limits, explicit HEVC HDR10 rules, and observed Roku display HDR10 fallback.");
    }
}
