using System.Reflection;
using Jellyfin.Data.Queries;
using Jellyfin.Plugin.VsrEverywhere;
using MediaBrowser.Controller.Devices;

internal class DeviceManagerProxy : DispatchProxy
{
    internal DeviceQuery? Query;
    internal bool Empty;
    protected override object? Invoke(MethodInfo? method, object?[]? arguments)
    {
        if (method?.Name != "GetDevices") throw new Exception("Unexpected device call");
        Query = (DeviceQuery)arguments![0]!;
        var result = Activator.CreateInstance(method.ReturnType)!;
        var property = method.ReturnType.GetProperty("Items")!;
        var itemType = property.PropertyType.IsArray ? property.PropertyType.GetElementType()! : property.PropertyType.GenericTypeArguments[0];
        var items = Array.CreateInstance(itemType, Empty ? 0 : 1);
        if (!Empty)
        {
            // Only identity fields are read; constructor signatures differ across host versions.
            var device = System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(itemType);
            itemType.GetProperty("DeviceId")!.SetValue(device, "approved-device");
            itemType.GetProperty("AppName")!.SetValue(device, "Test client");
            items.SetValue(device, 0);
        }
        property.SetValue(result, items);
        return result;
    }
}

internal static class DeviceCompatibilityChecks
{
    internal static void Run()
    {
        var paths = DispatchProxy.Create<MediaBrowser.Common.Configuration.IApplicationPaths, HostProxy>();
        var serializer = DispatchProxy.Create<MediaBrowser.Model.Serialization.IXmlSerializer, HostProxy>();
        var plugin = new Plugin(paths, serializer);
        if (plugin.Name != "VSR Everywhere" || !plugin.Configuration.EnableVsr)
            throw new Exception("Plugin construction/defaults failed against the loaded host API");
        foreach (var size in new[] { (1280, 720), (1920, 1080) })
            foreach (var bits in new[] { 8, 10 })
                if (!PlaybackFilter.SupportsInput(new MediaBrowser.Model.Entities.MediaStream { Width = size.Item1, Height = size.Item2, BitDepth = bits, ColorTransfer = "bt709" }))
                    throw new Exception("Supported SDR input was rejected");
        foreach (var video in new[] {
            new MediaBrowser.Model.Entities.MediaStream { Width = 1920, Height = 1080, BitDepth = 10, ColorTransfer = "smpte2084" },
            new MediaBrowser.Model.Entities.MediaStream { Width = 1920, Height = 1080, BitDepth = 10, ColorTransfer = "arib-std-b67" },
            new MediaBrowser.Model.Entities.MediaStream { Width = 3840, Height = 2160, BitDepth = 8 },
            new MediaBrowser.Model.Entities.MediaStream { Width = 1920, Height = 1080, BitDepth = 12 } })
            if (PlaybackFilter.SupportsInput(video)) throw new Exception("HDR or unsupported input was accepted");
        var manager = DispatchProxy.Create<IDeviceManager, DeviceManagerProxy>();
        var proxy = (DeviceManagerProxy)manager;
        var user = Guid.NewGuid();
        var query = new DeviceQuery { AccessToken = "fixture-token", UserId = user };
        var device = PlaybackFilter.RegisteredDevice(manager, query);
        if (device != ("approved-device", "Test client") || proxy.Query?.AccessToken != "fixture-token" || proxy.Query.UserId != user)
            throw new Exception("Registered-token device lookup changed");
        proxy.Empty = true;
        if (PlaybackFilter.RegisteredDevice(manager, query).DeviceId is not null)
            throw new Exception("An unregistered device was accepted");
        Console.WriteLine("PASS: device authorization adapter with the actual loaded Jellyfin API.");
    }
}

internal class HostProxy : DispatchProxy
{
    private readonly string _root = Path.Combine(AppContext.BaseDirectory, "checks", Guid.NewGuid().ToString("N"));
    protected override object? Invoke(MethodInfo? method, object?[]? arguments)
    {
        if (method?.ReturnType == typeof(string)) return _root;
        if (method?.Name.StartsWith("Deserialize", StringComparison.Ordinal) == true)
        {
            var type = method.IsGenericMethod ? method.GetGenericArguments()[0] : arguments?.OfType<Type>().FirstOrDefault();
            if (type is not null) return Activator.CreateInstance(type);
        }
        return null;
    }
}
