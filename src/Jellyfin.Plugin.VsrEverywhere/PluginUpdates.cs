using System.IO.Compression;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using MediaBrowser.Common;
using MediaBrowser.Common.Api;
using MediaBrowser.Common.Updates;
using MediaBrowser.Model.Updates;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VsrEverywhere;

public sealed record PluginUpdateStatus(string CurrentVersion, string? LatestVersion, bool UpdateAvailable,
    bool CanInstall, bool RestartRequired, string Message, string ReleaseUrl);

public sealed class PluginUpdates(IHttpClientFactory clients, IInstallationManager installations, IApplicationHost host)
{
    internal const string Repository = "https://github.com/TomSellecksMustache/VSR-Everywhere";
    internal const string LatestApi = "https://api.github.com/repos/TomSellecksMustache/VSR-Everywhere/releases/latest";
    private readonly SemaphoreSlim _gate = new(1, 1);
    private string? _pendingVersion;
    private static Version Current => typeof(Plugin).Assembly.GetName().Version!;

    public async Task<PluginUpdateStatus> Run(bool install, CancellationToken cancellationToken)
    {
        await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (_pendingVersion is not null) return Pending(_pendingVersion);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(45));
            var downloadToken = timeout.Token;
            using var client = clients.CreateClient();
            client.Timeout = TimeSpan.FromSeconds(45);
            client.DefaultRequestHeaders.UserAgent.ParseAdd("VSR-Everywhere/" + Current);
            client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
            var bytes = await Download(client, LatestApi, 1024 * 1024, downloadToken).ConfigureAwait(false);
            var release = JsonSerializer.Deserialize<Release>(bytes, new JsonSerializerOptions(JsonSerializerDefaults.Web));
            if (release is null || release.Draft != false || release.Prerelease != false ||
                !Regex.IsMatch(release.TagName ?? "", @"\Av[0-9]+\.[0-9]+\.[0-9]+\z") ||
                !Version.TryParse(release.TagName![1..], out var parsed))
                throw new InvalidDataException("GitHub did not return a stable release with a valid version.");
            var version = new Version(parsed.Major, parsed.Minor, parsed.Build, 0);
            var label = parsed.ToString(3);
            var releaseUrl = Repository + "/releases/tag/" + release.TagName;
            PluginUpdateStatus Status(bool available, bool canInstall, string message)
                => new(Current.ToString(3), label, available, canInstall, false, message, releaseUrl);
            if (version <= Current)
                return Status(false, false, version == Current ? "VSR Everywhere is up to date (" + label + ")."
                    : "Your installed version is newer than the latest stable release (" + label + ").");
            var name = "VSR-Everywhere-" + label + "-plugin.zip";
            var assets = (release.Assets ?? []).Where(asset => asset.Name == name).ToArray();
            var url = Repository + "/releases/download/" + release.TagName + "/" + name;
            if (assets.Length != 1 || assets[0].Url != url || assets[0].Size is <= 0 or > 16 * 1024 * 1024 ||
                !Regex.IsMatch(assets[0].Digest ?? "", @"\Asha256:[0-9a-fA-F]{64}\z"))
                return Status(true, false, "A new release is available. Use its installer; a verified plugin update package is unavailable.");
            bytes = await Download(client, url, 16 * 1024 * 1024, downloadToken).ConfigureAwait(false);
            var sha = Convert.ToHexString(SHA256.HashData(bytes));
            if (bytes.LongLength != assets[0].Size || !sha.Equals(assets[0].Digest![7..], StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("The update download did not match GitHub's SHA-256 checksum. Nothing was installed.");
            using var zip = new ZipArchive(new MemoryStream(bytes), ZipArchiveMode.Read);
            var meta = JsonSerializer.Deserialize<JsonElement>(ReadEntry(zip, "meta.json"));
            if (meta.ValueKind != JsonValueKind.Object ||
                !meta.TryGetProperty("guid", out var guid) || guid.ValueKind != JsonValueKind.String || !Guid.TryParse(guid.GetString(), out var id) || id != Plugin.PluginId ||
                !meta.TryGetProperty("version", out var number) || number.ValueKind != JsonValueKind.String || !Version.TryParse(number.GetString(), out var packaged) || packaged != version ||
                !meta.TryGetProperty("targetAbi", out var abi) || abi.ValueKind != JsonValueKind.String || !Version.TryParse(abi.GetString(), out var target))
                throw new InvalidDataException("The downloaded package has an unexpected plugin identity or version.");
            if (host.ApplicationVersion < target)
                return Status(true, false, "This update requires Jellyfin " + target + " or newer.");
            if (!RuntimeMatches(zip))
                return Status(true, false, "A new release is available. Run its full installer to update the video runtime safely.");
            if (!install) return Status(true, true, "Version " + label + " is available. Installing preserves settings; restart Jellyfin afterward.");
            // Jellyfin writes a separate version folder; the running DLL and worker stay together until restart.
            var checksum = Convert.ToHexString(MD5.HashData(bytes));
            await installations.InstallPackage(new InstallationInfo
            {
                Id = Plugin.PluginId, Name = "VSR Everywhere", Version = version, SourceUrl = url, Checksum = checksum,
                PackageInfo = new PackageInfo
                {
                    Id = Plugin.PluginId, Name = "VSR Everywhere", Owner = "TomSellecksMustache", Category = "General",
                    Versions = [new VersionInfo { Version = version.ToString(), SourceUrl = url, Checksum = checksum, TargetAbi = target.ToString() }]
                }
            }, CancellationToken.None).ConfigureAwait(false);
            _pendingVersion = label;
            return Pending(label);
        }
        finally { _gate.Release(); }
    }

    private static PluginUpdateStatus Pending(string version)
        => new(Current.ToString(3), version, false, false, true,
            "Version " + version + " is installed. Restart Jellyfin when you are ready to activate it.", Repository + "/releases/tag/v" + version);

    internal static bool RuntimeMatches(ZipArchive zip)
    {
        var names = OperatingSystem.IsWindows() ? new[] { "windows.json" } : new[] { "linux.json", "linux-requirements.txt" };
        foreach (var name in names)
        {
            if (zip.GetEntry("update/" + name) is null) return false;
            using var stream = typeof(Plugin).Assembly.GetManifestResourceStream("VsrEverywhere.Update." + name)!;
            using var reader = new StreamReader(stream);
            var installed = reader.ReadToEnd();
            var candidate = ReadEntry(zip, "update/" + name);
            if (name.EndsWith(".json", StringComparison.Ordinal))
            {
                var left = JsonNode.Parse(installed) as JsonObject;
                var right = JsonNode.Parse(candidate) as JsonObject;
                if (left is null || right is null) return false;
                left.Remove("version"); right.Remove("version");
                if (!JsonNode.DeepEquals(left, right)) return false;
            }
            else if (installed.Replace("\r\n", "\n") != candidate.Replace("\r\n", "\n")) return false;
        }
        return true;
    }

    private static string ReadEntry(ZipArchive zip, string name)
    {
        var entries = zip.Entries.Where(entry => entry.FullName == name).ToArray();
        if (entries.Length != 1 || entries[0].Length > 1024 * 1024)
            throw new InvalidDataException("Missing, duplicate, or oversized update metadata.");
        using var reader = new StreamReader(entries[0].Open());
        return reader.ReadToEnd();
    }

    private static async Task<byte[]> Download(HttpClient client, string url, int limit, CancellationToken token)
    {
        using var response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentLength > limit) throw new InvalidDataException("Update download is larger than expected.");
        await using var source = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
        using var output = new MemoryStream();
        var buffer = new byte[65536];
        int count;
        while ((count = await source.ReadAsync(buffer, token).ConfigureAwait(false)) > 0)
        {
            if (output.Length + count > limit) throw new InvalidDataException("Update download is larger than expected.");
            output.Write(buffer, 0, count);
        }
        return output.ToArray();
    }

    private sealed record Release([property: JsonPropertyName("tag_name")] string? TagName, bool? Draft, bool? Prerelease, Asset[]? Assets);
    private sealed record Asset(string? Name, long Size, string? Digest, [property: JsonPropertyName("browser_download_url")] string? Url);
}

[ApiController]
[Route("VsrEverywhere/Updates")]
[Authorize(Policy = Policies.RequiresElevation)]
public sealed class PluginUpdatesController(PluginUpdates updates, ILogger<PluginUpdatesController> logger) : ControllerBase
{
    [HttpGet]
    public Task<IActionResult> Check(CancellationToken token) => Run(false, token);

    [HttpPost]
    public Task<IActionResult> Install(CancellationToken token) => Run(true, token);

    private async Task<IActionResult> Run(bool install, CancellationToken token)
    {
        try { return new JsonResult(await updates.Run(install, token).ConfigureAwait(false), new JsonSerializerOptions(JsonSerializerDefaults.Web)); }
        catch (Exception ex) when (ex is HttpRequestException or IOException or InvalidDataException or JsonException or UnauthorizedAccessException ||
                                  ex is OperationCanceledException && !token.IsCancellationRequested)
        {
            logger.LogWarning(ex, "VSR Everywhere update {Operation} failed", install ? "installation" : "check");
            var message = ex is InvalidDataException ? ex.Message
                : ex is UnauthorizedAccessException ? "Jellyfin cannot write its plugin folder. Use the full installer to update."
                : "Update failed. Check internet access, free disk space, and the Jellyfin log, then retry.";
            return StatusCode(503, new { message });
        }
    }
}
