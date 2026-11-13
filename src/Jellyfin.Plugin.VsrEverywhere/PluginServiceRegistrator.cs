using MediaBrowser.Controller;
using MediaBrowser.Controller.Plugins;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Hosting;

namespace Jellyfin.Plugin.VsrEverywhere;

public sealed class PluginServiceRegistrator : IPluginServiceRegistrator
{
    public void RegisterServices(IServiceCollection services, IServerApplicationHost applicationHost)
    {
        services.AddSingleton<BridgeSessions>();
        services.AddScoped<PlaybackFilter>();
        services.PostConfigure<MvcOptions>(options => options.Filters.Add<PlaybackFilter>());
        services.AddTransient<IStartupFilter, BridgeStartupFilter>();
    }
}
