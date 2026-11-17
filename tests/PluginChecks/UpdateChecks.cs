using System.IO.Compression;
using System.Net;
using System.Net.Http.Json;
using System.Reflection;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using MediaBrowser.Common;
using MediaBrowser.Common.Api;
using MediaBrowser.Common.Updates;
using MediaBrowser.Model.Updates;
using Jellyfin.Plugin.VsrEverywhere;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

internal static class UpdateChecks
{
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }

    internal static async Task Run()
    {
        var current = typeof(Plugin).Assembly.GetName().Version!;
        var next = new Version(current.Major, current.Minor + 1, 0, 0);
        var fixture = new UpdateFixture(next);
        var updater = fixture.Updater();
        var result = await updater.Run(false, CancellationToken.None);
        Check(result.UpdateAvailable && result.CanInstall && !result.RestartRequired, "Stable update must be offered");
        Check(fixture.InstallCount == 0, "Checking must not install");
        result = await updater.Run(true, CancellationToken.None);
        Check(result.RestartRequired && !result.CanInstall && fixture.InstallCount == 1, "Install must require an explicit restart");
        Check(fixture.Installed!.Id == Plugin.PluginId && fixture.Installed.Version == next && fixture.Installed.Name == "VSR Everywhere", "Wrong native package identity");
        Check(fixture.Installed.Checksum == Convert.ToHexString(MD5.HashData(fixture.Package)), "Native updater must verify the inspected bytes");
        var requests = fixture.RequestCount;
        Check((await updater.Run(true, CancellationToken.None)).RestartRequired && fixture.InstallCount == 1 && fixture.RequestCount == requests,
            "Repeated clicks and page reloads must retain pending update state");

        foreach (var version in new[] { current, new Version(0, 0, 1, 0) })
        {
            fixture = new UpdateFixture(version);
            result = await fixture.Updater().Run(true, CancellationToken.None);
            Check(!result.UpdateAvailable && fixture.RequestCount == 1 && fixture.InstallCount == 0, "Current/older release must not download or install");
        }
        foreach (var change in new Action<UpdateFixture>[]
        {
            f => f.Draft = true, f => f.Prerelease = true, f => f.CorruptHash = true,
            f => f.Package = UpdateFixture.Zip(next, identity: Guid.NewGuid()),
            f => f.Package = UpdateFixture.Zip(new Version(9, 0, 0, 0)),
            f => f.Package = UpdateFixture.Zip(next, metadata: "{\"guid\":123}"),
            f => f.Package = UpdateFixture.Zip(next, metadata: "not JSON")
        })
        {
            fixture = new UpdateFixture(next); change(fixture);
            try { await fixture.Updater().Run(true, CancellationToken.None); throw new Exception("Unsafe package was accepted"); }
            catch (Exception ex) when (ex is InvalidDataException or JsonException) { }
            Check(fixture.InstallCount == 0, "Invalid release reached native installation");
        }
        foreach (var change in new Action<UpdateFixture>[]
        {
            f => f.Url = "https://example.com/update.zip", f => f.Digest = null,
            f => f.Package = UpdateFixture.Zip(next, runtimeChange: true),
            f => f.Package = UpdateFixture.Zip(next, includeRuntime: false),
            f => f.Package = UpdateFixture.Zip(next, target: "99.0.0.0"),
            f => f.DeclaredSize = 17 * 1024 * 1024
        })
        {
            fixture = new UpdateFixture(next); change(fixture);
            result = await fixture.Updater().Run(true, CancellationToken.None);
            Check(result.UpdateAvailable && !result.CanInstall && !result.RestartRequired && fixture.InstallCount == 0,
                "Unverified, incompatible or runtime-changing update must use full installer");
        }
        fixture = new UpdateFixture(next) { FailInstall = true };
        updater = fixture.Updater();
        try { await updater.Run(true, CancellationToken.None); throw new Exception("Failed install was accepted"); }
        catch (IOException) { }
        fixture.FailInstall = false;
        Check((await updater.Run(true, CancellationToken.None)).RestartRequired && fixture.InstallCount == 2, "Failed install must allow retry");
        using (var cancelled = new CancellationTokenSource())
        {
            cancelled.Cancel();
            fixture = new UpdateFixture(next);
            try { await fixture.Updater().Run(true, cancelled.Token); throw new Exception("Cancelled request was accepted"); }
            catch (OperationCanceledException) { }
            Check(fixture.InstallCount == 0 && fixture.RequestCount == 0, "Cancellation before installation must not mutate anything");
        }
        await CheckAuthorization(new UpdateFixture(next));
        Console.WriteLine("PASS: stable-only updates, SHA-256/identity checks, runtime compatibility, no downgrades, native staging/restart, retries and administrator-only HTTP routes.");
    }

    private static async Task CheckAuthorization(UpdateFixture fixture)
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options => options.Listen(IPAddress.Loopback, 0));
        builder.Services.AddSingleton(fixture.Updater());
        builder.Services.AddControllers().AddApplicationPart(typeof(PluginUpdatesController).Assembly);
        builder.Services.AddAuthentication("test").AddScheme<AuthenticationSchemeOptions, UpdateAuthHandler>("test", _ => { });
        builder.Services.AddAuthorization(options => options.AddPolicy(Policies.RequiresElevation,
            policy => policy.RequireAuthenticatedUser().RequireRole("admin")));
        await using var app = builder.Build();
        app.UseAuthentication(); app.UseAuthorization(); app.MapControllers();
        await app.StartAsync();
        var url = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single() + "/VsrEverywhere/Updates";
        using var client = new HttpClient();
        foreach (var method in new[] { HttpMethod.Get, HttpMethod.Post })
        {
            using var anonymous = await client.SendAsync(new HttpRequestMessage(method, url));
            Check(anonymous.StatusCode == HttpStatusCode.Unauthorized, "Anonymous update access must be denied");
            using var request = new HttpRequestMessage(method, url);
            request.Headers.Add("X-Test-Role", "user");
            using var denied = await client.SendAsync(request);
            Check(denied.StatusCode == HttpStatusCode.Forbidden, "Non-administrator update access must be denied");
        }
        Check(fixture.RequestCount == 0 && fixture.InstallCount == 0, "Unauthorized requests must not contact GitHub");
        client.DefaultRequestHeaders.Add("X-Test-Role", "admin");
        var state = await client.GetFromJsonAsync<PluginUpdateStatus>(url);
        Check(state!.CanInstall && fixture.InstallCount == 0, "Administrator check must not install");
        fixture.CorruptHash = true;
        using (var rejected = await client.PostAsync(url, null))
        {
            var error = await rejected.Content.ReadFromJsonAsync<JsonElement>();
            Check(rejected.StatusCode == HttpStatusCode.ServiceUnavailable && error.GetProperty("message").GetString()!.Contains("SHA-256"),
                "Failed verification must return a useful JSON error");
            Check(fixture.InstallCount == 0, "Failed HTTP verification must not install");
        }
        fixture.CorruptHash = false;
        using var install = await client.PostAsync(url, null);
        Check(install.IsSuccessStatusCode && (await install.Content.ReadFromJsonAsync<PluginUpdateStatus>())!.RestartRequired,
            "Administrator installation must return restart state");
        Check(fixture.InstallCount == 1, "Native installation must run once");
    }
}

internal sealed class UpdateFixture : HttpMessageHandler, IHttpClientFactory
{
    private readonly Version _version;
    internal byte[] Package;
    internal bool Draft, Prerelease, CorruptHash, FailInstall;
    internal int RequestCount, InstallCount;
    internal long? DeclaredSize;
    internal string? Url, Digest = "automatic";
    internal InstallationInfo? Installed;
    private readonly IApplicationHost _host;
    private readonly IInstallationManager _installations;

    internal UpdateFixture(Version version)
    {
        _version = version;
        Package = Zip(version);
        _host = DispatchProxy.Create<IApplicationHost, UpdateProxy>();
        ((UpdateProxy)_host).Call = (_, _) => new Version(12, 1, 0, 0);
        _installations = DispatchProxy.Create<IInstallationManager, UpdateProxy>();
        ((UpdateProxy)_installations).Call = (method, arguments) =>
        {
            if (method.Name != "InstallPackage") throw new Exception("Unexpected updater API: " + method.Name);
            Installed = (InstallationInfo)arguments![0]!;
            if (((CancellationToken)arguments[1]!).CanBeCanceled) throw new Exception("A disconnected browser must not cancel native installation");
            InstallCount++;
            return FailInstall ? Task.FromException(new IOException("fixture disk failure")) : Task.CompletedTask;
        };
    }

    internal PluginUpdates Updater() => new(this, _installations, _host);
    public HttpClient CreateClient(string name) => new(this, disposeHandler: false);

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        RequestCount++;
        var label = _version.ToString(3);
        var url = "https://github.com/TomSellecksMustache/VSR-Everywhere/releases/download/v" + label + "/VSR-Everywhere-" + label + "-plugin.zip";
        var digest = Digest == "automatic" ? "sha256:" + (CorruptHash ? new string('0', 64) : Convert.ToHexString(SHA256.HashData(Package))) : Digest;
        var bytes = request.RequestUri!.AbsoluteUri.EndsWith("/releases/latest", StringComparison.Ordinal)
            ? JsonSerializer.SerializeToUtf8Bytes(new
            {
                tag_name = "v" + label, draft = Draft, prerelease = Prerelease,
                assets = new[] { new { name = "VSR-Everywhere-" + label + "-plugin.zip", size = DeclaredSize ?? Package.Length, digest, browser_download_url = Url ?? url } }
            }) : Package;
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) });
    }

    internal static byte[] Zip(Version version, Guid? identity = null, bool runtimeChange = false, bool includeRuntime = true, string target = "10.10.0.0", string? metadata = null)
    {
        using var stream = new MemoryStream();
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
        {
            void Write(string name, string text) { using var writer = new StreamWriter(zip.CreateEntry(name).Open(), new UTF8Encoding(false)); writer.Write(text); }
            Write("meta.json", metadata ?? JsonSerializer.Serialize(new { guid = identity ?? Plugin.PluginId, version = version.ToString(), targetAbi = target }));
            if (includeRuntime)
                foreach (var name in new[] { "windows.json", "linux.json", "linux-requirements.txt" })
                {
                    using var resource = typeof(Plugin).Assembly.GetManifestResourceStream("VsrEverywhere.Update." + name)!;
                    using var reader = new StreamReader(resource);
                    var text = reader.ReadToEnd();
                    if (name.EndsWith(".json", StringComparison.Ordinal))
                    {
                        var json = System.Text.Json.Nodes.JsonNode.Parse(text)!.AsObject();
                        json["version"] = version.ToString(3);
                        if (runtimeChange) json["changedDependency"] = true;
                        text = json.ToJsonString();
                    }
                    Write("update/" + name, text);
                }
        }
        return stream.ToArray();
    }
}

internal class UpdateProxy : DispatchProxy
{
    internal Func<MethodInfo, object?[]?, object?> Call = null!;
    protected override object? Invoke(MethodInfo? method, object?[]? arguments) => Call(method!, arguments);
}

internal sealed class UpdateAuthHandler(IOptionsMonitor<AuthenticationSchemeOptions> options, ILoggerFactory logger, UrlEncoder encoder)
    : AuthenticationHandler<AuthenticationSchemeOptions>(options, logger, encoder)
{
    protected override Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        var role = Request.Headers["X-Test-Role"].ToString();
        if (role.Length == 0) return Task.FromResult(AuthenticateResult.NoResult());
        var principal = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Role, role)], "test"));
        return Task.FromResult(AuthenticateResult.Success(new AuthenticationTicket(principal, "test")));
    }
}
