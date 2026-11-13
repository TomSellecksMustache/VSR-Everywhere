using MediaBrowser.Model.Dlna;

namespace Jellyfin.Plugin.VsrEverywhere;

internal static class EnhancementProfiles
{
    internal static int DeliveryMbps(int requested, string? client)
        => Math.Clamp(requested, 4, client?.Contains("roku", StringComparison.OrdinalIgnoreCase) == true ? 40 : 60);

    internal static string Classify(string requested, string? title, IEnumerable<string> labels)
    {
        if (requested is "animation" or "live_action") return requested;
        var metadata = labels.Where(value => !string.IsNullOrWhiteSpace(value)).ToArray();
        var animation = new[] { "animation", "animated", "anime", "动画", "動畫", "アニメ" };
        if (metadata.Any(label => animation.Any(word => label.Contains(word, StringComparison.OrdinalIgnoreCase))) ||
            animation.Any(word => (title ?? "").Contains("[" + word + "]", StringComparison.OrdinalIgnoreCase)))
            return "animation";
        return metadata.Length > 0 ? "live_action" : "unknown";
    }

    internal static bool SupportsHdr10(DeviceProfile? device)
    {
        var profiles = (device?.CodecProfiles ?? []).Where(profile => profile.Type == CodecType.Video &&
            (profile.Codec ?? "").Split(',').Any(codec => codec.Trim().Equals("hevc", StringComparison.OrdinalIgnoreCase))).ToArray();
        var ranges = profiles.SelectMany(profile => profile.Conditions ?? [])
            .Where(condition => condition.Property == ProfileConditionValue.VideoRangeType).ToArray();
        // Unknown capabilities keep SDR. A contradictory HDR prohibition wins.
        if (ranges.Any(condition => condition.Condition == ProfileConditionType.NotEquals && HasHdr10(condition.Value))) return false;
        return ranges.Any(condition => condition.Condition is ProfileConditionType.Equals or ProfileConditionType.EqualsAny && HasHdr10(condition.Value)) &&
            ranges.All(condition => condition.Condition is not (ProfileConditionType.Equals or ProfileConditionType.EqualsAny) || HasHdr10(condition.Value));
    }

    private static bool HasHdr10(string? value)
        => (value ?? "").Split('|', ',', ';').Any(part => part.Trim().Equals("HDR10", StringComparison.OrdinalIgnoreCase));
}
