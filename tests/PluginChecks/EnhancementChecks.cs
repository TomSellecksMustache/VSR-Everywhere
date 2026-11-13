using MediaBrowser.Model.Dlna;
using Jellyfin.Plugin.VsrEverywhere;

internal static class EnhancementChecks
{
    internal static void Run()
    {
        static void Check(bool value) { if (!value) throw new Exception("Enhancement profile check failed"); }
        Check(EnhancementProfiles.Classify("auto", "Avatar", ["Animation", "Fantasy"]) == "animation");
        Check(EnhancementProfiles.Classify("auto", "Avatar", ["Science Fiction"]) == "live_action");
        Check(EnhancementProfiles.Classify("auto", "Unknown title", []) == "unknown");
        Check(EnhancementProfiles.Classify("animation", "Any title", ["Drama"]) == "animation");
        Check(EnhancementProfiles.Classify("live_action", "Any title", ["Anime"]) == "live_action");
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
        Console.WriteLine("PASS: content profiles, overrides, unknown titles, and explicit HDR10 capability checks.");
    }
}
