using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;
using Jellyfin.Plugin.VsrEverywhere.Configuration;

namespace Jellyfin.Plugin.VsrEverywhere;

public sealed class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    public static readonly Guid PluginId = new("7ca129d0-7454-41c7-aad6-55250b1a7b5b");
    public static Plugin? Instance { get; private set; }
    public Plugin(IApplicationPaths paths, IXmlSerializer serializer) : base(paths, serializer) => Instance = this;
    public override string Name => "VSR Everywhere";
    public override Guid Id => PluginId;
    public IEnumerable<PluginPageInfo> GetPages() => new[]
    {
        new PluginPageInfo { Name = Name, EmbeddedResourcePath = "Jellyfin.Plugin.VsrEverywhere.Configuration.configPage.html" }
    };
}
